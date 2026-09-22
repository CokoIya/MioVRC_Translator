"""Letting SteamVR launch Mio: the manifest and the registration calls."""

from __future__ import annotations

import json
import os
import sys
import types

from src.core import steamvr_autolaunch
from src.core.steamvr_autolaunch import APP_KEY, apply, is_enabled, launch_command, manifest_document


class _Apps:
    def __init__(self, *, installed=False, auto=False):
        self.calls: list[tuple] = []
        self.installed = installed
        self.auto = auto

    def addApplicationManifest(self, path, temporary=False):
        self.calls.append(("add", path, temporary))
        self.installed = True

    def identifyApplication(self, pid, key):
        self.calls.append(("identify", pid, key))

    def setApplicationAutoLaunch(self, key, enabled):
        self.calls.append(("auto", key, enabled))
        self.auto = bool(enabled)

    def isApplicationInstalled(self, key):
        return self.installed and key == APP_KEY

    def getApplicationAutoLaunch(self, key):
        return self.auto


def _openvr(apps):
    return types.SimpleNamespace(VRApplications=lambda: apps)


class TestManifest:
    def test_the_manifest_describes_a_dashboard_overlay_app(self, monkeypatch, tmp_path):
        monkeypatch.setattr(steamvr_autolaunch, "manifest_path", lambda: tmp_path / "mio.vrmanifest")

        document = manifest_document()
        app = document["applications"][0]

        assert app["app_key"] == APP_KEY
        assert app["is_dashboard_overlay"] is True
        assert app["launch_type"] == "binary"
        assert app["binary_path_windows"] == sys.executable
        assert "zh_cn" in app["strings"] and "en_us" in app["strings"]

    def test_from_source_the_interpreter_runs_main_py(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)

        binary, arguments = launch_command()

        assert binary == sys.executable
        assert arguments.endswith('main.py"')

    def test_frozen_the_exe_runs_bare(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        binary, arguments = launch_command()

        assert binary == sys.executable
        assert arguments == ""


class TestApply:
    def test_enabling_writes_the_manifest_registers_and_flags_auto_launch(self, monkeypatch, tmp_path):
        path = tmp_path / "steamvr" / "mio.vrmanifest"
        monkeypatch.setattr(steamvr_autolaunch, "manifest_path", lambda: path)
        apps = _Apps()

        ok, reason = apply(_openvr(apps), True)

        assert (ok, reason) == (True, "")
        assert path.is_file()
        assert json.loads(path.read_text("utf-8"))["applications"][0]["app_key"] == APP_KEY
        assert apps.calls[0] == ("add", str(path), False)
        assert ("identify", os.getpid(), APP_KEY) in apps.calls
        assert apps.calls[-1] == ("auto", APP_KEY, True)
        assert is_enabled(_openvr(apps)) is True

    def test_disabling_only_clears_the_flag_of_a_registered_app(self):
        apps = _Apps(installed=True, auto=True)

        assert apply(_openvr(apps), False) == (True, "")
        assert apps.calls == [("auto", APP_KEY, False)]
        assert is_enabled(_openvr(apps)) is False

    def test_disabling_an_unregistered_app_touches_nothing(self):
        apps = _Apps(installed=False)

        assert apply(_openvr(apps), False) == (True, "")
        assert apps.calls == []
        assert is_enabled(_openvr(apps)) is False

    def test_a_runtime_that_refuses_is_reported_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setattr(steamvr_autolaunch, "manifest_path", lambda: tmp_path / "m.vrmanifest")

        class _Refusing(_Apps):
            def addApplicationManifest(self, path, temporary=False):
                raise RuntimeError("ApplicationError_InvalidManifest")

        ok, reason = apply(_openvr(_Refusing()), True)

        assert ok is False
        assert reason.startswith("apply_failed")

    def test_without_the_applications_interface_nothing_can_be_said(self):
        assert is_enabled(types.SimpleNamespace()) is None
