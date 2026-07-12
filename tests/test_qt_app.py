from PySide6.QtGui import QFont

from src.ui_qt import font_config
from src.ui_qt.app import _app_font, _cjk_latin_font_paths


def test_app_font_uses_positive_point_size():
    font = _app_font()

    assert font.pointSizeF() > 0
    assert font.family()


def test_app_font_searches_bundled_font_first():
    paths = _cjk_latin_font_paths()

    assert paths[0].as_posix().endswith("assets/fonts/851tegakizatsu.TTF")


def test_app_font_can_use_system_default(monkeypatch):
    monkeypatch.setattr(font_config, "_system_default_font", lambda: QFont("System Test Font"))

    font = _app_font({"ui": {"font_family": "system"}})

    assert font.family() == "System Test Font"
