from __future__ import annotations

from tools.pyinstaller_runtime_filter import (
    filter_hiddenimports,
    filter_runtime_entries,
    should_keep_hidden_import,
    should_keep_runtime_entry,
)


def test_runtime_filter_removes_large_build_time_artifacts() -> None:
    removed = [
        "torch/lib/dnnl.lib",
        "torch/include/ATen/core/Tensor.h",
        "PySide6/resources/qtwebengine_devtools_resources.debug.pak",
        "PySide6/resources/qtwebengine_devtools_resources.pak",
        "PySide6/resources/v8_context_snapshot.debug.bin",
        "PySide6/qml/QtQuick/Controls/Button.qml",
        "PySide6/translations/qtwebengine_locales/de.pak",
        "PySide6/translations/qtwebengine_locales/fr.pak",
        "torch/bin/protoc.exe",
        "torch/bin/fbgemm.dll",
        "torch/lib/torch_cuda.dll",
        "torch/lib/c10_cuda.dll",
        "torch/lib/cudnn64_9.dll",
        "torch/lib/cublasLt64_12.dll",
        "Cython/Build/Dependencies.py",
        "Pythonwin/mfc140u.dll",
        "transformers/tests/test_modeling.py",
        "assets/icons/Mio_1.png",
        "assets/icons/2.png",
        "assets/icons/IMG_0600.PNG",
    ]

    for dest in removed:
        assert should_keep_runtime_entry(dest, f"C:/venv/Lib/site-packages/{dest}") is False


def test_runtime_filter_keeps_required_runtime_payloads() -> None:
    kept = [
        "torch/lib/torch_cpu.dll",
        "torch/lib/torch_python.dll",
        "PySide6/Qt6WebEngineCore.dll",
        "PySide6/resources/icudtl.dat",
        "PySide6/resources/qtwebengine_resources.pak",
        "PySide6/resources/v8_context_snapshot.bin",
        "PySide6/translations/qtbase_ja.qm",
        "PySide6/translations/qtwebengine_zh_CN.qm",
        "PySide6/translations/qtwebengine_locales/en-US.pak",
        "PySide6/translations/qtwebengine_locales/ja.pak",
        "PySide6/translations/qtwebengine_locales/ko.pak",
        "PySide6/translations/qtwebengine_locales/ru.pak",
        "PySide6/translations/qtwebengine_locales/zh-CN.pak",
        "PySide6/translations/qtwebengine_locales/zh-TW.pak",
        "pyopenjtalk/open_jtalk_dic_utf_8-1.11/sys.dic",
        "assets/fonts/851tegakizatsu.TTF",
        "assets/dictionaries/asr_terms.official.json",
        "librosa/__init__.pyi",
        "style_bert_vits2/models/infer.py",
        "soundcard/coreaudio.py.h",
        "soundcard/mediafoundation.py.h",
        "soundcard/pulseaudio.py.h",
    ]

    for dest in kept:
        assert should_keep_runtime_entry(dest, f"C:/venv/Lib/site-packages/{dest}") is True


def test_runtime_filter_only_exempts_soundcard_runtime_cffi_headers() -> None:
    assert should_keep_runtime_entry("soundcard/mediafoundation.py.h") is True
    assert should_keep_runtime_entry("soundcard/include/private.h") is False
    assert should_keep_runtime_entry("other_package/mediafoundation.py.h") is False


def test_runtime_filter_limits_qt_translations_to_app_locales() -> None:
    assert should_keep_runtime_entry("PySide6/translations/qtbase_de.qm") is False
    assert should_keep_runtime_entry("PySide6/translations/qtbase_ja.qm") is True
    assert should_keep_runtime_entry("PySide6/translations/qtbase_ko.qm") is True
    assert should_keep_runtime_entry("PySide6/translations/qtbase_ru.qm") is True
    assert should_keep_runtime_entry("PySide6/translations/qtbase_zh_CN.qm") is True
    assert should_keep_runtime_entry("PySide6/translations/qtbase_zh_TW.qm") is True


def test_runtime_filter_limits_qt_webengine_locale_packs_to_app_locales() -> None:
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/de.pak") is False
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/fr.pak") is False
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/en-US.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/en-GB.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/ja.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/ko.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/ru.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/zh-CN.pak") is True
    assert should_keep_runtime_entry("PySide6/translations/qtwebengine_locales/zh-TW.pak") is True


def test_filter_runtime_entries_preserves_toc_shape() -> None:
    entries = [
        ("torch/lib/dnnl.lib", "C:/venv/torch/lib/dnnl.lib", "BINARY"),
        ("torch/lib/torch_cpu.dll", "C:/venv/torch/lib/torch_cpu.dll", "BINARY"),
    ]

    assert filter_runtime_entries(entries) == [
        ("torch/lib/torch_cpu.dll", "C:/venv/torch/lib/torch_cpu.dll", "BINARY")
    ]


def test_hidden_import_filter_removes_tests_and_keeps_runtime_modules() -> None:
    removed = [
        "scipy.linalg.tests.test_basic",
        "torch.testing._internal.opinfo",
        "transformers.testing_utils",
        "typeguard._pytest_plugin",
        "google.protobuf.internal.testing_refleaks",
        "cn2an.transform_test",
        "pypinyin.__pyinstaller.hook-pypinyin",
        "pytest",
    ]
    kept = [
        "style_bert_vits2.tts_model",
        "transformers.pytorch_utils",
        "PySide6.QtWebEngineCore",
        "src.asr.sensevoice_model_manager",
        "google.protobuf.message",
    ]

    for module_name in removed:
        assert should_keep_hidden_import(module_name) is False
    for module_name in kept:
        assert should_keep_hidden_import(module_name) is True

    assert filter_hiddenimports(removed + kept + kept) == kept
