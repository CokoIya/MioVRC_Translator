from src.ui_qt.styles import build_main_window_styles


def test_main_window_styles_use_theme_name_for_dark_light_glass():
    dark_styles = build_main_window_styles("dark")
    light_styles = build_main_window_styles("light")

    assert "rgba(14, 18, 28, 0.68)" in dark_styles
    assert "rgba(247, 250, 255, 0.66)" in light_styles
    assert "rgba(14, 18, 28, 0.68)" not in light_styles
