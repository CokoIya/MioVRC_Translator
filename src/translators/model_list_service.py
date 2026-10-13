"""Read a translation provider's model list with the player's credentials.

Builds the configured translator, asks it for the provider's ``/models``
list, and filters the answer with :mod:`src.translators.model_listing`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from src.utils.config_manager import translation_config_with_relay_merged
from src.utils.ui_config import (
    get_backend_config_value,
    model_list_source_key,
    normalize_backend,
    normalize_relay_protocol,
)

from .factory import _create_translator_for_backend
from .model_listing import (
    DEFAULT_MODEL_LIST_TIMEOUT_S,
    ModelListResult,
    backend_supports_model_listing,
    builtin_model_list,
    filter_listed_models,
)

logger = logging.getLogger(__name__)


def fetch_backend_models(
    config: Mapping[str, object],
    *,
    timeout_s: float = DEFAULT_MODEL_LIST_TIMEOUT_S,
) -> ModelListResult:
    """Read the selected backend's model list using the player's credentials.

    ``config`` is a full app config snapshot (as built for a connection test)
    whose ``translation.backend`` names the backend to query. Any failure is
    reported in ``error`` together with the built-in list, so the caller can
    still offer something useful.
    """

    trans_cfg = config.get("translation", {}) if isinstance(config, Mapping) else {}
    if not isinstance(trans_cfg, Mapping):
        trans_cfg = {}
    # A snapshot may still carry the old GPT/Claude/Grok-compatible sections.
    trans_cfg = translation_config_with_relay_merged(dict(trans_cfg))
    backend = normalize_backend(str(trans_cfg.get("backend", "") or ""))
    backend_cfg = trans_cfg.get(backend, {})
    backend_cfg = backend_cfg if isinstance(backend_cfg, Mapping) else {}
    protocol = normalize_relay_protocol(backend_cfg.get("protocol"))
    base_url = get_backend_config_value(trans_cfg, backend, "base_url")
    source_key = model_list_source_key(backend, base_url, protocol)

    if not backend_supports_model_listing(backend):
        return ModelListResult(
            backend=backend,
            models=builtin_model_list(backend, protocol=protocol),
            source="builtin",
            source_key=source_key,
        )

    translator = None
    try:
        translator = _create_translator_for_backend(trans_cfg, backend)
        list_models = getattr(translator, "list_models", None)
        if not callable(list_models):
            raise RuntimeError("This translation provider cannot list models")
        raw_models = list_models(timeout_s=timeout_s)
        models, hidden = filter_listed_models(backend, raw_models, protocol=protocol)
        if not models:
            raise RuntimeError("The provider returned no usable chat models")
        logger.info(
            "Translation model list refreshed (backend=%s models=%d hidden=%d)",
            backend,
            len(models),
            hidden,
        )
        return ModelListResult(
            backend=backend,
            models=models,
            source="remote",
            source_key=source_key,
            hidden_count=hidden,
        )
    except Exception as exc:
        logger.warning(
            "Translation model list refresh failed (backend=%s error_type=%s)",
            backend,
            exc.__class__.__name__,
        )
        return ModelListResult(
            backend=backend,
            models=builtin_model_list(backend, protocol=protocol),
            source="builtin",
            source_key=source_key,
            error=exc,
        )
    finally:
        close = getattr(translator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close model-list translator", exc_info=True)
