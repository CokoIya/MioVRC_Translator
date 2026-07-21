from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_requirements_include_functional_xtts_runtime() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")

    assert "coqui-tts==0.27.5" in requirements
    assert "scikit-learn>=1.8.0,<2.0.0" in requirements
    assert "cutlet>=0.5.2,<0.6.0" in requirements
    assert "fugashi>=1.5.2,<2.0.0" in requirements
    assert "unidic-lite==1.0.8" in requirements
    assert "mojimoji>=0.0.13,<0.1.0" in requirements
    assert "pyworld-prebuilt==0.3.5.post2" in requirements
    assert "transformers>=5.5.0,<6.0.0" in requirements
    assert "nltk>=3.10.0,<4.0.0" in requirements
    assert "torch==2.8.0+cpu" in requirements
    assert "coqui-tts==0.27.5" in lock
    assert "scikit-learn==1.8.0" in lock
    assert "cutlet==0.5.2" in lock
    assert "fugashi==1.5.2" in lock
    assert "unidic-lite==1.0.8" in lock
    assert "mojimoji==0.0.13" in lock
    assert "transformers==5.5.0" in lock
    assert "torch==2.8.0+cpu" in lock
    assert "av==17.0.1" in lock
    assert "num2words==0.5.14" in lock
    assert "ko-speech-tools==0.1.0" in lock
    assert "g2p-en==2.1.0" in lock
    assert "nltk==3.10.0" in lock
    assert "defusedxml==0.7.1" in lock
    assert "pyworld-prebuilt==0.3.5.post2" in lock
    assert "setuptools==81.0.0" in lock


def test_pyinstaller_spec_collects_coqui_xtts_runtime() -> None:
    spec = (ROOT / "MioTranslator.spec").read_text(encoding="utf-8")

    assert '"TTS"' in spec
    assert '"TTS.api"' in spec
    assert '"sklearn"' in spec
    assert '"cutlet"' in spec
    assert '"fugashi"' in spec
    assert '"unidic_lite"' in spec
    assert '"mojimoji"' in spec
    assert '"av"' in spec
    assert '"av.audio.resampler"' in spec
    assert '"coqpit"' in spec
    assert '"trainer"' in spec
    assert '"num2words"' in spec
    assert '"ko_speech_tools"' in spec
    assert '"pypinyin"' in spec
    assert '"g2p_en"' in spec
    assert '"nltk"' in spec
    assert '"torchcodec"' in spec.split("excludes = [", 1)[1]
    assert '"TTS.tts.models.xtts"' in spec
    assert '"TTS.tts.layers.xtts.gpt"' in spec
    assert '"TTS.tts.layers.xtts.gpt_inference"' in spec
    assert '"transformers.models.gpt2.modeling_gpt2"' in spec
    assert '"transformers.models.gpt2.configuration_gpt2"' in spec
    assert '"transformers.models.gpt2.tokenization_gpt2"' in spec
    assert '"transformers.models.gpt2.tokenization_gpt2_fast"' not in spec
    assert '"transformers.generation"' in spec
    assert '"transformers.modeling_outputs"' in spec
    assert '"g2p_en",' not in spec.split("excludes = [", 1)[1]
    assert '"nltk",' not in spec.split("excludes = [", 1)[1]
    assert '"sklearn",' not in spec.split("excludes = [", 1)[1]

    hook = (ROOT / "tools" / "pyinstaller_hooks" / "hook-sklearn.py").read_text(encoding="utf-8")
    assert "collect_submodules(\"sklearn\"" in hook
    assert "collect_dynamic_libs(\"sklearn\")" in hook
    assert "collect_data_files(\"sklearn\")" in hook


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
    assert '"sklearn"' in check
    assert '"cutlet"' in check
    assert '"fugashi"' in check
    assert '"unidic_lite"' in check
    assert '"mojimoji"' in check
    assert '"av.audio.resampler"' in check
    assert '"num2words"' in check
    assert '"ko_speech_tools"' in check
    assert '"pypinyin"' in check
    assert '"nltk"' in check
    assert "_ensure_transformers_xtts_exports()" in check
    assert "from transformers import GPT2PreTrainedModel" in check
    assert "import TTS.tts.layers.xtts.gpt_inference" in check
    assert "from TTS.api import TTS" in check


def test_packaged_selftest_checks_voice_cloning_runtime() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "xtts_runtime_status(require_api=True)" in main
    assert "xtts_packaging_selftest()" in main
    assert "Voice Cloning runtime dependencies are missing" in main


def test_single_release_script_uses_full_feature_packaging_workflow() -> None:
    script = (ROOT / "build_release.ps1").read_text(encoding="utf-8")

    assert "tools\\check_release_environment.py" in script
    assert "-m PyInstaller --clean --noconfirm MioTranslator.spec" in script
    assert "MioTranslator-installer.iss" in script
    assert "MIO_TRANSLATOR_BUNDLE_MODELS" not in script
    assert ".venv-release311\\Scripts\\python.exe" in script
    assert ".venv311\\Scripts\\python.exe" not in script
    assert "main.py --mio-selftest" in script
    assert "dist\\MioTranslator\\MioTranslator.exe" in script


def test_release_environment_has_reproducible_rebuild_script() -> None:
    script = (ROOT / "rebuild_release_environment.ps1").read_text(encoding="utf-8")

    assert "C:\\Program Files\\Python311\\python.exe" in script
    assert "https://pypi.org/simple" in script
    assert "requirements.lock.txt" in script
    assert "--isolated" in script
    assert "tools\\check_release_environment.py" in script
    assert "main.py --mio-selftest" in script
    assert "MIO_TRANSLATOR_HOME" in script


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
        "test_ui_modernization.py",
        "scripts/modify_settings_window.py",
        "tools/download_models.py",
        "tools/final_check.py",
        "tools/test_frontend_optimization.py",
        "tools/test_realtime_integration.py",
        "tools/check_translation_release_environment.py",
        "tools/verify_fixes.py",
        "tools/verify_realtime_integration.py",
        "tools/verify_reintegration.py",
    )

    assert [path for path in obsolete_paths if (ROOT / path).exists()] == []
