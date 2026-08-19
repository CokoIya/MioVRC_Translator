from __future__ import annotations

from collections.abc import Callable
import logging

from PySide6.QtWidgets import QMessageBox, QWidget

from src.utils.credential_validation import MissingCredential
from src.utils.i18n import tr


logger = logging.getLogger(__name__)


def show_missing_credential_prompt(
    parent: QWidget | None,
    missing: MissingCredential,
    *,
    ui_language: str | None,
    open_settings: Callable[[], None] | None = None,
    trigger: str = "unspecified",
    active_only: bool | None = None,
) -> bool:
    """Show an actionable missing-credential prompt without exposing secrets.

    Returns ``True`` when the player chose the Settings action.
    """

    # Log only stable identifiers. Never log the credential value, endpoint,
    # model, player text, or the fully materialized configuration.
    logger.warning(
        "Missing credential prompt shown "
        "(trigger=%s scope=%s provider=%s credential=%s active_only=%s)",
        str(trigger or "unspecified").strip() or "unspecified",
        missing.scope,
        missing.provider_id,
        missing.credential_id,
        "unknown" if active_only is None else bool(active_only),
    )

    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle(tr(ui_language, "api_credential_missing_title"))
    dialog.setText(
        tr(
            ui_language,
            "api_credential_missing_message",
            provider=missing.provider_label,
            credential=missing.credential_label,
        )
    )
    settings_button = None
    if open_settings is not None:
        settings_button = dialog.addButton(
            tr(ui_language, "api_credential_open_settings"),
            QMessageBox.ButtonRole.ActionRole,
        )
    dialog.addButton(tr(ui_language, "cancel"), QMessageBox.ButtonRole.RejectRole)
    dialog.exec()
    try:
        clicked_button = dialog.clickedButton()
    except RuntimeError:
        clicked_button = None
    if settings_button is not None and clicked_button is settings_button:
        open_settings()
        return True
    return False
