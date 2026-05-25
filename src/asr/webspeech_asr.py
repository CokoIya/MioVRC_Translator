from __future__ import annotations

import html
import json
import logging
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import numpy as np

from src.asr.asr_cleaner import clean_asr_text
from src.asr.base import ASRProvider, ProgressCallback
from src.asr.errors import ASRConfigurationError, ASRProviderError
from src.asr.text_corrections import LayeredASRCorrector

logger = logging.getLogger(__name__)

DEFAULT_FINAL_TIMEOUT_SECONDS = 4.0
DEFAULT_PARTIAL_TIMEOUT_SECONDS = 0.2
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 3.0
DEFAULT_SILENCE_TIMEOUT_MS = 800
_LANGUAGE_ALIASES = {
    "ja": "ja-JP",
    "jp": "ja-JP",
    "zh": "zh-CN",
    "cn": "zh-CN",
    "yue": "zh-HK",
    "en": "en-US",
    "ko": "ko-KR",
    "kr": "ko-KR",
    "ru": "ru-RU",
}


def _cfg(config: Mapping[str, object] | None) -> Mapping[str, object]:
    asr_cfg = (config or {}).get("asr", {}) if isinstance(config, Mapping) else {}
    if not isinstance(asr_cfg, Mapping):
        return {}
    provider_cfg = asr_cfg.get("webspeech", {})
    return provider_cfg if isinstance(provider_cfg, Mapping) else {}


def _language_code(language: object) -> str:
    text = str(language or "").strip()
    if not text or text.lower() == "auto":
        return "ja-JP"
    lowered = text.lower().replace("_", "-")
    return _LANGUAGE_ALIASES.get(lowered, text)


class _BridgeState:
    def __init__(self, max_results: int = 16) -> None:
        self.condition = threading.Condition()
        self.connected = False
        self.error = ""
        self.partial_text = ""
        self._last_partial_text = ""
        self._last_final_text = ""
        self.final_results: deque[str] = deque(maxlen=max_results)
        self.last_event_at = 0.0

    def reset(self) -> None:
        with self.condition:
            self.connected = False
            self.error = ""
            self.partial_text = ""
            self._last_partial_text = ""
            self._last_final_text = ""
            self.final_results.clear()
            self.last_event_at = 0.0
            self.condition.notify_all()

    def set_connected(self) -> None:
        with self.condition:
            self.connected = True
            self.error = ""
            self.condition.notify_all()

    def set_disconnected(self) -> None:
        with self.condition:
            self.connected = False
            self.partial_text = ""
            self.condition.notify_all()

    def set_result(self, text: str, is_final: bool) -> None:
        cleaned = clean_asr_text(text)
        with self.condition:
            self.last_event_at = time.monotonic()
            if is_final:
                if cleaned and cleaned != self._last_final_text:
                    self.final_results.append(cleaned)
                    self._last_final_text = cleaned
                self.partial_text = ""
                self._last_partial_text = ""
            else:
                if cleaned == self._last_partial_text:
                    return
                self.partial_text = cleaned
                self._last_partial_text = cleaned
            self.condition.notify_all()

    def set_error(self, message: str) -> None:
        with self.condition:
            self.error = str(message or "").strip()
            self.condition.notify_all()

    def wait_connected(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        with self.condition:
            while not self.connected and not self.error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(timeout=remaining)
            return self.connected

    def pop_final(self, timeout_s: float) -> str:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        with self.condition:
            while not self.final_results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                self.condition.wait(timeout=remaining)
            return self.final_results.popleft()

    def latest_partial(self, timeout_s: float) -> str:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        with self.condition:
            while not self.partial_text:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                self.condition.wait(timeout=remaining)
            return self.partial_text


def _page(
    language: str,
    *,
    continuous: bool = True,
    interim_results: bool = True,
    max_alternatives: int = 1,
    restart_on_end: bool = True,
    silence_timeout_ms: int = DEFAULT_SILENCE_TIMEOUT_MS,
) -> bytes:
    safe_lang = html.escape(language, quote=True)
    options = json.dumps(
        {
            "continuous": bool(continuous),
            "interimResults": bool(interim_results),
            "maxAlternatives": max(1, min(int(max_alternatives or 1), 10)),
            "restartOnEnd": bool(restart_on_end),
            "silenceTimeoutMs": max(0, int(silence_timeout_ms or 0)),
        },
        separators=(",", ":"),
    )
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Mio WebSpeech Bridge</title>
<body style="font-family: sans-serif; max-width: 720px; margin: 32px auto; line-height: 1.5;">
<h1>Mio WebSpeech Bridge</h1>
<p>This page lets the browser Web Speech API send microphone transcripts back to Mio Translator.</p>
<p>Keep this page open while using WebSpeech ASR. Browser microphone permission is required.</p>
<p id="status">Starting...</p>
<script>
const statusEl = document.getElementById('status');
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
function post(path, payload) {{
  return fetch(path, {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload)}}).catch(() => {{}});
}}
if (!SpeechRecognition) {{
  statusEl.textContent = 'This browser does not support Web Speech API.';
  post('/error', {{message: 'Browser does not support Web Speech API'}});
}} else {{
  const options = {options};
  const rec = new SpeechRecognition();
  rec.lang = '{safe_lang}';
  rec.continuous = options.continuous;
  rec.interimResults = options.interimResults;
  rec.maxAlternatives = options.maxAlternatives;
  let stopped = false;
  let silenceTimer = null;
  let lastPartial = '';
  let lastFinal = '';
  function clearSilenceTimer() {{
    if (silenceTimer !== null) {{
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }}
  }}
  function postResult(text, isFinal) {{
    const clean = (text || '').trim();
    if (!clean) return;
    if (isFinal) {{
      clearSilenceTimer();
      if (clean === lastFinal) return;
      lastFinal = clean;
      lastPartial = '';
      post('/result', {{text: clean, final: true}});
      return;
    }}
    if (!options.interimResults || clean === lastPartial) return;
    lastPartial = clean;
    post('/result', {{text: clean, final: false}});
    if (options.silenceTimeoutMs > 0) {{
      clearSilenceTimer();
      silenceTimer = setTimeout(() => {{
        if (lastPartial) postResult(lastPartial, true);
      }}, options.silenceTimeoutMs);
    }}
  }}
  rec.onstart = () => {{
    statusEl.textContent = 'Connected. Listening with browser microphone...';
    post('/ready', {{language: rec.lang}});
  }};
  rec.onerror = (event) => {{
    const error = event.error || 'WebSpeech error';
    statusEl.textContent = 'WebSpeech: ' + error;
    if (error === 'no-speech' || error === 'aborted') return;
    post('/error', {{message: error}});
  }};
  rec.onend = () => {{
    if (lastPartial) postResult(lastPartial, true);
    clearSilenceTimer();
    post('/disconnected', {{}});
    if (!stopped && options.restartOnEnd) setTimeout(() => {{ try {{ rec.start(); }} catch (e) {{}} }}, 400);
  }};
  rec.onresult = (event) => {{
    for (let i = event.resultIndex; i < event.results.length; i++) {{
      const result = event.results[i];
      const text = result[0] && result[0].transcript ? result[0].transcript : '';
      postResult(text, result.isFinal);
    }}
  }};
  try {{ rec.start(); }} catch (e) {{ post('/error', {{message: String(e)}}); }}
  window.addEventListener('beforeunload', () => {{ stopped = true; try {{ rec.stop(); }} catch (e) {{}} }});
}}
</script>
</body>
</html>""".encode("utf-8")


class WebSpeechASRProvider(ASRProvider):
    provider_id = "webspeech"
    display_name = "Web Speech"
    requires_api_key = False
    supports_partial = True

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        corrector: LayeredASRCorrector | None = None,
    ) -> None:
        provider_cfg = _cfg(config)
        self.language = _language_code(provider_cfg.get("language", "ja-JP"))
        if "final_timeout_seconds" in provider_cfg:
            self.final_timeout_seconds = _float_value(
                provider_cfg.get("final_timeout_seconds"),
                DEFAULT_FINAL_TIMEOUT_SECONDS,
            )
        else:
            self.final_timeout_seconds = _float_value(
                provider_cfg.get("silence_timeout_ms", DEFAULT_FINAL_TIMEOUT_SECONDS * 1000),
                DEFAULT_FINAL_TIMEOUT_SECONDS,
                milliseconds=True,
            )
        self.partial_timeout_seconds = _float_value(
            provider_cfg.get("partial_timeout_seconds"),
            DEFAULT_PARTIAL_TIMEOUT_SECONDS,
        )
        self.connection_timeout_seconds = _float_value(
            provider_cfg.get("connection_timeout_seconds"),
            DEFAULT_CONNECTION_TIMEOUT_SECONDS,
        )
        self.continuous = _bool_value(provider_cfg.get("continuous"), True)
        self.interim_results = _bool_value(provider_cfg.get("interim_results"), True)
        self.max_alternatives = _int_range(provider_cfg.get("max_alternatives"), 1, 1, 10)
        self.restart_on_end = _bool_value(provider_cfg.get("restart_on_end"), True)
        self.silence_timeout_ms = _int_range(
            provider_cfg.get("silence_timeout_ms"),
            DEFAULT_SILENCE_TIMEOUT_MS,
            0,
            60000,
        )
        self.auto_open_browser = bool(provider_cfg.get("auto_open_browser", True))
        self.port = _int_value(provider_cfg.get("bridge_port"), 0)
        self._corrector = corrector
        self._state = _BridgeState()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._url = ""
        self._browser_opened = False
        self._warned_audio_ignored = False
        self._lock = threading.RLock()

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        with self._lock:
            if self._server is None:
                self._start_server()
            if self.auto_open_browser and not self._browser_opened:
                webbrowser.open_new_tab(self._url)
                self._browser_opened = True
            if progress_callback is not None:
                progress_callback({"stage": "ready", "message": f"WebSpeech bridge ready: {self._url}"})

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        is_final: bool = True,
    ) -> str:
        del sample_rate
        if np.asarray(audio).size and not self._warned_audio_ignored:
            logger.warning("WebSpeech uses the browser microphone and ignores Python audio buffers")
            self._warned_audio_ignored = True
        if self._server is None:
            self.load()
        if not self._state.connected and not self._state.wait_connected(self.connection_timeout_seconds):
            raise ASRProviderError("WebSpeech bridge is not connected; keep the browser bridge page open")
        if self._state.error:
            raise ASRProviderError(f"WebSpeech bridge error: {self._state.error}")
        text = (
            self._state.pop_final(self.final_timeout_seconds)
            if is_final
            else self._state.latest_partial(self.partial_timeout_seconds)
        )
        text = clean_asr_text(text)
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=_language_code(language) or self.language)
        return text

    def close(self) -> None:
        server = self._server
        self._server = None
        self._browser_opened = False
        self._state.reset()
        if server is not None:
            server.shutdown()
            server.server_close()

    @property
    def is_loaded(self) -> bool:
        return self._server is not None

    def _start_server(self) -> None:
        state = self._state
        state.reset()
        language = self.language
        page_options = {
            "continuous": self.continuous,
            "interim_results": self.interim_results,
            "max_alternatives": self.max_alternatives,
            "restart_on_end": self.restart_on_end,
            "silence_timeout_ms": self.silence_timeout_ms,
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _json_body(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                return payload if isinstance(payload, dict) else {}

            def _send_text(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._send_text(HTTPStatus.OK, _page(language, **page_options), "text/html; charset=utf-8")
                    return
                if parsed.path == "/status":
                    payload = json.dumps(
                        {
                            "connected": state.connected,
                            "error": state.error,
                            "language": language,
                            "options": page_options,
                        }
                    ).encode("utf-8")
                    self._send_text(HTTPStatus.OK, payload, "application/json")
                    return
                self._send_text(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

            def do_POST(self):
                parsed = urlparse(self.path)
                payload = self._json_body()
                if parsed.path == "/ready":
                    state.set_connected()
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/result":
                    state.set_result(str(payload.get("text", "")), bool(payload.get("final", False)))
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/error":
                    state.set_error(str(payload.get("message", "WebSpeech error")))
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/disconnected":
                    state.set_disconnected()
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                self._send_text(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            raise ASRConfigurationError(f"Failed to start WebSpeech bridge: {exc}") from exc
        self._server = server
        host, port = server.server_address[:2]
        self._url = f"http://{host}:{port}/"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="webspeech-bridge")
        self._thread.start()
        logger.info("WebSpeech bridge listening at %s", self._url)


def _float_value(value: object, default: float, *, milliseconds: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed / 1000.0 if milliseconds else parsed


def _int_value(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0 <= parsed <= 65535 else default


def _int_range(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _bool_value(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
