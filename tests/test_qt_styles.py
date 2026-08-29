from src.ui_qt.styles import build_main_window_styles, set_cjk_latin_font_family


def test_main_window_styles_use_theme_name_for_dark_light_glass():
    dark_styles = build_main_window_styles("dark")
    light_styles = build_main_window_styles("light")

    assert "rgba(14, 18, 28, 0.68)" in dark_styles
    assert "rgba(247, 250, 255, 0.66)" in light_styles
    assert "rgba(14, 18, 28, 0.68)" not in light_styles
    assert "#actionStrip {\n        background: transparent;\n        border: 0;" in dark_styles


def test_custom_cjk_latin_font_is_first_in_stack():
    set_cjk_latin_font_family("851tegakizatsu")
    try:
        styles = build_main_window_styles("dark")
    finally:
        set_cjk_latin_font_family(None)

    assert 'font-family: "851tegakizatsu", "Segoe UI Variable Text"' in styles


def test_app_stylesheet_themes_non_native_file_dialogs():
    """File pickers are forced non-native, so they need explicit styling.

    Without these rules the browser renders with the default light palette
    inside a dark window.
    """
    from src.ui_qt.styles import build_app_stylesheet
    from src.ui_qt.theme import theme_tokens

    for theme in ("dark", "light"):
        sheet = build_app_stylesheet(theme)
        tokens = theme_tokens(theme)
        assert "QFileDialog QListView" in sheet
        assert "QFileDialog QTreeView" in sheet
        assert "QFileDialog QToolButton" in sheet
        assert "QFileDialog QHeaderView::section" in sheet
        # The list body must use the themed panel color, not the Qt default.
        assert str(tokens["PANEL_BG"]) in sheet
        assert str(tokens["TEXT_PRIMARY"]) in sheet
