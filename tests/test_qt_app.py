from src.ui_qt.app import _app_font, _cjk_latin_font_paths


def test_app_font_uses_positive_point_size():
    font = _app_font()

    assert font.pointSizeF() > 0
    assert font.family()


def test_app_font_searches_bundled_font_first():
    paths = _cjk_latin_font_paths()

    assert paths[0].as_posix().endswith("assets/fonts/851tegakizatsu.TTF")
