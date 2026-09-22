"""A test build carries its own version without touching the release surfaces.

build_beta.ps1 stamps the beta version into the bundle and the Windows
resource through environment variables; the committed version files stay as
the release wants them, and the release build does not notice the machinery.
"""

from __future__ import annotations

import re
from pathlib import Path

from src import version as version_module
from src.version import BUILD_VERSION_STAMP, build_version_override

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVersionStamp:
    def test_a_stamped_bundle_reports_the_stamped_version(self, tmp_path):
        (tmp_path / BUILD_VERSION_STAMP).write_text("1.4.2_beta\n", encoding="utf-8")

        assert build_version_override(str(tmp_path)) == "1.4.2_beta"

    def test_without_a_stamp_the_release_version_stands(self, tmp_path):
        assert build_version_override(str(tmp_path)) == ""
        assert build_version_override("") == ""

    def test_a_stamp_that_is_not_a_version_is_ignored(self, tmp_path):
        (tmp_path / BUILD_VERSION_STAMP).write_text("rm -rf /", encoding="utf-8")

        assert build_version_override(str(tmp_path)) == ""

    def test_the_source_tree_is_not_stamped(self):
        # Running from source there is no bundle, so the committed version applies.
        assert not hasattr(version_module, "_MEIPASS")
        assert re.fullmatch(r"\d+\.\d+\.\d+", version_module.APP_VERSION)

    def test_a_beta_version_sorts_between_the_releases_around_it(self):
        from src.updater.update_checker import _version_tuple

        assert _version_tuple("1.4.1") < _version_tuple("1.4.2_beta") < _version_tuple("1.4.2")


class TestBuildSurfaces:
    def test_the_installer_script_lets_a_build_override_its_version(self):
        installer = (REPO_ROOT / "MioTranslator-installer.iss").read_text(encoding="utf-8")

        assert re.search(r"^#ifndef AppVersion\s*\n#define AppVersion\s+\"v", installer, re.M)
        assert re.search(r"^#ifndef AppNumericVersion\s*\n#define AppNumericVersion\s+\"", installer, re.M)

    def test_the_spec_takes_the_stamp_and_resource_from_the_environment(self):
        spec = (REPO_ROOT / "MioTranslator.spec").read_text(encoding="utf-8")

        assert "MIO_BUILD_VERSION_STAMP" in spec
        assert "MIO_BUILD_VERSION_INFO" in spec
        assert 'version=_windows_version_info' in spec

    def test_the_beta_script_publishes_nothing(self):
        script = (REPO_ROOT / "build_beta.ps1").read_text(encoding="utf-8")

        for forbidden in ("update_release_manifests", "generate_release_checksums", "upload_to_r2", "git ", "docs\\"):
            assert forbidden not in script, forbidden
        assert "MIO_BUILD_VERSION_STAMP" in script
        assert "/DAppVersion=" in script
        # Only its own outputs are named in dist; nothing there is removed.
        for line in script.splitlines():
            if "Remove-Item" in line:
                assert "dist" not in line.lower(), line

    def test_the_release_checker_accepts_the_directml_runtime_for_onnxruntime(self):
        from tools.check_release_environment import dependency_is_pinned

        assert dependency_is_pinned("onnxruntime", {"onnxruntime-directml"})
        assert dependency_is_pinned("onnxruntime", {"onnxruntime"})
        assert not dependency_is_pinned("onnxruntime", {"rapidocr-onnxruntime"})
