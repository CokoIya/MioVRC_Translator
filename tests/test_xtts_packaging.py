from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_requirements_include_functional_xtts_runtime() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")

    assert "coqui-tts==0.27.5" in requirements
    assert "cutlet>=0.5.2,<0.6.0" in requirements
    assert "fugashi>=1.5.2,<2.0.0" in requirements
    assert "unidic-lite==1.0.8" in requirements
    assert "mojimoji>=0.0.13,<0.1.0" in requirements
    assert "transformers>=4.57.6,<5.0.0" in requirements
    assert "torch==2.8.0+cpu" in requirements
    assert "coqui-tts==0.27.5" in lock
    assert "cutlet==0.5.2" in lock
    assert "fugashi==1.5.2" in lock
    assert "unidic-lite==1.0.8" in lock
    assert "mojimoji==0.0.13" in lock
    assert "transformers==4.57.6" in lock
    assert "torch==2.8.0+cpu" in lock


def test_pyinstaller_spec_collects_coqui_xtts_runtime() -> None:
    spec = (ROOT / "MioTranslator.spec").read_text(encoding="utf-8")

    assert '"TTS"' in spec
    assert '"TTS.api"' in spec
    assert '"cutlet"' in spec
    assert '"fugashi"' in spec
    assert '"unidic_lite"' in spec
    assert '"mojimoji"' in spec
    assert '"coqpit"' in spec
    assert '"trainer"' in spec


def test_full_pyinstaller_spec_includes_embedded_webspeech_bridge() -> None:
    spec = (ROOT / "MioTranslator.spec").read_text(encoding="utf-8")

    assert '"src.ui_qt.webspeech_bridge_window"' in spec
    assert '"PySide6.QtWebEngineCore"' in spec
    assert '"PySide6.QtWebEngineWidgets"' in spec


def test_pyinstaller_spec_filters_non_runtime_artifacts() -> None:
    spec = (ROOT / "MioTranslator.spec").read_text(encoding="utf-8")

    assert "filter_runtime_entries" in spec
    assert "filter_hiddenimports" in spec
    assert "a.binaries = TOC(filter_runtime_entries" in spec
    assert "a.datas = TOC(filter_runtime_entries" in spec
    assert "hiddenimports = filter_hiddenimports(hiddenimports)" in spec


def test_release_environment_check_imports_tts_api() -> None:
    check = (ROOT / "tools" / "check_release_environment.py").read_text(encoding="utf-8")

    assert '"TTS"' in check
    assert '"cutlet"' in check
    assert '"fugashi"' in check
    assert '"unidic_lite"' in check
    assert '"mojimoji"' in check
    assert "from TTS.api import TTS" in check


def test_single_release_script_uses_full_feature_packaging_workflow() -> None:
    script = (ROOT / "build_release.ps1").read_text(encoding="utf-8")

    assert "tools\\check_release_environment.py" in script
    assert "-m PyInstaller --clean --noconfirm MioTranslator.spec" in script
    assert "MioTranslator-installer.iss" in script
    assert "MIO_TRANSLATOR_BUNDLE_MODELS" not in script


def test_obsolete_packaging_workflows_are_removed() -> None:
    obsolete_paths = (
        "MioTranslator.translation.spec",
        "build_release_translation.ps1",
        "build_release_lite.ps1",
        "build_release_lite_installer.ps1",
        "build_release_full_installer.ps1",
        "build_booth_downloader.ps1",
        "Mio_vrc_download.spec",
        "Mio_vrc_download_bundle.spec",
        "tools/check_translation_release_environment.py",
    )

    assert [path for path in obsolete_paths if (ROOT / path).exists()] == []
