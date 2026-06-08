from src.updater.update_checker import UpdateInfo
from src.ui_qt import update_window
from src.ui_qt.update_window import UpdateWindow


def _update_info() -> UpdateInfo:
    return UpdateInfo(
        version="v9.9.9",
        download_url="https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
        notes="test",
        installer_name="app.exe",
        size_bytes=123,
        sha256="a" * 64,
    )


def test_update_window_ready_state_uses_qt_close_not_tk_withdraw(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)

    window = UpdateWindow(None, _update_info(), "zh-CN")
    qtbot.addWidget(window)

    window._switch_to_ready()
    window._btn_secondary.click()

    assert window.isHidden()
