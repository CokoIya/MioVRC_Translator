from dataclasses import replace
import hashlib
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
def _trusted_installer_public_key(monkeypatch):
    monkeypatch.setattr(
        update_window,
        "TRUSTED_INSTALLER_PUBLIC_KEYS",
        TEST_PUBLIC_KEYS,
    )


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

    monkeypatch.setattr(
        update_window,
        "_launch_installer",
        lambda path, **kwargs: launched.append((path, kwargs)),
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


def test_windows_update_helper_waits_installs_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    installer = tmp_path / "MioTranslator-Setup.exe"
    restart_exe = tmp_path / "MioTranslator.exe"

    helper = update_window._write_windows_update_helper(
        installer,
        restart_exe,
        expected_sha256="a" * 64,
        expected_size=123,
    )

    script = helper.read_text(encoding="utf-8-sig")
    assert "Wait-Process -Id" in script
    assert "$PSModuleAutoLoadingPreference = 'None'" in script
    assert "Microsoft.PowerShell.Security.psd1" not in script
    assert "/VERYSILENT" in script
    assert "/CLOSEAPPLICATIONS" in script
    assert "/RESTARTAPPLICATIONS" in script
    assert "Start-Process -FilePath $restartExe" in script
    assert "$expectedSha256 = '" + "a" * 64 + "'" in script
    assert "$expectedSize = [Int64]123" in script
    locked_hash = "Get-FileHash -InputStream $launchStream -Algorithm SHA256"
    assert script.count(locked_hash) == 1
    assert script.count("Get-FileHash") == 1
    final_hash_offset = script.index(locked_hash)
    launch_offset = script.index("$process = Start-Process")
    assert script.index("$arguments = @(") < final_hash_offset
    assert final_hash_offset < launch_offset
    assert "[System.IO.FileShare]::Read" in script
    assert "Get-AuthenticodeSignature" not in script
    assert "$launchStream.Dispose()" in script
    assert script.index("$launchStream.Dispose()") > launch_offset
    assert "ReparsePoint" in script
    assert str(helper.with_suffix(".log")) in script


def test_windows_update_helper_supports_install_without_app_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)

    helper = update_window._write_windows_update_helper(
        tmp_path / "MioTranslator-Setup.exe",
        None,
        expected_sha256="a" * 64,
        expected_size=123,
    )

    script = helper.read_text(encoding="utf-8-sig")
    assert "$restartExe = $null" in script
    assert "$installDir = $null" in script
    assert "if ($null -ne $installDir) {" in script
    assert "if (($null -ne $restartExe) -and" in script


def test_windows_update_helper_does_not_clobber_predictable_hardlink(tmp_path, monkeypatch):
    monkeypatch.setattr(update_window, "app_temp_dir", lambda: tmp_path)
    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_bytes(b"preserve this file")
    predictable = tmp_path / f"mio-update-install-{os.getpid()}.ps1"
    try:
        os.link(sensitive, predictable)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation is unavailable")

    helper = update_window._write_windows_update_helper(
        tmp_path / "MioTranslator-Setup.exe",
        tmp_path / "MioTranslator.exe",
        expected_sha256="a" * 64,
        expected_size=123,
    )

    assert helper != predictable
    assert helper.parent == tmp_path
    assert helper.name.startswith("mio-update-install-")
    assert sensitive.read_bytes() == b"preserve this file"
    assert predictable.read_bytes() == b"preserve this file"

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
    info = replace(
        _update_info(),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
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
    info = replace(
        _update_info(),
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
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


def test_windows_launch_uses_resolved_absolute_powershell(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    restart_exe = tmp_path / "MioTranslator.exe"
    restart_exe.write_bytes(b"app")
    helper = tmp_path / "random-helper.ps1"
    helper.write_text("# helper", encoding="utf-8")
    powershell = (
        tmp_path
        / "Windows"
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    launches = []

    monkeypatch.setattr(update_window, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(update_window, "verify_installer", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_window, "_restart_executable", lambda: restart_exe)
    monkeypatch.setattr(update_window, "windows_powershell_executable", lambda: powershell)
    monkeypatch.setattr(update_window, "_write_windows_update_helper", lambda *args, **kwargs: helper)
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append((command, kwargs)),
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

    assert len(launches) == 1
    command, _kwargs = launches[0]
    assert command[0] == str(powershell)
    assert Path(command[0]).is_absolute()
    assert command[-2:] == ["-File", str(helper)]


def test_launch_installer_revalidates_before_secure_windows_helper_launch(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    helper = tmp_path / "random-helper.ps1"
    powershell = tmp_path / "Windows" / "System32" / "powershell.exe"
    events = []

    monkeypatch.setattr(update_window, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        update_window,
        "verify_installer",
        lambda path, **kwargs: events.append(("verify", path, kwargs)),
    )
    monkeypatch.setattr(update_window, "_restart_executable", lambda: None)
    monkeypatch.setattr(update_window, "windows_powershell_executable", lambda: powershell)

    def write_helper(path, restart_exe, **kwargs):
        events.append(("helper", path, restart_exe, kwargs))
        return helper

    monkeypatch.setattr(update_window, "_write_windows_update_helper", write_helper)
    monkeypatch.setattr(
        update_window.subprocess,
        "Popen",
        lambda command, **kwargs: events.append(("launch", command, kwargs)),
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

    assert [event[0] for event in events] == ["verify", "helper", "launch"]
    assert events[1][2] is None
    assert events[2][1][0] == str(powershell)
    assert events[2][1][-2:] == ["-File", str(helper)]
