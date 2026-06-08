from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

MAIN_THEME_CONFIG_KEY = "main_window_theme"

_THEME_ALIASES = {
    "light": "light",
    "white": "light",
    "day": "light",
    "dark": "dark",
    "black": "dark",
    "night": "dark",
}

THEME_TOKENS: dict[str, dict[str, str | int]] = {
    "dark": {
        "APP_BG": "#07080d",
        "SHELL_BG": "#0b0d14",
        "SHELL_BORDER": "#273041",
        "HEADER_BG": "rgba(13, 15, 24, 0.94)",
        "HEADER_BORDER": "#2d3850",
        "HEADER_TEXT": "#f7fbff",
        "HEADER_MUTED": "#9eaabd",
        "PANEL_BG": "#10131c",
        "PANEL_ALT_BG": "#151925",
        "PANEL_RAISED": "#1b2030",
        "PANEL_BORDER": "#30384c",
        "PANEL_DIVIDER": "#252c3c",
        "FIELD_BG": "#0c111a",
        "FIELD_HOVER": "#171d2a",
        "FIELD_BORDER": "#343d52",
        "FIELD_FOCUS": "#1ed7ff",
        "TEXT_PRIMARY": "#edf7ff",
        "TEXT_SECONDARY": "#b7c2d2",
        "TEXT_MUTED": "#7f8b9f",
        "TEXT_INVERTED": "#ffffff",
        "ACCENT": "#2f6fff",
        "ACCENT_HOVER": "#4f8dff",
        "ACCENT_SOFT": "#13254a",
        "ACCENT_BORDER": "#315fbf",
        "SUCCESS": "#44e28a",
        "SUCCESS_SOFT": "#0d271b",
        "SUCCESS_BORDER": "#23784d",
        "WARNING": "#ffc857",
        "WARNING_SOFT": "#302510",
        "WARNING_BORDER": "#8f6920",
        "DANGER": "#ff5f7a",
        "DANGER_SOFT": "#32151d",
        "DANGER_BORDER": "#914052",
        "SHADOW": "rgba(0, 0, 0, 0.46)",
        "INPUT_TEXT": "#edf7ff",
        "EDITOR_TEXT": "#f1f8ff",
        "EDITOR_MUTED": "#96a7bd",
        "CHIP_BG": "#1a2130",
        "CHIP_BORDER": "#354255",
        "CHIP_TEXT": "#9eeaff",
        "ICON_MUTED": "#a9b6c9",
        "ICON_STRONG": "#f5f8ff",
        "MAGENTA": "#ff4fd8",
        "MAGENTA_SOFT": "#35162f",
        "MAGENTA_BORDER": "#7d3b74",
        "RADIUS_XL": 18,
        "RADIUS_L": 8,
        "RADIUS_M": 8,
        "RADIUS_S": 6,
        "CONTROL_H": 40,
        "HEADER_H": 78,
        "FOOTER_H": 58,
        "SIDE_WIDTH": 304,
    },
    "light": {
        "APP_BG": "#e8edf3",
        "SHELL_BG": "#f7fafc",
        "SHELL_BORDER": "#ccd6e1",
        "HEADER_BG": "rgba(247, 250, 252, 0.96)",
        "HEADER_BORDER": "#d1dbe7",
        "HEADER_TEXT": "#111827",
        "HEADER_MUTED": "#5b6878",
        "PANEL_BG": "#ffffff",
        "PANEL_ALT_BG": "#f3f6fa",
        "PANEL_RAISED": "#eef3f8",
        "PANEL_BORDER": "#d4deea",
        "PANEL_DIVIDER": "#e1e7f0",
        "FIELD_BG": "#f8fbff",
        "FIELD_HOVER": "#eef5fa",
        "FIELD_BORDER": "#cdd8e5",
        "FIELD_FOCUS": "#0098c7",
        "TEXT_PRIMARY": "#111827",
        "TEXT_SECONDARY": "#465569",
        "TEXT_MUTED": "#707d8f",
        "TEXT_INVERTED": "#ffffff",
        "ACCENT": "#0098c7",
        "ACCENT_HOVER": "#007fa7",
        "ACCENT_SOFT": "#e2f7fc",
        "ACCENT_BORDER": "#9eddea",
        "SUCCESS": "#16a34a",
        "SUCCESS_SOFT": "#e9f8ef",
        "SUCCESS_BORDER": "#b8e2c7",
        "WARNING": "#a76500",
        "WARNING_SOFT": "#fff7df",
        "WARNING_BORDER": "#e9cd83",
        "DANGER": "#db2851",
        "DANGER_SOFT": "#fff0f4",
        "DANGER_BORDER": "#efb9c7",
        "SHADOW": "rgba(17, 24, 39, 0.14)",
        "INPUT_TEXT": "#111827",
        "EDITOR_TEXT": "#111827",
        "EDITOR_MUTED": "#728196",
        "CHIP_BG": "#e6f8fc",
        "CHIP_BORDER": "#bee4ef",
        "CHIP_TEXT": "#007fa7",
        "ICON_MUTED": "#667486",
        "ICON_STRONG": "#111827",
        "MAGENTA": "#bf299e",
        "MAGENTA_SOFT": "#fce9f8",
        "MAGENTA_BORDER": "#e6b1da",
        "RADIUS_XL": 18,
        "RADIUS_L": 8,
        "RADIUS_M": 8,
        "RADIUS_S": 6,
        "CONTROL_H": 40,
        "HEADER_H": 78,
        "FOOTER_H": 58,
        "SIDE_WIDTH": 304,
    },
}


def normalize_theme(theme: object) -> str:
    value = str(theme or "").strip().lower()
    return _THEME_ALIASES.get(value, "dark")


def normalize_theme_preference(theme: object) -> str:
    value = str(theme or "").strip().lower()
    if value in {"system", "auto", "follow", "follow-system", "default", ""}:
        return "system"
    return _THEME_ALIASES.get(value, "system")


def system_theme() -> str:
    app = QApplication.instance()
    if app is None:
        return "dark"
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    try:
        window_color = app.palette().color(QPalette.ColorRole.Window)
        return "dark" if window_color.lightness() < 128 else "light"
    except Exception:
        return "dark"


def resolve_theme(theme_preference: object) -> str:
    preference = normalize_theme_preference(theme_preference)
    if preference == "system":
        return system_theme()
    return preference


def theme_preference_from_config(config: dict) -> str:
    ui_cfg = config.get("ui", {}) if isinstance(config, dict) else {}
    if isinstance(ui_cfg, dict):
        return normalize_theme_preference(ui_cfg.get(MAIN_THEME_CONFIG_KEY, "dark"))
    return "dark"


def theme_from_config(config: dict) -> str:
    return resolve_theme(theme_preference_from_config(config))


def theme_tokens(theme: object) -> dict[str, str | int]:
    return THEME_TOKENS.get(normalize_theme(theme), THEME_TOKENS["dark"])


def icon_tint(theme: object, *, strong: bool = False) -> str:
    tokens = theme_tokens(theme)
    key = "ICON_STRONG" if strong else "ICON_MUTED"
    return str(tokens[key])
