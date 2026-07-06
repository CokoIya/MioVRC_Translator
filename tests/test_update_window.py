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


def test_update_window_ready_button_launches_installer_once(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    launched: list[object] = []
    destroyed: list[bool] = []

    monkeypatch.setattr(update_window, "_launch_installer", lambda path: launched.append(path))

    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: destroyed.append(True)

    window._switch_to_ready()
    window._btn_primary.click()

    assert launched == [installer]
    assert destroyed == [True]


def test_windows_update_helper_waits_installs_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "MioTranslator-Setup.exe"
    restart_exe = tmp_path / "MioTranslator.exe"

    helper = update_window._write_windows_update_helper(installer, restart_exe)

    script = helper.read_text(encoding="utf-8")
    assert "Wait-Process -Id" in script
    assert "/VERYSILENT" in script
    assert "/CLOSEAPPLICATIONS" in script
    assert "/RESTARTAPPLICATIONS" in script
    assert "Start-Process -FilePath $restartExe" in script
