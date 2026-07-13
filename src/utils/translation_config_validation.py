from __future__ import annotations

from collections.abc import Mapping

from src.utils.credential_validation import first_missing_required_credential


def missing_required_translation_api_key(
    config: Mapping[str, object] | None,
    ui_language: str | None = None,
) -> tuple[bool, str]:
    missing = first_missing_required_credential(
        config,
        scopes=("translation",),
        ui_language=ui_language,
        active_only=True,
    )
    if missing is None:
        return False, ""
    return True, missing.provider_label
