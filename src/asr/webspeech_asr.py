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
from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language
from src.utils.ui_config import get_ui_language

logger = logging.getLogger(__name__)

DEFAULT_FINAL_TIMEOUT_SECONDS = 4.0
DEFAULT_PARTIAL_TIMEOUT_SECONDS = 0.2
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 3.0
DEFAULT_SILENCE_TIMEOUT_MS = 800
DEFAULT_STALE_CONNECTION_SECONDS = 8.0
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
        self.last_heartbeat_at = 0.0
        self.capture_enabled = True

    def reset(self) -> None:
        with self.condition:
            self.connected = False
            self.error = ""
            self.partial_text = ""
            self._last_partial_text = ""
            self._last_final_text = ""
            self.final_results.clear()
            self.last_event_at = 0.0
            self.last_heartbeat_at = 0.0
            self.condition.notify_all()

    def set_connected(self) -> None:
        with self.condition:
            self.connected = True
            self.error = ""
            now = time.monotonic()
            self.last_event_at = now
            self.last_heartbeat_at = now
            self.condition.notify_all()

    def set_disconnected(self) -> None:
        with self.condition:
            self.connected = False
            self.partial_text = ""
            self.condition.notify_all()

    def set_heartbeat(self) -> None:
        with self.condition:
            self.last_heartbeat_at = time.monotonic()
            self.condition.notify_all()

    def set_capture_enabled(self, enabled: bool) -> None:
        with self.condition:
            desired = bool(enabled)
            if desired == self.capture_enabled:
                return
            self.capture_enabled = desired
            self.partial_text = ""
            self._last_partial_text = ""
            self._last_final_text = ""
            self.final_results.clear()
            self.condition.notify_all()

    def capture_status(self) -> dict[str, bool]:
        with self.condition:
            return {"capture_enabled": self.capture_enabled}

    def mark_stale_if_needed(self, stale_after_s: float) -> bool:
        if stale_after_s <= 0:
            return False
        now = time.monotonic()
        with self.condition:
            if not self.connected:
                return False
            heartbeat = self.last_heartbeat_at or self.last_event_at
            if heartbeat and (now - heartbeat) <= stale_after_s:
                return False
            self.connected = False
            self.partial_text = ""
            self.condition.notify_all()
            return True

    def set_result(self, text: str, is_final: bool) -> None:
        cleaned = clean_asr_text(text)
        with self.condition:
            if not self.capture_enabled:
                return
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
            while self.capture_enabled and not self.final_results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                self.condition.wait(timeout=remaining)
            if not self.capture_enabled:
                return ""
            return self.final_results.popleft()

    def latest_partial(self, timeout_s: float) -> str:
        deadline = time.monotonic() + max(timeout_s, 0.0)
        with self.condition:
            while self.capture_enabled and not self.partial_text:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                self.condition.wait(timeout=remaining)
            if not self.capture_enabled:
                return ""
            return self.partial_text


def _page(
    language: str,
    *,
    ui_language: str = "en",
    continuous: bool = True,
    interim_results: bool = True,
    max_alternatives: int = 1,
    restart_on_end: bool = True,
    silence_timeout_ms: int = DEFAULT_SILENCE_TIMEOUT_MS,
    capture_enabled: bool = True,
) -> bytes:
    safe_lang = html.escape(language, quote=True)
    normalized_ui_language = normalize_ui_language(ui_language, default="en")
    safe_ui_language = html.escape(normalized_ui_language, quote=True)
    title = html.escape(tr(normalized_ui_language, "webspeech_page_title"))
    intro = html.escape(tr(normalized_ui_language, "webspeech_page_intro"))
    permission = html.escape(tr(normalized_ui_language, "webspeech_page_permission"))
    messages = json.dumps(
        {
            "starting": tr(normalized_ui_language, "webspeech_status_starting"),
            "unsupported": tr(normalized_ui_language, "webspeech_status_unsupported"),
            "paused": tr(normalized_ui_language, "webspeech_status_paused"),
            "resuming": tr(normalized_ui_language, "webspeech_status_resuming"),
            "listening": tr(normalized_ui_language, "webspeech_status_listening"),
            "errorPrefix": tr(normalized_ui_language, "webspeech_error_prefix"),
            "errorDefault": tr(normalized_ui_language, "webspeech_error_default"),
            "errors": {
                "no-speech": tr(normalized_ui_language, "webspeech_error_no_speech"),
                "aborted": tr(normalized_ui_language, "webspeech_error_aborted"),
                "audio-capture": tr(
                    normalized_ui_language, "webspeech_error_audio_capture"
                ),
                "network": tr(normalized_ui_language, "webspeech_error_network"),
                "not-allowed": tr(
                    normalized_ui_language, "webspeech_error_not_allowed"
                ),
                "service-not-allowed": tr(
                    normalized_ui_language, "webspeech_error_service_not_allowed"
                ),
                "bad-grammar": tr(
                    normalized_ui_language, "webspeech_error_bad_grammar"
                ),
                "language-not-supported": tr(
                    normalized_ui_language,
                    "webspeech_error_language_not_supported",
                ),
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    options = json.dumps(
        {
            "continuous": bool(continuous),
            "interimResults": bool(interim_results),
            "maxAlternatives": max(1, min(int(max_alternatives or 1), 10)),
            "restartOnEnd": bool(restart_on_end),
            "silenceTimeoutMs": max(0, int(silence_timeout_ms or 0)),
            "captureEnabled": bool(capture_enabled),
        },
        separators=(",", ":"),
    )
    return f"""<!doctype html>
<html lang="{safe_ui_language}">
<meta charset="utf-8">
<title>{title}</title>
<body style="font-family: sans-serif; max-width: 720px; margin: 32px auto; line-height: 1.5;">
<h1>{title}</h1>
<p>{intro}</p>
<p>{permission}</p>
<p id="status"></p>
<script>
const statusEl = document.getElementById('status');
const messages = {messages};
statusEl.textContent = messages.starting;
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  function post(path, payload) {{
  return fetch(path, {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload)}}).catch(() => {{}});
}}
function beacon(path, payload) {{
  const body = JSON.stringify(payload || {{}});
  if (navigator.sendBeacon) {{
    try {{ navigator.sendBeacon(path, new Blob([body], {{type: 'application/json'}})); return; }} catch (e) {{}}
  }}
  post(path, payload || {{}});
}}
if (!SpeechRecognition) {{
  statusEl.textContent = messages.unsupported;
  post('/error', {{message: messages.unsupported}});
}} else {{
  const options = {options};
  const rec = new SpeechRecognition();
  rec.lang = '{safe_lang}';
  rec.continuous = options.continuous;
  rec.interimResults = options.interimResults;
  rec.maxAlternatives = options.maxAlternatives;
  let stopped = false;
  let recognitionRunning = false;
  let captureEnabled = options.captureEnabled;
  let silenceTimer = null;
  let lastPartial = '';
  let lastFinal = '';
  let restartAttempts = 0;
  const heartbeatTimer = setInterval(() => post('/heartbeat', {{ts: Date.now()}}), 2000);
  const captureTimer = setInterval(pollCaptureState, 250);
  function clearSilenceTimer() {{
    if (silenceTimer !== null) {{
      clearTimeout(silenceTimer);
      silenceTimer = null;
    }}
  }}
  function postResult(text, isFinal) {{
    if (!captureEnabled) return;
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
  function startRecognition() {{
    if (stopped || !captureEnabled || recognitionRunning) return;
    try {{ rec.start(); }} catch (e) {{
      statusEl.textContent = messages.errorPrefix + messages.errorDefault;
      post('/error', {{message: messages.errorDefault}});
    }}
  }}
  function setCaptureEnabled(enabled) {{
    const next = Boolean(enabled);
    if (captureEnabled === next) return;
    captureEnabled = next;
    clearSilenceTimer();
    lastPartial = '';
    lastFinal = '';
    if (!captureEnabled) {{
      statusEl.textContent = messages.paused;
      try {{ rec.abort(); }} catch (e) {{}}
      return;
    }}
    statusEl.textContent = messages.resuming;
    startRecognition();
  }}
  async function pollCaptureState() {{
    try {{
      const response = await fetch('/capture-state', {{cache: 'no-store'}});
      if (!response.ok) return;
      const control = await response.json();
      setCaptureEnabled(control.capture_enabled !== false);
    }} catch (e) {{}}
  }}
  rec.onstart = () => {{
    recognitionRunning = true;
    restartAttempts = 0;
    if (!captureEnabled) {{
      try {{ rec.abort(); }} catch (e) {{}}
      return;
    }}
    statusEl.textContent = messages.listening;
    post('/ready', {{language: rec.lang}});
  }};
  rec.onerror = (event) => {{
    const errorCode = event.error || '';
    const error = messages.errors[errorCode] || messages.errorDefault;
    statusEl.textContent = messages.errorPrefix + error;
    if (errorCode === 'no-speech' || errorCode === 'aborted') return;
    post('/error', {{message: error}});
  }};
  rec.onend = () => {{
    recognitionRunning = false;
    if (captureEnabled && lastPartial) postResult(lastPartial, true);
    clearSilenceTimer();
    post('/disconnected', {{}});
    if (!stopped && captureEnabled && options.restartOnEnd) {{
      const delay = Math.min(400 + restartAttempts * 250, 2500);
      restartAttempts += 1;
      setTimeout(() => {{
        if (!captureEnabled || stopped) return;
        startRecognition();
      }}, delay);
    }}
  }};
  rec.onresult = (event) => {{
    if (!captureEnabled) return;
    for (let i = event.resultIndex; i < event.results.length; i++) {{
      const result = event.results[i];
      const text = result[0] && result[0].transcript ? result[0].transcript : '';
      postResult(text, result.isFinal);
    }}
  }};
  if (captureEnabled) {{
    startRecognition();
  }} else {{
    statusEl.textContent = messages.paused;
  }}
  window.addEventListener('beforeunload', () => {{
    stopped = true;
    clearInterval(heartbeatTimer);
    clearInterval(captureTimer);
    beacon('/disconnected', {{}});
    try {{ rec.stop(); }} catch (e) {{}}
  }});
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
        self.ui_language = normalize_ui_language(
            get_ui_language(dict(config or {}))
            if isinstance(config, Mapping)
            else get_ui_language({}),
        )
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
        self.embedded_browser = _bool_value(provider_cfg.get("embedded_browser"), True)
        self.stale_connection_seconds = _float_range(
            provider_cfg.get("stale_connection_seconds"),
            DEFAULT_STALE_CONNECTION_SECONDS,
            minimum=2.0,
            maximum=60.0,
        )
        self.port = _int_value(provider_cfg.get("bridge_port"), 0)
        self._corrector = corrector
        self._state = _BridgeState()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._url = ""
        self._browser_opened = False
        self._browser_opener = None
        self._browser_handle = None
        self._warned_audio_ignored = False
        self._lock = threading.RLock()
        self._closed = False

    def set_browser_opener(self, opener) -> None:
        """Install an app-owned browser opener.

        The Qt main window uses this to create an embedded WebEngine view on
        the UI thread. Tests and non-Qt hosts can leave it unset, in which case
        the provider falls back to the system browser.
        """
        self._browser_opener = opener

    def update_language(self, ui_language: str) -> None:
        """Apply a new UI locale without restarting the ASR provider.

        The bridge HTTP handler deliberately reads ``self.ui_language`` for
        every request, so reloading an already-open bridge page is enough to
        update both its visible copy and any later browser-side error text.
        """

        normalized = normalize_ui_language(ui_language, default="en")
        with self._lock:
            self.ui_language = normalized
            handle = self._browser_handle
            url = self._url
        if handle is None:
            return
        try:
            update_language = getattr(handle, "update_language", None)
            if callable(update_language):
                update_language(normalized)
                return
            load_url = getattr(handle, "load_url", None)
            if callable(load_url) and url:
                load_url(url)
                return
            reload_page = getattr(handle, "reload", None)
            if callable(reload_page):
                reload_page()
        except Exception:
            logger.debug(
                "Failed to refresh the WebSpeech bridge language",
                exc_info=True,
            )

    def load(self, progress_callback: Optional[ProgressCallback] = None) -> None:
        with self._lock:
            if self._closed:
                raise ASRConfigurationError("WebSpeech provider is closed")
            if self._server is None:
                self._start_server()
            if self.auto_open_browser and not self._browser_opened:
                self._open_bridge_page()
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "ready",
                        "message": tr(
                            self.ui_language,
                            "webspeech_progress_ready",
                            url=self._url,
                        ),
                    }
                )

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
        self._state.mark_stale_if_needed(self.stale_connection_seconds)
        if not self._state.connected and not self._state.wait_connected(self.connection_timeout_seconds):
            raise ASRProviderError(tr(self.ui_language, "webspeech_not_connected"))
        if self._state.error:
            raise ASRProviderError(
                tr(self.ui_language, "webspeech_bridge_error", error=self._state.error)
            )
        text = (
            self._state.pop_final(self.final_timeout_seconds)
            if is_final
            else self._state.latest_partial(self.partial_timeout_seconds)
        )
        text = clean_asr_text(text)
        if self._state.error:
            raise ASRProviderError(
                tr(self.ui_language, "webspeech_bridge_error", error=self._state.error)
            )
        if text and self._corrector is not None:
            text = self._corrector.apply(text, language=_language_code(language) or self.language)
        return text

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            server = self._server
            thread = self._thread
            handle = self._browser_handle
            self._server = None
            self._thread = None
            self._url = ""
            self._browser_opened = False
            self._browser_handle = None
            self._browser_opener = None
            self._corrector = None
        self._state.reset()
        if handle is not None:
            try:
                close = getattr(handle, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug("Failed to close embedded WebSpeech bridge", exc_info=True)
        if server is not None:
            try:
                if (
                    thread is not None
                    and thread is not threading.current_thread()
                    and thread.is_alive()
                ):
                    server.shutdown()
            finally:
                server.server_close()
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("WebSpeech bridge thread did not stop in time")

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return not self._closed and self._server is not None

    def set_capture_enabled(self, enabled: bool) -> None:
        """Pause/resume browser-owned microphone capture and discard stale text."""

        self._state.set_capture_enabled(enabled)

    def _start_server(self) -> None:
        state = self._state
        state.reset()
        language = self.language
        provider = self
        page_options = {
            "continuous": self.continuous,
            "interim_results": self.interim_results,
            "max_alternatives": self.max_alternatives,
            "restart_on_end": self.restart_on_end,
            "silence_timeout_ms": self.silence_timeout_ms,
            "capture_enabled": state.capture_status()["capture_enabled"],
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
                    runtime_options = dict(page_options)
                    runtime_options["capture_enabled"] = state.capture_status()[
                        "capture_enabled"
                    ]
                    self._send_text(
                        HTTPStatus.OK,
                        _page(
                            language,
                            ui_language=provider.ui_language,
                            **runtime_options,
                        ),
                        "text/html; charset=utf-8",
                    )
                    return
                if parsed.path == "/status":
                    payload = json.dumps(
                        {
                            "connected": state.connected,
                            "error": state.error,
                            "language": language,
                            "ui_language": provider.ui_language,
                            "options": page_options,
                        }
                    ).encode("utf-8")
                    self._send_text(HTTPStatus.OK, payload, "application/json")
                    return
                if parsed.path == "/ping":
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/capture-state":
                    payload = json.dumps(state.capture_status()).encode("utf-8")
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
                if parsed.path == "/heartbeat":
                    state.set_heartbeat()
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/result":
                    state.set_result(str(payload.get("text", "")), bool(payload.get("final", False)))
                    self._send_text(HTTPStatus.OK, b"{}", "application/json")
                    return
                if parsed.path == "/error":
                    state.set_error(
                        str(
                            payload.get("message")
                            or tr(
                                provider.ui_language,
                                "webspeech_error_default",
                            )
                        )
                    )
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
            logger.error("Failed to start WebSpeech bridge: %s", exc)
            raise ASRConfigurationError(
                tr(self.ui_language, "webspeech_start_failed")
            ) from exc
        self._server = server
        host, port = server.server_address[:2]
        self._url = f"http://{host}:{port}/"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="webspeech-bridge")
        try:
            self._thread.start()
        except Exception:
            self._server = None
            self._thread = None
            self._url = ""
            server.server_close()
            raise
        logger.info("WebSpeech bridge listening at %s", self._url)

    def _open_bridge_page(self) -> None:
        opener = self._browser_opener if self.embedded_browser else None
        if opener is not None:
            try:
                self._browser_handle = opener(self._url)
                self._browser_opened = True
                return
            except Exception:
                logger.debug("Embedded WebSpeech bridge opener failed", exc_info=True)
                self._browser_handle = None
        webbrowser.open_new_tab(self._url)
        self._browser_opened = True


def _float_value(value: object, default: float, *, milliseconds: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed / 1000.0 if milliseconds else parsed


def _float_range(
    value: object,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    parsed = _float_value(value, default)
    return max(minimum, min(parsed, maximum))


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
