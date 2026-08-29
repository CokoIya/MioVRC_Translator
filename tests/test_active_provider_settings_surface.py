from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from src.ui_qt import settings_window as settings_module
from src.ui_qt.settings_window import SettingsWindow
from src.utils.i18n import tr
from src.utils.ui_config import get_backend_label


_REQUIRED_PROVIDER_PATHS = {
    "openai",
    "openai_compatible",
    "xai",
    "grok_compatible",
}


class _DummyTTS:
    def get_available_voices(self):
        return []


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _config(backend: str = "openai_compatible") -> dict:
    return {
        "ui": {"language": "en", "main_window_theme": "system"},
        "translation": {
            "backend": backend,
            "target_language": "ja",
            "source_language": "auto",
            "output_format": "translated_with_original",
            backend: {
                "api_key": "test-key",
                "base_url": "https://relay.example/v1/",
                "model": "Custom/Model:Preview-001",
                "timeout_s": 21,
                "connect_timeout_s": 2.5,
                "pool_timeout_s": 1.5,
                "read_timeout_s": 31,
                "write_timeout_s": 12,
                "wall_timeout_s": 45,
                "max_retries": 0,
                "custom_headers": {},
                "streaming": True,
            },
        },
        "asr": {"engine": "sensevoice-small"},
        "tts": {"engine": "edge", "rate": 1.0, "volume": 0.8},
        "vrc_listen": {
            "enabled": False,
            "source_language": "auto",
            "target_language": "zh",
        },
        "hotkeys": {"mic_mute": "Ctrl+Alt+F2"},
        "text_input_window": {"hotkey": "Alt+X"},
        "osc": {"avatar_sync": {"enabled": False, "params": {}}},
    }


def _api_tts_config(engine: str = "qwen_tts") -> dict:
    config = _config()
    engine_config = {
        "api_key": "tts-key",
        "region": "singapore",
        "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
        "model": "qwen3-tts-flash",
        "voice": "Cherry",
        "timeout_seconds": 18,
        "connect_timeout_seconds": 2.5,
        "read_timeout_seconds": 17,
        "wall_timeout_seconds": 35,
        "max_retries": 0,
    }
    if engine == "mimo_tts":
        engine_config.update(
            {
                "region": "global",
                "base_url": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5-tts",
                "voice": "mimo_default",
            }
        )
        engine_config.pop("connect_timeout_seconds")
        engine_config.pop("read_timeout_seconds")
        engine_config.pop("wall_timeout_seconds")
    config["tts"] = {
        "enabled": True,
        "engine": engine,
        "rate": 1.0,
        "volume": 0.8,
        engine: engine_config,
    }
    return config


def _tabbed_provider_config() -> dict:
    config = _config("openai")
    config["translation"]["openai"]["api_key"] = "old-openai"
    config["translation"]["openai_compatible"] = {
        "api_key": "old-relay",
        "base_url": "https://relay.example/v1/",
        "model": "relay-model",
    }
    return config


def _patch_dialog_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(settings_module.AudioRecorder, "list_devices", lambda: [])
    monkeypatch.setattr(settings_module, "_list_desktop_output_devices", lambda: [])
    monkeypatch.setattr(settings_module, "find_best_virtual_output_device", lambda: None)
    monkeypatch.setattr(
        settings_module,
        "create_tts_engine",
        lambda _engine, config=None: _DummyTTS(),
    )
    monkeypatch.setattr(
        settings_module,
        "first_missing_required_credential",
        lambda _cfg, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(settings_module.config_manager, "save_config", lambda _cfg: None)
    monkeypatch.setattr(
        settings_module,
        "dictionary_status",
        lambda: {"layers": [], "user_path": ""},
    )


def _open_provider_page(dialog: SettingsWindow, backend: str) -> None:
    dialog.select_page("api_config")
    dialog._backend_var.set(get_backend_label(backend, dialog._ui_lang))
    dialog._render_backend_fields()


def _dispose(dialog: SettingsWindow, qapp: QApplication) -> None:
    dialog.reject()
    dialog.deleteLater()
    qapp.processEvents()


def test_active_settings_surface_exposes_official_and_compatible_paths(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    dialog = SettingsWindow(None, _config())
    try:
        dialog.select_page("api_config")
        assert _REQUIRED_PROVIDER_PATHS <= set(dialog._backend_codes.values())
        expected_timeout_labels = {
            tr(dialog._ui_lang, key)
            for key in (
                "connect_timeout",
                "pool_timeout",
                "read_timeout",
                "write_timeout",
                "wall_timeout",
            )
        }
        for backend in sorted(_REQUIRED_PROVIDER_PATHS):
            _open_provider_page(dialog, backend)
            rendered_labels = {
                label.text()
                for label in dialog._backend_fields_frame.findChildren(QLabel)
            }
            assert expected_timeout_labels <= rendered_labels, backend
    finally:
        _dispose(dialog, qapp)


def test_active_official_provider_model_ids_are_editable(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    dialog = SettingsWindow(None, _config("openai"))
    try:
        for backend in ("openai", "xai"):
            _open_provider_page(dialog, backend)
            model_combo = next(
                (
                    combo
                    for combo in dialog._backend_fields_frame.findChildren(QComboBox)
                    if combo.isEditable()
                ),
                None,
            )
            assert model_combo is not None
            assert model_combo.isEditable(), backend
    finally:
        _dispose(dialog, qapp)


def test_active_settings_surface_loads_and_tests_granular_timeouts(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    config = _config("openai_compatible")
    dialog = SettingsWindow(None, config)
    try:
        _open_provider_page(dialog, "openai_compatible")

        assert dialog._backend_model_var.value() == "Custom/Model:Preview-001"
        assert dialog._backend_base_url_var.value() == "https://relay.example/v1/"
        assert dialog._backend_connect_timeout_var.value() == "2.5"
        assert dialog._backend_pool_timeout_var.value() == "1.5"
        assert dialog._backend_read_timeout_var.value() == "31"
        assert dialog._backend_write_timeout_var.value() == "12"
        assert dialog._backend_wall_timeout_var.value() == "45"

        dialog._backend_connect_timeout_var.set("3.25")
        dialog._backend_pool_timeout_var.set("")
        dialog._backend_read_timeout_var.set("40")
        dialog._backend_write_timeout_var.set("13")
        dialog._backend_wall_timeout_var.set("55")
        snapshot = dialog._translation_connection_config()["translation"][
            "openai_compatible"
        ]

        assert snapshot["base_url"] == "https://relay.example/v1/"
        assert snapshot["model"] == "Custom/Model:Preview-001"
        assert snapshot["connect_timeout_s"] == 3.25
        assert "pool_timeout_s" not in snapshot
        assert snapshot["read_timeout_s"] == 40.0
        assert snapshot["write_timeout_s"] == 13.0
        assert snapshot["wall_timeout_s"] == 55.0
    finally:
        _dispose(dialog, qapp)


@pytest.mark.parametrize(
    "backend",
    ("openai_compatible", "grok_compatible"),
)
def test_active_relay_settings_reject_unsafe_public_http_base_urls(
    qapp,
    monkeypatch,
    backend,
):
    _patch_dialog_dependencies(monkeypatch)
    dialog = SettingsWindow(None, _config(backend))
    try:
        _open_provider_page(dialog, backend)
        dialog._backend_base_url_var.set("http://public.example/v1")

        with pytest.raises(ValueError):
            dialog._translation_connection_config()
    finally:
        _dispose(dialog, qapp)


def test_active_settings_save_persists_and_clears_timeout_overrides(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    config = _config("grok_compatible")
    dialog = SettingsWindow(None, config)
    try:
        _open_provider_page(dialog, "grok_compatible")
        dialog._backend_connect_timeout_var.set("4")
        dialog._backend_pool_timeout_var.set("")
        dialog._backend_read_timeout_var.set("41")
        dialog._backend_write_timeout_var.set("14")
        dialog._backend_wall_timeout_var.set("56")
        dialog._save()

        saved = config["translation"]["grok_compatible"]
        assert saved["model"] == "Custom/Model:Preview-001"
        assert saved["base_url"] == "https://relay.example/v1/"
        assert saved["connect_timeout_s"] == 4.0
        assert "pool_timeout_s" not in saved
        assert saved["read_timeout_s"] == 41.0
        assert saved["write_timeout_s"] == 14.0
        assert saved["wall_timeout_s"] == 56.0
    finally:
        _dispose(dialog, qapp)


def test_active_provider_switches_preserve_and_save_each_unsaved_draft(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    config = _config("openai_compatible")
    config["translation"]["grok_compatible"] = {
        "api_key": "grok-old",
        "base_url": "https://grok-relay.example/v1/",
        "model": "grok-old",
        "timeout_s": 20,
        "connect_timeout_s": 2,
        "pool_timeout_s": 1,
        "read_timeout_s": 30,
        "write_timeout_s": 10,
        "wall_timeout_s": 40,
        "max_retries": 0,
        "custom_headers": {},
        "streaming": False,
    }
    dialog = SettingsWindow(None, config)
    try:
        _open_provider_page(dialog, "openai_compatible")
        dialog._backend_api_key_var.set("openai-unsaved")
        dialog._backend_base_url_var.set("https://new-openai-relay.example/v1/")
        dialog._backend_model_var.set("Org/Exact-GPT:Preview")
        dialog._backend_connect_timeout_var.set("3.5")

        _open_provider_page(dialog, "grok_compatible")
        dialog._backend_api_key_var.set("grok-unsaved")
        dialog._backend_base_url_var.set("https://new-grok-relay.example/v1/")
        dialog._backend_model_var.set("grok-custom-2026-07")
        dialog._backend_wall_timeout_var.set("58")

        _open_provider_page(dialog, "openai_compatible")
        assert dialog._backend_api_key_var.value() == "openai-unsaved"
        assert (
            dialog._backend_base_url_var.value()
            == "https://new-openai-relay.example/v1/"
        )
        assert dialog._backend_model_var.value() == "Org/Exact-GPT:Preview"
        assert dialog._backend_connect_timeout_var.value() == "3.5"

        dialog._save()

        openai_saved = config["translation"]["openai_compatible"]
        grok_saved = config["translation"]["grok_compatible"]
        assert openai_saved["api_key"] == "openai-unsaved"
        assert openai_saved["model"] == "Org/Exact-GPT:Preview"
        assert openai_saved["connect_timeout_s"] == 3.5
        assert grok_saved["api_key"] == "grok-unsaved"
        assert (
            grok_saved["base_url"]
            == "https://new-grok-relay.example/v1/"
        )
        assert grok_saved["model"] == "grok-custom-2026-07"
        assert grok_saved["wall_timeout_s"] == 58.0
    finally:
        _dispose(dialog, qapp)


def test_active_language_rebuild_preserves_unsaved_provider_fields(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    dialog = SettingsWindow(None, _config("grok_compatible"))
    try:
        _open_provider_page(dialog, "grok_compatible")
        dialog._backend_api_key_var.set("grok-unsaved")
        dialog._backend_base_url_var.set("https://grok-relay.example/v1/")
        dialog._backend_model_var.set("grok/custom:latest")
        dialog._backend_timeout_var.set("27")
        dialog._backend_custom_headers_var.set('{"X-Relay":"player"}')

        dialog._apply_ui_language("ja", emit_signal=False)

        assert dialog._backend_code() == "grok_compatible"
        assert dialog._backend_api_key_var.value() == "grok-unsaved"
        assert dialog._backend_base_url_var.value() == "https://grok-relay.example/v1/"
        assert dialog._backend_model_var.value() == "grok/custom:latest"
        assert dialog._backend_timeout_var.value() == "27"
        assert (
            dialog._backend_custom_headers_var.value()
            == '{"X-Relay":"player"}'
        )
    finally:
        _dispose(dialog, qapp)


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        (
            "qwen_tts",
            {
                "timeout_seconds": "18",
                "connect_timeout_seconds": "2.5",
                "read_timeout_seconds": "17",
                "wall_timeout_seconds": "35",
            },
        ),
        (
            "mimo_tts",
            {
                "timeout_seconds": "18",
                "connect_timeout_seconds": "18",
                "read_timeout_seconds": "18",
                "wall_timeout_seconds": "45.0",
            },
        ),
    ],
)
def test_active_api_tts_timeout_controls_load_and_reach_test_config(
    qapp,
    monkeypatch,
    engine,
    expected,
):
    _patch_dialog_dependencies(monkeypatch)
    dialog = SettingsWindow(None, _api_tts_config(engine))
    try:
        dialog.select_page("api_config")
        values = {
            "timeout_seconds": dialog._tts_api_timeout_var.value(),
            "connect_timeout_seconds": dialog._tts_api_connect_timeout_var.value(),
            "read_timeout_seconds": dialog._tts_api_read_timeout_var.value(),
            "wall_timeout_seconds": dialog._tts_api_wall_timeout_var.value(),
        }
        assert values == expected

        labels = {
            label.text()
            for label in dialog._pages["api_config"].findChildren(QLabel)
        }
        assert {
            tr("en", "request_timeout"),
            tr("en", "connect_timeout"),
            tr("en", "read_timeout"),
            tr("en", "wall_timeout"),
        } <= labels
        assert tr("en", "tts_api_timeout_hint") in labels

        test_config = dialog._current_tts_engine_config(engine)
        assert {
            key: test_config[key]
            for key in (
                "timeout_seconds",
                "connect_timeout_seconds",
                "read_timeout_seconds",
                "wall_timeout_seconds",
            )
        } == {key: float(value) for key, value in expected.items()}
    finally:
        _dispose(dialog, qapp)


def test_active_api_tts_timeout_save_and_bounds(qapp, monkeypatch):
    _patch_dialog_dependencies(monkeypatch)
    config = _api_tts_config("qwen_tts")
    dialog = SettingsWindow(None, config)
    try:
        dialog.select_page("api_config")
        for attribute, invalid in (
            ("_tts_api_timeout_var", "2.99"),
            ("_tts_api_timeout_var", "120.01"),
            ("_tts_api_connect_timeout_var", "0.24"),
            ("_tts_api_connect_timeout_var", "120.01"),
            ("_tts_api_read_timeout_var", "0.24"),
            ("_tts_api_read_timeout_var", "120.01"),
            ("_tts_api_wall_timeout_var", "2.99"),
            ("_tts_api_wall_timeout_var", "300.01"),
        ):
            variable = getattr(dialog, attribute)
            original = variable.value()
            variable.set(invalid)
            with pytest.raises(ValueError):
                dialog._tts_api_timeout_values()
            variable.set(original)

        dialog._tts_api_timeout_var.set("23")
        dialog._tts_api_connect_timeout_var.set("0.25")
        dialog._tts_api_read_timeout_var.set("120")
        dialog._tts_api_wall_timeout_var.set("300")
        dialog._save()

        saved = config["tts"]["qwen_tts"]
        assert saved["timeout_seconds"] == 23.0
        assert saved["connect_timeout_seconds"] == 0.25
        assert saved["read_timeout_seconds"] == 120.0
        assert saved["wall_timeout_seconds"] == 300.0
    finally:
        _dispose(dialog, qapp)


def test_active_api_tts_engine_switches_preserve_and_save_each_draft(
    qapp,
    monkeypatch,
):
    _patch_dialog_dependencies(monkeypatch)
    config = _api_tts_config("qwen_tts")
    config["tts"]["mimo_tts"] = {
        "api_key": "mimo-old",
        "region": "global",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-tts",
        "voice": "mimo_default",
        "timeout_seconds": 19,
        "connect_timeout_seconds": 3,
        "read_timeout_seconds": 18,
        "wall_timeout_seconds": 46,
    }
    dialog = SettingsWindow(None, config)
    try:
        dialog._tts_api_key_var.set("qwen-unsaved")
        qwen_custom_region = next(
            label
            for label, code in dialog._tts_api_region_codes.items()
            if code == "custom"
        )
        dialog._tts_api_region_var.set(qwen_custom_region)
        dialog._tts_api_base_url_var.set("https://qwen-tts-relay.example/api/v1/")
        dialog._tts_api_model_var.set("qwen3-tts-custom-player")
        dialog._tts_api_timeout_var.set("23")
        dialog._tts_api_connect_timeout_var.set("2.75")
        dialog._tts_api_read_timeout_var.set("44")
        dialog._tts_api_wall_timeout_var.set("70")

        dialog._on_tts_engine_changed("mimo_tts")
        assert dialog._tts_api_key_var.value() == "mimo-old"
        dialog._tts_api_key_var.set("mimo-unsaved")
        mimo_custom_region = next(
            label
            for label, code in dialog._tts_api_region_codes.items()
            if code == "custom"
        )
        dialog._tts_api_region_var.set(mimo_custom_region)
        dialog._tts_api_base_url_var.set("https://mimo-tts-relay.example/v1/")
        dialog._tts_api_model_var.set("mimo/custom-model:preview")
        dialog._tts_api_timeout_var.set("24")
        dialog._tts_api_connect_timeout_var.set("3.25")
        dialog._tts_api_read_timeout_var.set("45")
        dialog._tts_api_wall_timeout_var.set("71")

        dialog._on_tts_engine_changed("qwen_tts")
        assert dialog._tts_api_key_var.value() == "qwen-unsaved"
        assert dialog._selected_tts_api_region() == "custom"
        assert (
            dialog._tts_api_base_url_var.value()
            == "https://qwen-tts-relay.example/api/v1/"
        )
        assert dialog._tts_api_model_var.value() == "qwen3-tts-custom-player"
        assert dialog._tts_api_timeout_var.value() == "23"
        assert dialog._tts_api_connect_timeout_var.value() == "2.75"
        assert dialog._tts_api_read_timeout_var.value() == "44"
        assert dialog._tts_api_wall_timeout_var.value() == "70"

        dialog._save()

        qwen_saved = config["tts"]["qwen_tts"]
        mimo_saved = config["tts"]["mimo_tts"]
        assert qwen_saved["api_key"] == "qwen-unsaved"
        assert qwen_saved["region"] == "custom"
        assert qwen_saved["base_url"] == "https://qwen-tts-relay.example/api/v1"
        assert qwen_saved["model"] == "qwen3-tts-custom-player"
        assert qwen_saved["timeout_seconds"] == 23.0
        assert qwen_saved["connect_timeout_seconds"] == 2.75
        assert qwen_saved["read_timeout_seconds"] == 44.0
        assert qwen_saved["wall_timeout_seconds"] == 70.0
        assert mimo_saved["api_key"] == "mimo-unsaved"
        assert mimo_saved["region"] == "custom"
        assert mimo_saved["base_url"] == "https://mimo-tts-relay.example/v1"
        assert mimo_saved["model"] == "mimo/custom-model:preview"
        assert mimo_saved["timeout_seconds"] == 24.0
        assert mimo_saved["connect_timeout_seconds"] == 3.25
        assert mimo_saved["read_timeout_seconds"] == 45.0
        assert mimo_saved["wall_timeout_seconds"] == 71.0
    finally:
        _dispose(dialog, qapp)
