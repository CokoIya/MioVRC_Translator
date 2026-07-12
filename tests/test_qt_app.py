from PySide6.QtGui import QFont

from src.ui_qt import app as qt_app
from src.ui_qt import font_config
from src.ui_qt import update_window
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
    assert "Microsoft YaHei UI" in font.families()
    assert "Yu Gothic UI" in font.families()
    assert "Malgun Gothic" in font.families()


def test_run_qt_app_presents_consumed_update_result_after_window_is_shown(
    monkeypatch,
):
    result = update_window.UpdateInstallResult(False, 5)
    events = []

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, _argv):
            events.append("app-created")

        def setApplicationName(self, _name):
            pass

        def setOrganizationName(self, _name):
            pass

        def setWindowIcon(self, _icon):
            pass

        def exec(self):
            events.append("exec")
            return 17

    class FakeWindow:
        def __init__(self, _config):
            events.append("window-created")

        def setWindowIcon(self, _icon):
            pass

        def show(self):
            events.append("shown")

    monkeypatch.setattr(qt_app, "QApplication", FakeApplication)
    monkeypatch.setattr(qt_app, "MainWindow", FakeWindow)
    monkeypatch.setattr(qt_app, "_configure_rendering", lambda: None)
    monkeypatch.setattr(qt_app, "_app_icon", lambda: object())
    monkeypatch.setattr(qt_app, "_apply_style", lambda *_args: None)
    monkeypatch.setattr(qt_app, "install_qt_translations", lambda *_args: None)
    monkeypatch.setattr(qt_app, "apply_application_font", lambda *_args: None)
    monkeypatch.setattr(
        update_window,
        "consume_update_install_result",
        lambda: result,
    )
    monkeypatch.setattr(update_window, "load_deferred_update_info", lambda: None)
    monkeypatch.setattr(
        update_window,
        "show_update_install_result",
        lambda parent, shown_result, language: events.append(
            ("result", parent, shown_result, language)
        ),
    )
    monkeypatch.setattr(
        qt_app.QTimer,
        "singleShot",
        lambda _delay, callback: callback(),
    )

    exit_code = qt_app.run_qt_app({"ui": {"language": "ja"}})

    assert exit_code == 17
    assert events[:3] == ["app-created", "window-created", "shown"]
    result_event = events[3]
    assert result_event[0] == "result"
    assert result_event[2:] == (result, "ja")
    assert events[4] == "exec"
