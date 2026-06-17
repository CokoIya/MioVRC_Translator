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
        "APP_BG": "#111018",
        "SHELL_BG": "#17131f",
        "SHELL_BORDER": "#3a3149",
        "HEADER_BG": "rgba(30, 24, 42, 0.94)",
        "HEADER_BORDER": "#4c3f61",
        "HEADER_TEXT": "#fff7ed",
        "HEADER_MUTED": "#c7bdd8",
        "PANEL_BG": "#1d1828",
        "PANEL_ALT_BG": "#251f32",
        "PANEL_RAISED": "#30283e",
        "PANEL_BORDER": "#433750",
        "PANEL_DIVIDER": "#332b40",
        "FIELD_BG": "#171827",
        "FIELD_HOVER": "#2c2638",
        "FIELD_BORDER": "#4b405e",
        "FIELD_FOCUS": "#22d3ee",
        "TEXT_PRIMARY": "#fffaf0",
        "TEXT_SECONDARY": "#d4cae6",
        "TEXT_MUTED": "#9d92ae",
        "TEXT_INVERTED": "#ffffff",
        "ACCENT": "#7c3aed",
        "ACCENT_HOVER": "#8b5cf6",
        "ACCENT_SOFT": "#34245a",
        "ACCENT_BORDER": "#8b5cf6",
        "SUCCESS": "#14b8a6",
        "SUCCESS_SOFT": "#102f2e",
        "SUCCESS_BORDER": "#2dd4bf",
        "WARNING": "#f59e0b",
        "WARNING_SOFT": "#3a2a12",
        "WARNING_BORDER": "#fbbf24",
        "DANGER": "#f43f5e",
        "DANGER_SOFT": "#3b1724",
        "DANGER_BORDER": "#fb7185",
        "SHADOW": "rgba(4, 2, 12, 0.56)",
        "INPUT_TEXT": "#fffaf0",
        "EDITOR_TEXT": "#fffaf0",
        "EDITOR_MUTED": "#a89db8",
        "CHIP_BG": "#2f2547",
        "CHIP_BORDER": "#5b4b74",
        "CHIP_TEXT": "#67e8f9",
        "ICON_MUTED": "#b8adca",
        "ICON_STRONG": "#fff7ed",
        "MAGENTA": "#fb7185",
        "MAGENTA_SOFT": "#3a1e33",
        "MAGENTA_BORDER": "#f472b6",
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
        "APP_BG": "#f4f0ea",
        "SHELL_BG": "#fffaf3",
        "SHELL_BORDER": "#ded4c6",
        "HEADER_BG": "rgba(255, 250, 243, 0.96)",
        "HEADER_BORDER": "#d8cabc",
        "HEADER_TEXT": "#241c2b",
        "HEADER_MUTED": "#6c5f76",
        "PANEL_BG": "#fffdf8",
        "PANEL_ALT_BG": "#f4efe7",
        "PANEL_RAISED": "#eee7dc",
        "PANEL_BORDER": "#ddd2c4",
        "PANEL_DIVIDER": "#e8ded1",
        "FIELD_BG": "#fffaf3",
        "FIELD_HOVER": "#f3eadf",
        "FIELD_BORDER": "#d6c8bb",
        "FIELD_FOCUS": "#14b8a6",
        "TEXT_PRIMARY": "#241c2b",
        "TEXT_SECONDARY": "#5f5367",
        "TEXT_MUTED": "#85788f",
        "TEXT_INVERTED": "#ffffff",
        "ACCENT": "#4f46e5",
        "ACCENT_HOVER": "#4338ca",
        "ACCENT_SOFT": "#e9e7ff",
        "ACCENT_BORDER": "#a5b4fc",
        "SUCCESS": "#0f766e",
        "SUCCESS_SOFT": "#e0f7f3",
        "SUCCESS_BORDER": "#99f6e4",
        "WARNING": "#d97706",
        "WARNING_SOFT": "#fff3cf",
        "WARNING_BORDER": "#fcd34d",
        "DANGER": "#e11d48",
        "DANGER_SOFT": "#ffe4e6",
        "DANGER_BORDER": "#fda4af",
        "SHADOW": "rgba(68, 49, 28, 0.16)",
        "INPUT_TEXT": "#241c2b",
        "EDITOR_TEXT": "#241c2b",
        "EDITOR_MUTED": "#8a7d92",
        "CHIP_BG": "#eef2ff",
        "CHIP_BORDER": "#c7d2fe",
        "CHIP_TEXT": "#4338ca",
        "ICON_MUTED": "#70667a",
        "ICON_STRONG": "#241c2b",
        "MAGENTA": "#e11d48",
        "MAGENTA_SOFT": "#ffe4e6",
        "MAGENTA_BORDER": "#fda4af",
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
