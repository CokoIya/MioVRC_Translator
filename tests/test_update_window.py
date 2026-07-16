from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.updater.installer_signature import (
    INSTALLER_SIGNATURE_ALGORITHM,
    sign_installer_metadata,
)
from src.updater.manifest_signature import public_key_from_seed
from src.updater.update_checker import UpdateInfo
from src.ui_qt import update_window
from src.ui_qt.update_window import UpdateWindow


TEST_SEED = "61" * 32
TEST_KEY_ID = "test-installer-key"
TEST_PUBLIC_KEYS = ((TEST_KEY_ID, public_key_from_seed(TEST_SEED)),)


@pytest.fixture(autouse=True)
def _trusted_installer_public_key(monkeypatch, tmp_path):
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )
    retained_dir = tmp_path / "retained-updates"
    retained_dir.mkdir()
    monkeypatch.setattr(update_window, "_retained_update_dir", lambda: retained_dir)


def _installer_signature(digest: str, size: int) -> str:
    return sign_installer_metadata(
        TEST_SEED,
        sha256=digest,
        size_bytes=size,
    )


def _update_info() -> UpdateInfo:
    digest = "a" * 64
    size = 123
    return UpdateInfo(
        version="v9.9.9",
        download_url="https://github.com/CokoIya/MioVRC_Translator/releases/download/v9.9.9/app.exe",
        notes="test",
        installer_name="app.exe",
        size_bytes=size,
        sha256=digest,
        installer_signature=_installer_signature(digest, size),
        installer_signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
        installer_signature_key_id=TEST_KEY_ID,
    )


def _update_info_for_payload(payload: bytes) -> UpdateInfo:
    digest = hashlib.sha256(payload).hexdigest()
    size = len(payload)
    return replace(
        _update_info(),
        size_bytes=size,
        sha256=digest,
        installer_signature=_installer_signature(digest, size),
    )


def test_update_window_ready_state_uses_qt_close_not_tk_withdraw(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)

    window = UpdateWindow(None, _update_info(), "zh-CN")
    qtbot.addWidget(window)

    assert window.testAttribute(
        update_window.Qt.WidgetAttribute.WA_DeleteOnClose
    )

    window._switch_to_ready()
    window._btn_secondary.click()

    assert window.isHidden()


def test_closing_active_download_minimizes_without_destroying_worker_owner(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)
    window.show()
    window._downloading = True
    window._download_done = False

    window.close()

    assert window.isHidden()
    assert window._minimized is True
    assert window._destroying is False

    window._downloading = False
    window.close()
    assert window._destroying is True


def test_update_shutdown_closes_response_and_joins_download_thread(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)

    class Response:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class Thread:
        def __init__(self) -> None:
            self.alive = True
            self.join_calls: list[float] = []

        def join(self, timeout=None) -> None:
            self.join_calls.append(timeout)
            self.alive = False

        def is_alive(self) -> bool:
            return self.alive

    response = Response()
    thread = Thread()
    window._active_download_response = response
    window._download_thread = thread
    window._downloading = True

    window.shutdown()

    assert window._download_cancel_event.is_set()
    assert response.close_calls == 1
    assert thread.join_calls == [1.0]
    assert window._download_thread is None
    assert window._downloading is False
    assert window._destroying is True


def test_update_window_uses_requested_english_update_actions(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)

    assert window._btn_primary.text() == "Start Update"

    window._switch_to_ready()

    assert window._btn_primary.text() == "Install Now"
    assert window._btn_secondary.text() == "Install Later"


def test_install_later_retains_and_restores_verified_installer(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"verified deferred installer"
    info = _update_info_for_payload(payload)

    first = UpdateWindow(None, info, "en")
    qtbot.addWidget(first)
    first._final_path.write_bytes(payload)
    first._installer_path = first._final_path
    first._download_done = True
    first._switch_to_ready()

    first._btn_secondary.click()

    assert first.isHidden()
    assert first._final_path.read_bytes() == payload

    reopened = UpdateWindow(None, info, "ja")
    qtbot.addWidget(reopened)

    assert reopened._view_state == "ready"
    assert reopened._download_done is True
    assert reopened._installer_path == reopened._final_path
    assert reopened._btn_primary.text() == update_window.tr("ja", "update_install_now")
    assert reopened._btn_secondary.text() == update_window.tr(
        "ja", "update_install_later"
    )
    assert reopened._sub_label.text() == update_window.tr("ja", "update_retained_note")


def test_deferred_update_sidecar_round_trips_without_reusing_update_info(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"offline deferred installer"
    original = replace(
        _update_info_for_payload(payload),
        localized_notes={"en": "Offline update", "ja": "オフライン更新"},
    )
    installer = update_window._retained_installer_path(original)
    installer.write_bytes(payload)

    assert update_window.persist_deferred_update(original, installer) is True

    restored = update_window.load_deferred_update_info()

    assert restored is not None
    assert restored is not original
    assert restored == original
    assert update_window._deferred_update_metadata_path().is_file()


def test_stale_deferred_metadata_is_rejected_and_private_snapshots_are_cleaned(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"stale deferred installer"
    info = _update_info_for_payload(payload)
    installer = update_window._retained_installer_path(info)
    installer.write_bytes(payload)
    update_window.persist_deferred_update(info, installer)
    metadata_path = update_window._deferred_update_metadata_path()
    payload_data = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload_data["update"]["version"] = update_window.APP_VERSION
    metadata_path.write_text(
        json.dumps(payload_data, ensure_ascii=False),
        encoding="utf-8",
    )

    assert update_window.load_deferred_update_info() is None
    assert not metadata_path.exists()
    assert not installer.exists()


def test_malformed_deferred_metadata_cleans_managed_orphan_installer(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    orphan = update_window._retained_update_dir() / f"mio-update-{'b' * 64}.exe"
    orphan.write_bytes(b"orphan")
    metadata_path = update_window._deferred_update_metadata_path()
    metadata_path.write_text("{not-json", encoding="utf-8")

    assert update_window.load_deferred_update_info() is None
    assert not metadata_path.exists()
    assert not orphan.exists()


def test_persisting_new_pending_update_cleans_older_managed_installer(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    older = update_window._retained_update_dir() / f"mio-update-{'b' * 64}.exe"
    older.write_bytes(b"older installer")
    payload = b"new authenticated installer"
    info = _update_info_for_payload(payload)
    current = update_window._retained_installer_path(info)
    current.write_bytes(payload)

    update_window.persist_deferred_update(info, current)

    assert current.is_file()
    assert not older.exists()


def test_orphan_cleanup_refuses_managed_hardlink_snapshot(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    sensitive = tmp_path / "sensitive.exe"
    sensitive.write_bytes(b"preserve")
    unsafe_orphan = (
        update_window._retained_update_dir() / f"mio-update-{'c' * 64}.exe"
    )
    try:
        os.link(sensitive, unsafe_orphan)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable")
    payload = b"new safe installer"
    info = _update_info_for_payload(payload)
    current = update_window._retained_installer_path(info)
    current.write_bytes(payload)

    update_window.persist_deferred_update(info, current)

    assert sensitive.read_bytes() == b"preserve"
    assert unsafe_orphan.read_bytes() == b"preserve"


def test_repair_flow_is_not_persisted_as_pending_application_update(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"repair installer"
    info = replace(_update_info_for_payload(payload), flow="repair")
    installer = update_window._retained_installer_path(info)
    installer.write_bytes(payload)

    assert update_window.persist_deferred_update(info, installer) is False
    assert not update_window._deferred_update_metadata_path().exists()
    assert installer.is_file()


def test_invalid_repair_installer_does_not_clear_unrelated_pending_update(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    update_payload = b"normal pending update"
    update_info = _update_info_for_payload(update_payload)
    pending_installer = update_window._retained_installer_path(update_info)
    pending_installer.write_bytes(update_payload)
    update_window.persist_deferred_update(update_info, pending_installer)

    repair_payload = b"repair payload"
    repair_info = replace(_update_info_for_payload(repair_payload), flow="repair")
    repair_installer = update_window._retained_installer_path(repair_info)
    repair_installer.write_bytes(b"x" * len(repair_payload))

    repair_window = UpdateWindow(None, repair_info, "en")
    qtbot.addWidget(repair_window)

    assert not repair_installer.exists()
    assert pending_installer.is_file()
    assert update_window._deferred_update_metadata_path().is_file()
    assert update_window.load_deferred_update_info() == update_info


def test_deferred_sidecar_does_not_bypass_full_installer_hash_verification(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"authenticated pending installer"
    info = _update_info_for_payload(payload)
    installer = update_window._retained_installer_path(info)
    installer.write_bytes(payload)
    update_window.persist_deferred_update(info, installer)
    installer.write_bytes(b"x" * len(payload))

    restored_info = update_window.load_deferred_update_info()

    assert restored_info is not None
    window = UpdateWindow(None, restored_info, "en")
    qtbot.addWidget(window)
    assert window._view_state == "initial"
    assert not installer.exists()
    assert not update_window._deferred_update_metadata_path().exists()


def test_qt_app_restores_deferred_update_from_disk_without_network_or_memory(
    tmp_path,
    monkeypatch,
):
    from src.ui_qt import app as qt_app

    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"offline startup installer"
    original = _update_info_for_payload(payload)
    installer = update_window._retained_installer_path(original)
    installer.write_bytes(payload)
    update_window.persist_deferred_update(original, installer)
    published = []

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, _argv):
            pass

        def setApplicationName(self, _name):
            pass

        def setOrganizationName(self, _name):
            pass

        def setWindowIcon(self, _icon):
            pass

        def exec(self):
            return 0

    class FakeMainWindow:
        def __init__(self, _config):
            pass

        def setWindowIcon(self, _icon):
            pass

        def _handle_update_available(self, info):
            published.append(info)

        def show(self):
            pass

    monkeypatch.setattr(qt_app, "QApplication", FakeApplication)
    monkeypatch.setattr(qt_app, "MainWindow", FakeMainWindow)
    monkeypatch.setattr(qt_app, "_configure_rendering", lambda: None)
    monkeypatch.setattr(qt_app, "_app_icon", lambda: object())
    monkeypatch.setattr(qt_app, "_apply_style", lambda *_args: None)
    monkeypatch.setattr(qt_app, "install_qt_translations", lambda *_args: None)
    monkeypatch.setattr(qt_app, "apply_application_font", lambda *_args: None)
    monkeypatch.setattr(
        update_window,
        "consume_update_install_result",
        lambda: None,
    )

    assert qt_app.run_qt_app({"ui": {"language": "en"}}) == 0
    assert len(published) == 1
    assert published[0] == original
    assert published[0] is not original


def test_update_window_allows_layout_growth_for_long_translations(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "ru")
    qtbot.addWidget(window)

    assert window.minimumWidth() == 440
    assert window.maximumWidth() > window.minimumWidth()
    assert window._sub_label.wordWrap()


def test_update_progress_uses_locale_aware_numbers(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "ru")
    qtbot.addWidget(window)

    window._on_progress(1_572_864, 3_145_728)

    assert "1,5 МБ" in window._sub_label.text()
    assert "3,0 МБ" in window._sub_label.text()
    assert "50%" in window._sub_label.text()


def test_update_window_runtime_language_switch_preserves_state(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    info = replace(
        _update_info(),
        localized_notes={"en": "English notes", "ru": "Русские примечания"},
    )
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._switch_to_downloading()
    window._on_progress(1_572_864, 3_145_728)

    window.update_language("ru-RU")

    assert window.windowTitle() == update_window.tr("ru", "update_title")
    assert window._title_label.text() == window.windowTitle()
    assert window._notes_label.text() == "Русские примечания"
    assert "1,5 МБ" in window._sub_label.text()
    assert window._btn_secondary.text() == update_window.tr("ru", "update_minimize")


def test_update_window_runtime_language_switch_relocalizes_known_errors(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)
    window._switch_to_error(
        update_window._localized_update_error(
            "en", update_window.requests.ConnectionError()
        )
    )

    window.update_language("ja")

    assert window._sub_label.text() == update_window._localized_update_error(
        "ja", update_window.requests.ConnectionError()
    )
    assert window._btn_primary.text() == "再試行"


def test_update_error_details_are_localized_without_raw_exception_text():
    raw = RuntimeError("Update server returned an unsupported Content-Encoding")

    assert update_window._localized_update_error("en", raw) == (
        "The update server returned an unsupported Content-Encoding."
    )
    russian = update_window._localized_update_error("ru", raw)
    assert "неподдерживаемый формат сжатия" in russian
    assert "Content-Encoding" not in russian


def test_update_window_ready_button_launches_installer_once(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    launched: list[object] = []
    destroyed: list[bool] = []

    class Process:
        pid = 1234

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(update_window, "verify_installer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        update_window,
        "_launch_installer",
        lambda path, **kwargs: launched.append((path, kwargs)) or Process(),
    )

    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: destroyed.append(True)

    window._switch_to_ready()
    window._btn_primary.click()

    assert launched == [(installer, {
        "expected_sha256": "a" * 64,
        "expected_size": 123,
        "installer_signature": _update_info().installer_signature,
        "signature_algorithm": INSTALLER_SIGNATURE_ALGORITHM,
        "signature_key_id": TEST_KEY_ID,
        "trusted_public_keys": TEST_PUBLIC_KEYS,
    })]
    assert destroyed == [True]


def test_install_now_verifies_quiesces_revalidates_then_exits(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    events: list[str] = []

    class Process:
        pid = 1238

        @staticmethod
        def poll():
            return None

    class Parent(update_window.QWidget):
        def _prepare_for_update_install(self):
            events.append("prepare")
            return True

        def _abort_update_install_preparation(self):
            events.append("abort")

    parent = Parent()
    qtbot.addWidget(parent)
    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda *_args, **_kwargs: events.append("verify"),
    )
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda *_args, **_kwargs: events.append("popen") or Process(),
    )
    window = UpdateWindow(parent, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: events.append("destroy")
    window._switch_to_ready()

    window._run_installer()

    assert events == ["verify", "prepare", "verify", "popen", "destroy"]


def test_failed_quiesce_keeps_mio_open_and_does_not_launch(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    launched: list[bool] = []
    destroyed: list[bool] = []

    class Parent(update_window.QWidget):
        def __init__(self):
            super().__init__()
            self.abort_calls = 0

        @staticmethod
        def _prepare_for_update_install():
            return False

        def _abort_update_install_preparation(self):
            self.abort_calls += 1

    parent = Parent()
    qtbot.addWidget(parent)
    monkeypatch.setattr(update_window, "verify_installer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        update_window,
        "_launch_installer",
        lambda *_args, **_kwargs: launched.append(True),
    )
    window = UpdateWindow(parent, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: destroyed.append(True)
    window._switch_to_ready()

    window._run_installer()

    assert parent.abort_calls == 1
    assert launched == []
    assert destroyed == []
    assert window._sub_label.text() == update_window.tr(
        "en", "update_shutdown_failed"
    )


def test_installer_launch_failure_keeps_mio_open_and_reports_localized_error(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    destroyed = []

    def fail_launch(*_args, **_kwargs):
        raise OSError("process creation failed")

    monkeypatch.setattr(update_window, "verify_installer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(update_window, "_launch_installer", fail_launch)
    window = UpdateWindow(None, _update_info(), "ko")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: destroyed.append(True)
    window._switch_to_ready()

    window._btn_primary.click()

    assert destroyed == []
    assert window._view_state == "error"
    assert window._sub_label.text() == update_window.tr(
        "ko", "update_installer_launch_failed"
    )
    assert window._btn_secondary.text() == update_window.tr("ko", "update_install_later")


def test_install_now_reentrant_click_launches_only_one_process(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    launched: list[Path] = []
    destroyed: list[bool] = []

    class Process:
        pid = 1235

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(update_window, "verify_installer", lambda *_args, **_kwargs: None)
    window = UpdateWindow(None, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._destroy_master_if_alive = lambda: destroyed.append(True)

    def launch(path, **_kwargs):
        launched.append(path)
        window._run_installer()
        return Process()

    monkeypatch.setattr(update_window, "_launch_installer", launch)
    window._switch_to_ready()

    window._run_installer()
    window._run_installer()

    assert launched == [installer]
    assert destroyed == [True]


def test_invalid_installer_never_quiesces_or_launches(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "app.exe"
    installer.write_bytes(b"installer")
    launched: list[bool] = []

    class Parent(update_window.QWidget):
        def __init__(self):
            super().__init__()
            self.prepared = 0

        def _prepare_for_update_install(self):
            self.prepared += 1
            return True

    parent = Parent()
    qtbot.addWidget(parent)
    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            update_window.InstallerVerificationError("signature mismatch")
        ),
    )
    monkeypatch.setattr(
        update_window,
        "_launch_installer",
        lambda *_args, **_kwargs: launched.append(True),
    )
    window = UpdateWindow(parent, _update_info(), "en")
    qtbot.addWidget(window)
    window._installer_path = installer
    window._switch_to_ready()

    window._run_installer()

    assert parent.prepared == 0
    assert launched == []
    assert window._view_state == "error"
    assert window._sub_label.text() == update_window.tr(
        "en", "update_error_verification"
    )


def test_repair_update_window_uses_repair_copy_and_hides_ignore(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    info = replace(
        _update_info(),
        flow="repair",
        localized_notes={"en": "Detected issue: No module named 'sklearn'"},
    )

    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)

    assert window.windowTitle() == "Voice Cloning Repair"
    assert window._btn_ignore.isHidden()
    assert window._btn_primary.text() == "Download Full Installer"

    window._switch_to_ready()

    assert "repair Voice Cloning" in window._progress_label.text()


def test_restored_repair_installer_keeps_repair_specific_note(
    qtbot,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    payload = b"verified repair installer"
    info = replace(_update_info_for_payload(payload), flow="repair")
    initial = UpdateWindow(None, info, "en")
    qtbot.addWidget(initial)
    initial._final_path.write_bytes(payload)

    restored = UpdateWindow(None, info, "en")
    qtbot.addWidget(restored)

    assert restored._view_state == "ready"
    assert restored._sub_label.text() == update_window.tr(
        "en", "xtts_runtime_repair_install_note"
    )


@pytest.mark.parametrize("language", ("zh-CN", "en", "ja", "ru", "ko"))
def test_install_copy_describes_a_visible_installer(language):
    note = update_window.tr(language, "update_install_note")
    launching = update_window.tr(language, "update_launching_note")

    assert note
    assert launching
    assert update_window.tr(language, "update_shutdown_failed")
    forbidden = {
        "zh-CN": "后台",
        "en": "background",
        "ja": "バックグラウンド",
        "ru": "фонов",
        "ko": "백그라운드",
    }
    assert forbidden[language].casefold() not in note.casefold()
    assert forbidden[language].casefold() not in launching.casefold()


@pytest.mark.parametrize(
    ("status", "exit_code", "succeeded"),
    (("success", 0, True), ("failed", 5, False)),
)
def test_update_result_marker_is_consumed_securely(
    tmp_path,
    monkeypatch,
    status,
    exit_code,
    succeeded,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    marker = tmp_path / "mio-update-install-random123.result.json"
    marker.write_text(
        f'{{"status":"{status}","exit_code":{exit_code}}}',
        encoding="utf-8",
    )
    installer_log = tmp_path / "mio-update-install-random123.log"
    installer_log.write_text("installer log", encoding="utf-8")
    argv = ["MioTranslator.exe", f"--mio-update-result={marker}", "--kept"]

    result = update_window.consume_update_install_result(argv)

    assert result == update_window.UpdateInstallResult(succeeded, exit_code)
    assert argv == ["MioTranslator.exe", "--kept"]
    assert not marker.exists()
    assert installer_log.exists() is (not succeeded)


def test_missing_update_result_marker_becomes_localized_recoverable_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    marker = tmp_path / "mio-update-install-missing.result.json"
    argv = ["MioTranslator.exe", f"--mio-update-result={marker}"]

    result = update_window.consume_update_install_result(argv)

    assert result == update_window.UpdateInstallResult(False, None)
    assert argv == ["MioTranslator.exe"]


def test_update_result_argument_cannot_delete_file_outside_private_temp_dir(
    tmp_path,
    monkeypatch,
):
    private_temp = tmp_path / "private-temp"
    private_temp.mkdir()
    outside = tmp_path / "mio-update-install-outside.result.json"
    outside.write_text('{"status":"failed","exit_code":5}', encoding="utf-8")
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: private_temp)
    argv = ["MioTranslator.exe", f"--mio-update-result={outside}"]

    result = update_window.consume_update_install_result(argv)

    assert result == update_window.UpdateInstallResult(False, None)
    assert outside.read_text(encoding="utf-8") == (
        '{"status":"failed","exit_code":5}'
    )


def test_update_result_dialog_uses_selected_language(monkeypatch):
    shown = []
    monkeypatch.setattr(
        update_window.QMessageBox,
        "information",
        lambda parent, title, message: shown.append(("info", parent, title, message)),
    )
    monkeypatch.setattr(
        update_window.QMessageBox,
        "warning",
        lambda parent, title, message: shown.append(("warning", parent, title, message)),
    )

    update_window.show_update_install_result(
        None,
        update_window.UpdateInstallResult(False, 5),
        "ru-RU",
    )

    assert shown == [
        (
            "warning",
            None,
            update_window.tr("ru", "update_install_failed_title"),
            update_window.tr("ru", "update_install_failed_message"),
        )
    ]


def test_successful_update_result_uses_non_modal_main_window_status(monkeypatch):
    statuses = []

    class Parent:
        def _set_bottom(self, message, color, *, key=None):
            statuses.append((message, color, key))

    monkeypatch.setattr(
        update_window.QMessageBox,
        "information",
        lambda *_args, **_kwargs: pytest.fail(
            "successful automatic installation must not require a modal click"
        ),
    )

    update_window.show_update_install_result(
        Parent(),
        update_window.UpdateInstallResult(True, 0),
        "ja-JP",
    )

    assert statuses == [
        (
            update_window.tr("ja", "update_install_success_message"),
            "success",
            "update_install_success_message",
        )
    ]


def test_installer_process_confirmation_rejects_invalid_or_failed_startup():
    with pytest.raises(OSError):
        update_window._confirm_installer_process_started(None)

    class FailedProcess:
        pid = 42

        @staticmethod
        def poll():
            return 1

    with pytest.raises(OSError):
        update_window._confirm_installer_process_started(FailedProcess())

    class BootstrapProcess:
        pid = 43

        @staticmethod
        def poll():
            return 0

    update_window._confirm_installer_process_started(BootstrapProcess())

class _RecordedSignal:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def _bridge():
    return SimpleNamespace(
        progress=_RecordedSignal(),
        complete=_RecordedSignal(),
        error=_RecordedSignal(),
    )


class _FakeResponse:
    def __init__(
        self,
        chunks,
        *,
        content_length=None,
        status_code=200,
        url=None,
        headers=None,
    ):
        self._chunks = list(chunks)
        self.headers = dict(headers or {})
        if content_length is not None:
            self.headers["content-length"] = str(content_length)
        self.url = url or (
            "https://github.com/CokoIya/MioVRC_Translator/"
            "releases/download/v9.9.9/app.exe"
        )
        self.status_code = status_code
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


def test_download_rejects_untrusted_intermediate_redirect(
    qtbot,
    tmp_path,
    monkeypatch,
):
    info = _update_info()
    response = _FakeResponse(
        [],
        status_code=302,
        headers={"Location": "https://evil.example/app.exe"},
    )
    requests = []

    def get(*args, **kwargs):
        requests.append((args, kwargs))
        return response

    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )
    monkeypatch.setattr(update_window.requests, "get", get)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert len(requests) == 1
    assert requests[0][1]["allow_redirects"] is False
    assert response.closed
    assert window._bridge.complete.calls == []
    assert "redirect URL is not trusted" in window._bridge.error.calls[0][0]
    assert not window._wip_path.exists()
    assert not window._final_path.exists()


def test_download_validates_and_closes_every_trusted_redirect(
    qtbot,
    tmp_path,
    monkeypatch,
):
    payload = b"signed installer"
    info = _update_info_for_payload(payload)
    first = _FakeResponse(
        [],
        status_code=302,
        headers={
            "Location": (
                "https://release-assets.githubusercontent.com/"
                "github-production-release-asset/app.exe"
            )
        },
    )
    second = _FakeResponse(
        [payload],
        content_length=len(payload),
        url=(
            "https://release-assets.githubusercontent.com/"
            "github-production-release-asset/app.exe"
        ),
    )
    responses = iter((first, second))
    requests = []

    def get(*args, **kwargs):
        requests.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )
    monkeypatch.setattr(update_window.requests, "get", get)
    monkeypatch.setattr(update_window, "verify_installer", lambda *args, **kwargs: None)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert len(requests) == 2
    assert all(call[1]["allow_redirects"] is False for call in requests)
    assert first.closed
    assert second.closed
    assert window._bridge.complete.calls == [(window._final_path,)]
    assert window._final_path.read_bytes() == payload


def test_download_rejects_compressed_installer_response(
    qtbot,
    tmp_path,
    monkeypatch,
):
    payload = b"compressed bytes"
    info = replace(
        _update_info(),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    response = _FakeResponse(
        [payload],
        content_length=len(payload),
        headers={"content-encoding": "gzip"},
    )
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert response.closed
    assert window._bridge.complete.calls == []
    assert "unsupported Content-Encoding" in window._bridge.error.calls[0][0]
    assert not window._wip_path.exists()
    assert not window._final_path.exists()


def test_download_rejects_manifest_content_length_mismatch(qtbot, tmp_path, monkeypatch):
    payload = b"abcd"
    info = replace(
        _update_info(),
        size_bytes=3,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    response = _FakeResponse([payload], content_length=len(payload))
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(update_window, "TRUSTED_INSTALLER_PUBLIC_KEYS", TEST_PUBLIC_KEYS)
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert window._bridge.complete.calls == []
    assert "does not match the signed manifest" in window._bridge.error.calls[0][0]
    assert not window._wip_path.exists()


def test_download_aborts_before_writing_projected_overflow(qtbot, tmp_path, monkeypatch):
    payload = b"abcd"
    info = replace(
        _update_info(),
        size_bytes=3,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    response = _FakeResponse([b"ab", b"cd"])
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(update_window, "TRUSTED_INSTALLER_PUBLIC_KEYS", TEST_PUBLIC_KEYS)
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert "exceeded the signed manifest size" in window._bridge.error.calls[0][0]
    assert not window._wip_path.exists()
    assert not window._final_path.exists()


def test_download_exclusively_creates_work_file_without_clobbering_hardlink(
    qtbot,
    tmp_path,
    monkeypatch,
):
    payload = b"signed installer"
    info = replace(
        _update_info(),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    response = _FakeResponse([payload], content_length=len(payload))
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(update_window, "TRUSTED_INSTALLER_PUBLIC_KEYS", TEST_PUBLIC_KEYS)
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_bytes(b"preserve this file")
    try:
        os.link(sensitive, window._wip_path)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable")

    window._download_worker()

    assert window._bridge.complete.calls == []
    assert window._bridge.error.calls
    assert sensitive.read_bytes() == b"preserve this file"
    assert not window._wip_path.exists()
    assert not window._final_path.exists()
    assert response.closed


def test_download_rejects_staged_path_replacement_before_publication(
    qtbot,
    tmp_path,
    monkeypatch,
):
    payload = b"signed installer"
    info = replace(
        _update_info(),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    response = _FakeResponse([payload], content_length=len(payload))
    sensitive = tmp_path / "sensitive.exe"
    sensitive.write_bytes(payload)
    original_publish = update_window.atomic_replace_secure_file

    def replace_then_publish(staged, destination, **kwargs):
        staged.unlink()
        try:
            os.link(sensitive, staged)
        except (OSError, NotImplementedError):
            pytest.skip("hardlink creation is unavailable")
        return original_publish(staged, destination, **kwargs)

    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        update_window,
        "atomic_replace_secure_file",
        replace_then_publish,
    )
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert response.closed
    assert window._bridge.complete.calls == []
    assert window._bridge.error.calls
    assert sensitive.read_bytes() == payload
    assert not window._wip_path.exists()
    assert not window._final_path.exists()


def test_download_promotes_only_after_shared_verification(qtbot, tmp_path, monkeypatch):
    payload = b"signed installer"
    info = _update_info_for_payload(payload)
    response = _FakeResponse([payload], content_length=len(payload))
    verified = []
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    monkeypatch.setattr(update_window, "TRUSTED_INSTALLER_PUBLIC_KEYS", TEST_PUBLIC_KEYS)
    monkeypatch.setattr(update_window.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda path, **kwargs: verified.append((path, kwargs)),
    )
    window = UpdateWindow(None, info, "en")
    qtbot.addWidget(window)
    window._bridge = _bridge()

    window._download_worker()

    assert verified == [(window._final_path, {
        "expected_sha256": info.sha256,
        "expected_size": len(payload),
        "installer_signature": info.installer_signature,
        "signature_algorithm": info.installer_signature_algorithm,
        "signature_key_id": info.installer_signature_key_id,
        "trusted_public_keys": TEST_PUBLIC_KEYS,
    })]
    assert window._bridge.complete.calls == [(window._final_path,)]
    assert window._final_path.read_bytes() == payload


def test_windows_launch_revalidates_then_starts_visible_installer_directly(
    tmp_path,
    monkeypatch,
):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    events = []

    class Process:
        pid = 1236

    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda path, **kwargs: events.append(("verify", path, kwargs)),
    )
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda command, **kwargs: events.append(("launch", command, kwargs)) or Process(),
    )

    process = update_window._launch_installer(
        installer,
        expected_sha256="a" * 64,
        expected_size=9,
        installer_signature=_installer_signature("a" * 64, 9),
        signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
        signature_key_id=TEST_KEY_ID,
        trusted_public_keys=TEST_PUBLIC_KEYS,
    )

    assert process.pid == 1236
    assert [event[0] for event in events] == ["verify", "launch"]
    command, kwargs = events[1][1:]
    assert command == [str(installer)]
    assert kwargs == {"cwd": str(installer.parent)}


def test_visible_installer_launch_has_no_silent_or_hidden_flags(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    launches = []

    monkeypatch.setattr(update_window, "verify_installer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append((command, kwargs)) or SimpleNamespace(pid=1237),
    )

    update_window._launch_installer(
        installer,
        expected_sha256="a" * 64,
        expected_size=9,
        installer_signature=_installer_signature("a" * 64, 9),
        signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
        signature_key_id=TEST_KEY_ID,
        trusted_public_keys=TEST_PUBLIC_KEYS,
    )

    command, kwargs = launches[0]
    assert command == [str(installer)]
    assert kwargs == {"cwd": str(installer.parent)}
    forbidden = (
        "powershell",
        "-windowstyle",
        "hidden",
        "/verysilent",
        "/silent",
        "/suppressmsgboxes",
        "/nocancel",
        "/closeapplications",
        "/restartapplications",
    )
    rendered = " ".join(command).casefold()
    assert not any(flag in rendered for flag in forbidden)
    assert "creationflags" not in kwargs


def test_visible_installer_popen_failure_is_propagated(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    events: list[str] = []
    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda *_args, **_kwargs: events.append("verify"),
    )
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    with pytest.raises(OSError, match="denied"):
        update_window._launch_installer(
            installer,
            expected_sha256="a" * 64,
            expected_size=9,
            installer_signature=_installer_signature("a" * 64, 9),
            signature_algorithm=INSTALLER_SIGNATURE_ALGORITHM,
            signature_key_id=TEST_KEY_ID,
            trusted_public_keys=TEST_PUBLIC_KEYS,
        )

    assert events == ["verify"]
