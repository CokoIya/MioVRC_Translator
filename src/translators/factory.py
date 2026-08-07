from __future__ import annotations

import copy
from collections.abc import Mapping
from threading import Lock
from typing import Callable
import logging

from .anthropic_translator import AnthropicTranslator
from .base import BaseTranslator, TranslationContextStore
from .deepl_translator import DeepLTranslator
from .google_web_translator import GoogleWebTranslator
from .libretranslate_translator import LibreTranslateTranslator
from .microsoft_edge_translator import MicrosoftEdgeTranslator
from .mymemory_translator import MyMemoryTranslator
from .openai_translator import OpenAITranslator
from src.utils.config_manager import is_protected_secret_blob
from src.utils.latency_metrics import (
    merge_translation_metrics,
    translation_metrics_snapshot,
)
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.provider_network import should_bypass_environment_proxies
from src.utils.qwen_endpoints import (
    QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    require_qwen_tokyo_workspace_base_url,
)
from src.utils.ui_config import (
    DEFAULT_BACKEND,
    get_backend_order,
    get_backend_config_value,
    get_backend_label,
    get_backend_spec,
    normalize_backend,
    normalize_backend_region,
)

logger = logging.getLogger(__name__)

OPENAI_COMPATIBLE_BACKENDS = {
    "openai",
    "local_ai",
    "deepseek",
    "zhipu",
    "qianwen",
    "xiaomi",
    "gemini",
    "kimi",
    "hunyuan",
    "xai",
    "mistral",
    "doubao",
    "nvidia",
    "openai_compatible",
    "grok_compatible",
}


class FallbackTranslator(BaseTranslator):
    def __init__(
        self,
        primary: BaseTranslator,
        fallback_factories: list[tuple[str, Callable[[], BaseTranslator]]],
        context_store: TranslationContextStore | None = None,
    ):
        super().__init__(
            context_store=context_store or getattr(primary, "_context_store", None)
        )
        self._primary = primary
        self._fallback_factories = list(fallback_factories)
        self._fallbacks: dict[str, BaseTranslator] = {}
        self._fallbacks_lock = Lock()
        self._last_metrics_translator: BaseTranslator = primary
        self._wall_timeout_s = getattr(primary, "_wall_timeout_s", None)

    def prewarm(self) -> bool:
        """Warm only the configured primary; fallbacks remain lazy."""

        return bool(self._primary.prewarm())

    def translate(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        self._reset_translation_metrics()
        return self._run_with_wall_timeout(
            lambda: self._translate_with_fallbacks(
                text,
                src_lang,
                tgt_lang,
                context_source=context_source,
            ),
            timeout_s=self._wall_timeout_s,
            operation_name="translation fallback chain",
        )

    def _translate_with_fallbacks(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        context_source: str = "default",
    ) -> str:
        request_metrics: dict[str, object] = {}

        def capture(translator: BaseTranslator) -> None:
            merge_translation_metrics(
                request_metrics,
                translation_metrics_snapshot(translator),
            )
            self._record_translation_metrics(**request_metrics)

        try:
            try:
                result = self._primary.translate(
                    text,
                    src_lang,
                    tgt_lang,
                    context_source=context_source,
                )
            finally:
                capture(self._primary)
            self._last_metrics_translator = self._primary
            return result
        except Exception as primary_exc:
            logger.warning(
                "Primary translation backend failed; trying fallbacks (%s)",
                safe_exception_summary(primary_exc),
            )
            for backend, factory in self._fallback_factories:
                if self._pending_requests_retired():
                    raise primary_exc
                try:
                    translator = self._fallback_translator(backend, factory)
                    try:
                        result = translator.translate(
                            text,
                            src_lang,
                            tgt_lang,
                            context_source=context_source,
                        )
                    finally:
                        capture(translator)
                    self._last_metrics_translator = translator
                    return result
                except Exception as fallback_exc:
                    if self._pending_requests_retired():
                        raise primary_exc
                    logger.warning(
                        "Fallback translation backend failed "
                        "(backend=%s error=%s)",
                        backend,
                        safe_exception_summary(fallback_exc),
                    )
            raise primary_exc

    def translation_metrics(self) -> dict[str, object]:
        request_metrics = super().translation_metrics()
        if request_metrics:
            return request_metrics
        metrics = getattr(self._last_metrics_translator, "translation_metrics", None)
        if callable(metrics):
            return dict(metrics())
        return super().translation_metrics()

    def _fallback_translator(
        self,
        backend: str,
        factory: Callable[[], BaseTranslator],
    ) -> BaseTranslator:
        with self._fallbacks_lock:
            existing = self._fallbacks.get(backend)
        if existing is not None:
            if self._pending_requests_retired():
                raise RuntimeError("Translation provider client is closed")
            return existing
        if self._pending_requests_retired():
            raise RuntimeError("Translation provider client is closed")

        candidate = factory()
        selected = candidate
        close_candidate = False
        with self._fallbacks_lock:
            if self._pending_requests_retired():
                close_candidate = True
            else:
                existing = self._fallbacks.get(backend)
                if existing is None:
                    self._fallbacks[backend] = candidate
                else:
                    selected = existing
                    close_candidate = True
        if close_candidate:
            close = getattr(candidate, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug(
                        "Failed to close unused fallback translator resource (%s)",
                        safe_exception_summary(exc),
                    )
        if self._pending_requests_retired():
            raise RuntimeError("Translation provider client is closed")
        return selected

    def rewrite_asr(
        self,
        text: str,
        style: str,
        *,
        language_hint: str = "auto",
        context_source: str = "mic",
    ) -> str:
        self._reset_translation_metrics()
        return self._run_with_wall_timeout(
            lambda: self._rewrite_asr_with_fallbacks(
                text,
                style,
                language_hint=language_hint,
                context_source=context_source,
            ),
            timeout_s=self._wall_timeout_s,
            operation_name="ASR rewrite fallback chain",
        )

    def _rewrite_asr_with_fallbacks(
        self,
        text: str,
        style: str,
        *,
        language_hint: str = "auto",
        context_source: str = "mic",
    ) -> str:
        request_metrics: dict[str, object] = {}

        def capture(translator: BaseTranslator) -> None:
            merge_translation_metrics(
                request_metrics,
                translation_metrics_snapshot(translator),
            )
            self._record_translation_metrics(**request_metrics)

        try:
            try:
                result = self._primary.rewrite_asr(
                    text,
                    style,
                    language_hint=language_hint,
                    context_source=context_source,
                )
            finally:
                capture(self._primary)
            self._last_metrics_translator = self._primary
            return result
        except Exception as primary_exc:
            logger.warning(
                "Primary ASR rewrite backend failed; trying fallbacks (%s)",
                safe_exception_summary(primary_exc),
            )
            for backend, factory in self._fallback_factories:
                if self._pending_requests_retired():
                    raise primary_exc
                try:
                    translator = self._fallback_translator(backend, factory)
                    try:
                        result = translator.rewrite_asr(
                            text,
                            style,
                            language_hint=language_hint,
                            context_source=context_source,
                        )
                    finally:
                        capture(translator)
                    self._last_metrics_translator = translator
                    return result
                except Exception as fallback_exc:
                    if self._pending_requests_retired():
                        raise primary_exc
                    logger.warning(
                        "Fallback ASR rewrite backend failed "
                        "(backend=%s error=%s)",
                        backend,
                        safe_exception_summary(fallback_exc),
                    )
            raise primary_exc

    def close(self) -> None:
        # Mark the wrapper unavailable before taking the child snapshot so an
        # unwinding timed-out chain cannot create an untracked fallback.
        super().close()
        with self._fallbacks_lock:
            translators = [self._primary, *self._fallbacks.values()]
            self._fallbacks.clear()
        closed: list[BaseTranslator] = []
        for translator in translators:
            if any(translator is existing for existing in closed):
                continue
            closed.append(translator)
            close = getattr(translator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug(
                        "Failed to close fallback translator resource (%s)",
                        safe_exception_summary(exc),
                    )

    def cancel_pending_requests(self) -> None:
        """Cancel primary and initialized fallback clients as one unit."""

        self.close()


def _require_text(value: str, label: str, *, max_chars: int = 8192) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is not configured")
    if len(text) > max(int(max_chars), 1):
        raise ValueError(f"{label} is too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError(f"{label} contains invalid control characters")
    if is_protected_secret_blob(text):
        raise ValueError(
            f"{label} is stored as DPAPI ciphertext but could not be decrypted "
            "on this host. Please re-enter the value in Settings."
        )
    return text


def _float_setting(
    value: object, default: object, *, minimum: float, maximum: float
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        try:
            parsed = float(default)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _int_setting(value: object, default: object, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(default)
        except (TypeError, ValueError):
            parsed = minimum
    return max(minimum, min(parsed, maximum))


def _bool_setting(value: object, default: object = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def _backend_cfg(trans_cfg: Mapping[str, object], backend: str) -> Mapping[str, object]:
    backend_cfg = trans_cfg.get(backend, {})
    if isinstance(backend_cfg, Mapping):
        return backend_cfg
    return {}


def _fallback_backends(
    trans_cfg: Mapping[str, object], primary_backend: str
) -> list[str]:
    raw = trans_cfg.get("fallback_backends", ())
    if isinstance(raw, str):
        candidates = [item.strip() for item in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item).strip() for item in raw]
    else:
        candidates = []

    valid_backends = set(get_backend_order())
    seen = {primary_backend}
    result: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            backend = normalize_backend(candidate)
        except Exception:
            continue
        if backend in seen or backend not in valid_backends:
            continue
        seen.add(backend)
        result.append(backend)
    return result


def _create_openai_compatible_translator(
    trans_cfg: Mapping[str, object],
    backend: str,
    context_store: TranslationContextStore | None = None,
) -> OpenAITranslator:
    spec = get_backend_spec(backend)
    backend_cfg = _backend_cfg(trans_cfg, backend)
    label = get_backend_label(backend)
    if backend == "qianwen" and normalize_backend_region(
        backend,
        backend_cfg.get("region"),
    ) == "japan":
        base_url = require_qwen_tokyo_workspace_base_url(
            backend_cfg.get("base_url"),
            endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
            label="Qwen Tokyo workspace API",
        )
    else:
        base_url = get_backend_config_value(trans_cfg, backend, "base_url")
    local_compatible_endpoint = bool(
        backend in {"local_ai", "openai_compatible", "grok_compatible"}
        and should_bypass_environment_proxies(base_url)
    )
    api_key_required = bool(spec.get("api_key_required", True)) and not (
        local_compatible_endpoint
    )
    configured_api_key = str(backend_cfg.get("api_key", "")).strip()
    api_key = configured_api_key
    if api_key_required:
        api_key = _require_text(api_key, f"{label} API Key")
    else:
        if api_key:
            api_key = _require_text(api_key, f"{label} API Key")
        api_key = api_key or "local-ai"
    model = _require_text(
        get_backend_config_value(trans_cfg, backend, "model"),
        f"{label} Model",
        max_chars=512,
    )
    timeout_s = _float_setting(
        backend_cfg.get("timeout_s"),
        spec.get("timeout_s", 15.0),
        minimum=3.0,
        maximum=120.0,
    )
    is_local_ai = backend == "local_ai"
    return OpenAITranslator(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_s=timeout_s,
        max_output_tokens=_int_setting(
            backend_cfg.get("max_output_tokens"),
            spec.get("max_output_tokens", 192),
            minimum=32,
            maximum=4096,
        ),
        max_retries=_int_setting(
            backend_cfg.get("max_retries"),
            spec.get("max_retries", 0),
            minimum=0,
            maximum=3,
        ),
        extra_body=dict(spec.get("extra_body", {})),
        prefer_max_completion_tokens=bool(
            spec.get("prefer_max_completion_tokens", False)
        ),
        context_store=context_store,
        provider_id=backend,
        # Local and compatible servers may legitimately use keyless or
        # credentialed HTTP on an explicit local route. Public HTTP remains
        # prohibited by validate_api_base_url.
        allow_private_http=is_local_ai or local_compatible_endpoint,
        custom_headers=backend_cfg.get("custom_headers", {}),
        streaming=_bool_setting(
            backend_cfg.get("streaming"),
            spec.get("streaming", False),
        ),
        connect_timeout_s=_float_setting(
            backend_cfg.get("connect_timeout_s"),
            timeout_s,
            minimum=0.1,
            maximum=120.0,
        ),
        pool_timeout_s=_float_setting(
            backend_cfg.get("pool_timeout_s"),
            timeout_s,
            minimum=0.1,
            maximum=120.0,
        ),
        read_timeout_s=_float_setting(
            backend_cfg.get("read_timeout_s"),
            timeout_s,
            minimum=0.1,
            maximum=300.0,
        ),
        write_timeout_s=_float_setting(
            backend_cfg.get("write_timeout_s"),
            timeout_s,
            minimum=0.1,
            maximum=300.0,
        ),
        wall_timeout_s=_float_setting(
            backend_cfg.get("wall_timeout_s"),
            timeout_s,
            minimum=0.1,
            maximum=300.0,
        ),
        # The OpenAI SDK requires a non-null key in supported versions. Keep
        # its internal placeholder out of the actual HTTP Authorization
        # header when a local-compatible credential field was left blank.
        omit_placeholder_authorization=(not api_key_required and not configured_api_key),
    )


def _create_translator_for_backend(
    trans_cfg: Mapping[str, object],
    backend: str,
    context_store: TranslationContextStore | None = None,
) -> BaseTranslator:
    if backend == "deepl":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        return DeepLTranslator(
            api_key=_require_text(
                backend_cfg.get("api_key", ""), f"{get_backend_label(backend)} API Key"
            ),
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            timeout_s=_float_setting(
                backend_cfg.get("timeout_s"),
                spec.get("timeout_s", 10.0),
                minimum=3.0,
                maximum=120.0,
            ),
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 1),
                minimum=0,
                maximum=3,
            ),
        )

    if backend == "libretranslate":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        return LibreTranslateTranslator(
            api_key=str(backend_cfg.get("api_key", "") or "").strip(),
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            timeout_s=_float_setting(
                backend_cfg.get("timeout_s"),
                spec.get("timeout_s", 10.0),
                minimum=3.0,
                maximum=120.0,
            ),
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 1),
                minimum=0,
                maximum=3,
            ),
        )

    if backend == "google_web":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        return GoogleWebTranslator(
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            timeout_s=_float_setting(
                backend_cfg.get("timeout_s"),
                spec.get("timeout_s", 8.0),
                minimum=2.0,
                maximum=60.0,
            ),
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 1),
                minimum=0,
                maximum=3,
            ),
        )

    if backend == "microsoft_edge_web":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        return MicrosoftEdgeTranslator(
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            timeout_s=_float_setting(
                backend_cfg.get("timeout_s"),
                spec.get("timeout_s", 8.0),
                minimum=2.0,
                maximum=60.0,
            ),
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 1),
                minimum=0,
                maximum=3,
            ),
        )

    if backend == "mymemory":
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        return MyMemoryTranslator(
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            contact_email=str(backend_cfg.get("contact_email", "") or "").strip(),
            timeout_s=_float_setting(
                backend_cfg.get("timeout_s"),
                spec.get("timeout_s", 8.0),
                minimum=2.0,
                maximum=60.0,
            ),
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 1),
                minimum=0,
                maximum=3,
            ),
        )

    if backend in OPENAI_COMPATIBLE_BACKENDS:
        return _create_openai_compatible_translator(
            trans_cfg,
            backend,
            context_store=context_store,
        )

    if backend in {"anthropic", "anthropic_compatible"}:
        spec = get_backend_spec(backend)
        backend_cfg = _backend_cfg(trans_cfg, backend)
        api_key = _require_text(
            backend_cfg.get("api_key", ""), f"{get_backend_label(backend)} API Key"
        )
        model = _require_text(
            get_backend_config_value(trans_cfg, backend, "model"),
            f"{get_backend_label(backend)} Model",
            max_chars=512,
        )

        timeout_s = _float_setting(
            backend_cfg.get("timeout_s"),
            spec.get("timeout_s", 15.0),
            minimum=3.0,
            maximum=120.0,
        )
        return AnthropicTranslator(
            api_key=api_key,
            model=model,
            base_url=get_backend_config_value(trans_cfg, backend, "base_url"),
            timeout_s=timeout_s,
            max_retries=_int_setting(
                backend_cfg.get("max_retries"),
                spec.get("max_retries", 0),
                minimum=0,
                maximum=3,
            ),
            max_output_tokens=int(spec.get("max_output_tokens", 192)),
            context_store=context_store,
            provider_id=backend,
            custom_headers=backend_cfg.get("custom_headers", {}),
            streaming=_bool_setting(
                backend_cfg.get("streaming"),
                spec.get("streaming", False),
            ),
            connect_timeout_s=_float_setting(
                backend_cfg.get("connect_timeout_s"),
                timeout_s,
                minimum=0.1,
                maximum=120.0,
            ),
            pool_timeout_s=_float_setting(
                backend_cfg.get("pool_timeout_s"),
                timeout_s,
                minimum=0.1,
                maximum=120.0,
            ),
            read_timeout_s=_float_setting(
                backend_cfg.get("read_timeout_s"),
                timeout_s,
                minimum=0.1,
                maximum=300.0,
            ),
            write_timeout_s=_float_setting(
                backend_cfg.get("write_timeout_s"),
                timeout_s,
                minimum=0.1,
                maximum=300.0,
            ),
            wall_timeout_s=_float_setting(
                backend_cfg.get("wall_timeout_s"),
                timeout_s,
                minimum=0.1,
                maximum=300.0,
            ),
        )

    raise ValueError(f"Unknown translation backend: {backend}")


def create_translator(
    config: dict,
    *,
    context_store: TranslationContextStore | None = None,
) -> BaseTranslator:
    trans_cfg = config.get("translation", {})
    if not isinstance(trans_cfg, Mapping):
        trans_cfg = {}
    backend = normalize_backend(trans_cfg.get("backend", DEFAULT_BACKEND))
    primary = _create_translator_for_backend(
        trans_cfg,
        backend,
        context_store=context_store,
    )
    fallback_backends = _fallback_backends(trans_cfg, backend)
    if not fallback_backends:
        return primary

    factories: list[tuple[str, Callable[[], BaseTranslator]]] = []
    for fallback_backend in fallback_backends:
        factories.append(
            (
                fallback_backend,
                lambda fallback_backend=fallback_backend: _create_translator_for_backend(
                    trans_cfg,
                    fallback_backend,
                    context_store=context_store,
                ),
            )
        )
    return FallbackTranslator(
        primary,
        factories,
        context_store=context_store,
    )


def test_translation_connection(config: dict) -> str:
    """Run a minimal primary-provider request using the current Settings values."""

    snapshot = copy.deepcopy(config)
    trans_cfg = snapshot.setdefault("translation", {})
    if not isinstance(trans_cfg, dict):
        raise ValueError("Translation configuration is not configured")
    # A connection test must report the selected provider's own failure rather
    # than succeeding through an unrelated fallback backend.
    trans_cfg["fallback_backends"] = []
    translator = create_translator(snapshot)
    try:
        result = translator.translate(
            "Connection test.",
            "en",
            "ja",
            context_source="connection_test",
        )
        if not str(result or "").strip():
            raise RuntimeError("Translation API returned an empty response")
        return str(result)
    finally:
        close = getattr(translator, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                # Connection-test cleanup must not replace the provider result
                # or its localized authentication/model/endpoint classification.
                logger.warning(
                    "Translation connection-test client cleanup failed: %s",
                    safe_exception_summary(exc),
                )
