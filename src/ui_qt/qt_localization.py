from __future__ import annotations

import logging

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication, QFileDialog

from src.utils.localization import normalize_ui_language, translate_language_catalog

logger = logging.getLogger(__name__)

_QT_LOCALES = {
    "zh-CN": "zh_CN",
    "en": "en",
    "ja": "ja",
    "ru": "ru",
    "ko": "ko",
}
_installed_app: QApplication | None = None
_installed_translators: list[QTranslator] = []

QT_FILE_DIALOG_TEXTS = {
    "zh-CN": {
        "look_in": "位置：",
        "file_name": "文件名：",
        "file_type": "文件类型：",
        "open": "打开",
        "save": "保存",
        "select": "选择",
        "cancel": "取消",
    },
    "en": {
        "look_in": "Look in:",
        "file_name": "File name:",
        "file_type": "Files of type:",
        "open": "Open",
        "save": "Save",
        "select": "Select",
        "cancel": "Cancel",
    },
    "ja": {
        "look_in": "場所：",
        "file_name": "ファイル名：",
        "file_type": "ファイルの種類：",
        "open": "開く",
        "save": "保存",
        "select": "選択",
        "cancel": "キャンセル",
    },
    "ru": {
        "look_in": "Папка:",
        "file_name": "Имя файла:",
        "file_type": "Тип файла:",
        "open": "Открыть",
        "save": "Сохранить",
        "select": "Выбрать",
        "cancel": "Отмена",
    },
    "ko": {
        "look_in": "위치:",
        "file_name": "파일 이름:",
        "file_type": "파일 형식:",
        "open": "열기",
        "save": "저장",
        "select": "선택",
        "cancel": "취소",
    },
}


def install_qt_translations(
    app: QApplication | None,
    language: object,
) -> str:
    """Apply Qt's own widget/dialog translations for the selected app locale."""

    global _installed_app, _installed_translators

    normalized = normalize_ui_language(language)
    locale_name = _QT_LOCALES[normalized]
    QLocale.setDefault(QLocale(locale_name))
    if app is None:
        return normalized

    if _installed_app is not None:
        for translator in _installed_translators:
            try:
                _installed_app.removeTranslator(translator)
            except RuntimeError:
                pass
    _installed_app = app
    _installed_translators = []

    translations_path = QLibraryInfo.path(
        QLibraryInfo.LibraryPath.TranslationsPath
    )
    for catalog_name in (f"qtbase_{locale_name}", f"qt_{locale_name}"):
        translator = QTranslator(app)
        if translator.load(catalog_name, translations_path):
            app.installTranslator(translator)
            _installed_translators.append(translator)
        else:
            logger.debug(
                "Qt translation catalog is unavailable (catalog=%s path=%s)",
                catalog_name,
                translations_path,
            )
    return normalized


def configure_file_dialog(
    dialog: QFileDialog,
    language: object,
    *,
    title: str,
    accept_action: str = "open",
) -> QFileDialog:
    """Make a file dialog follow the app locale instead of the OS locale."""

    normalized = normalize_ui_language(language)
    action = accept_action if accept_action in {"open", "save", "select"} else "open"
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setWindowTitle(str(title))
    labels = {
        QFileDialog.DialogLabel.LookIn: "look_in",
        QFileDialog.DialogLabel.FileName: "file_name",
        QFileDialog.DialogLabel.FileType: "file_type",
        QFileDialog.DialogLabel.Accept: action,
        QFileDialog.DialogLabel.Reject: "cancel",
    }
    for dialog_label, key in labels.items():
        dialog.setLabelText(
            dialog_label,
            translate_language_catalog(QT_FILE_DIALOG_TEXTS, normalized, key),
        )
    return dialog
