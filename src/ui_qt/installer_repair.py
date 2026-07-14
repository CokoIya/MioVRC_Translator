"""Shared full-installer repair helpers for bundled runtime components."""
from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from src.updater.update_checker import UpdateInfo
from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language

MIO_RELEASE_DOWNLOAD_URL = "https://miovrc.com/#download"
_SUPPORTED_REPAIR_NOTE_LANGUAGES = ("zh-CN", "en", "ja", "ru", "ko")
logger = logging.getLogger(__name__)


def build_runtime_repair_update_info(
    update_info: UpdateInfo,
    ui_lang: str,
    detail: str = "",
) -> UpdateInfo:
    issue = str(detail or "").strip() or tr(ui_lang, "xtts_runtime_unknown_issue")
    current_language = normalize_ui_language(ui_lang)
    localized_notes = dict(update_info.localized_notes or {})
    for language in _SUPPORTED_REPAIR_NOTE_LANGUAGES:
        key = language.strip().lower().replace("_", "-")
        localized_issue = (
            issue
            if language == current_language
            else tr(language, "xtts_runtime_unknown_issue")
        )
        localized_notes[key] = tr(
            language,
            "xtts_runtime_repair_notes",
            detail=localized_issue,
        )
    return replace(
        update_info,
        notes=localized_notes["en"],
        localized_notes=localized_notes,
        flow="repair",
    )


def show_installer_download_fallback(
    parent: QWidget | None,
    ui_lang: str,
    *,
    detail: str = "",
    error: str = "",
) -> None:
    issue = str(detail or "").strip() or tr(ui_lang, "xtts_runtime_unknown_issue")
    fetch_error = str(error or "").strip()
    if fetch_error:
        logger.warning("Unable to fetch full installer information: %s", fetch_error)
        message = tr(ui_lang, "xtts_runtime_repair_fetch_failed", detail=issue)
    else:
        message = tr(ui_lang, "xtts_runtime_repair_manual", detail=issue)

    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle(tr(ui_lang, "xtts_runtime_repair_title"))
    dialog.setText(message)
    download_btn = dialog.addButton(
        tr(ui_lang, "xtts_runtime_download_installer"),
        QMessageBox.ButtonRole.AcceptRole,
    )
    dialog.addButton(tr(ui_lang, "xtts_download_close"), QMessageBox.ButtonRole.RejectRole)
    dialog.exec()
    if dialog.clickedButton() is download_btn:
        QDesktopServices.openUrl(QUrl(MIO_RELEASE_DOWNLOAD_URL))
