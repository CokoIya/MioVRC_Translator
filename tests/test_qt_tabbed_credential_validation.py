from __future__ import annotations

from src.ui_qt.settings import api_models_tab, quick_setup_tab, settings_window_tabbed
from src.ui_qt.settings.api_models_tab import APIModelsTab
from src.ui_qt.settings.quick_setup_tab import QuickSetupTab
from src.ui_qt.settings.settings_window_tabbed import SettingsWindowTabbed


def _translation_config(*, backend: str = "openai", openai: str = "", anthropic: str = "") -> dict:
    return {
        "ui": {"language": "en"},
        "translation": {
            "backend": backend,
            "source_language": "auto",
            "target_language": "ja",
            "openai": {"api_key": openai},
            "anthropic": {"api_key": anthropic},
            "deepseek": {"api_key": ""},
            "gemini": {"api_key": ""},
            "qianwen": {"api_key": "", "region": "singapore"},
        },
        "tts": {"engine": "edge", "enabled": False},
        "vrc_listen": {"enabled": False},
    }


def _recording_prompt(records: list):
    def show(_parent, missing, *, ui_language, open_settings=None, **_kwargs):
        records.append((missing, ui_language, open_settings))
        return False

    return show


def test_api_models_validates_provider_and_model_selection_but_not_load(
    qtbot,
    monkeypatch,
):
    prompts = []
    monkeypatch.setattr(
        api_models_tab,
        "show_missing_credential_prompt",
        _recording_prompt(prompts),
    )
    tab = APIModelsTab(_translation_config(openai="configured"), "en")
    qtbot.addWidget(tab)

    tab.load_config(_translation_config(openai="configured"))
    assert prompts == []

    tab._provider_combo.setCurrentIndex(tab._provider_combo.findData("anthropic"))
    assert [item[0].provider_id for item in prompts] == ["anthropic"]

    prompts.clear()
    tab._on_model_selected(0)
    assert [item[0].provider_id for item in prompts] == ["anthropic"]
    assert prompts[0][1] == "en"


def test_quick_setup_validates_new_provider_and_does_not_reuse_previous_key(
    qtbot,
    monkeypatch,
):
    prompts = []
    monkeypatch.setattr(
        quick_setup_tab,
        "show_missing_credential_prompt",
        _recording_prompt(prompts),
    )
    tab = QuickSetupTab(_translation_config(openai="openai-secret"), "en")
    qtbot.addWidget(tab)
    tab.load_config(
        {
            "ui_language": "en",
            "translation_provider": "openai",
            "api_key": "openai-secret",
        }
    )
    assert prompts == []

    tab._provider_combo.setCurrentIndex(tab._provider_combo.findData("anthropic"))

    assert tab._api_key_input.text() == ""
    assert [item[0].provider_id for item in prompts] == ["anthropic"]


def test_tabbed_connection_tests_validate_the_provider_without_network_calls(
    qtbot,
    monkeypatch,
):
    prompts = []
    monkeypatch.setattr(
        api_models_tab,
        "show_missing_credential_prompt",
        _recording_prompt(prompts),
    )
    tab = APIModelsTab(_translation_config(), "en")
    qtbot.addWidget(tab)
    tab.load_config(_translation_config())

    tab._test_openai()
    tab._test_anthropic()

    assert [item[0].provider_id for item in prompts] == ["openai", "anthropic"]


def test_tabbed_keyless_local_compatible_endpoint_skips_credential_prompt(
    qtbot,
    monkeypatch,
):
    prompts = []
    monkeypatch.setattr(
        api_models_tab,
        "show_missing_credential_prompt",
        _recording_prompt(prompts),
    )
    config = _translation_config(backend="openai_compatible")
    config["translation"]["openai_compatible"] = {
        "api_key": "",
        "base_url": "http://192.168.65.2:11434/v1",
        "model": "local-model",
    }
    tab = APIModelsTab(config, "en")
    qtbot.addWidget(tab)
    tab.load_config(config)

    assert tab._prompt_for_missing_credential("openai_compatible") is False
    assert prompts == []
    assert (
        tab.get_config()["translation"]["openai_compatible"]["base_url"]
        == "http://192.168.65.2:11434/v1"
    )


def test_tabbed_qwen_japan_region_preserves_workspace_endpoint(qtbot):
    config = _translation_config(backend="qianwen")
    config["translation"]["qianwen"] = {
        "api_key": "tokyo-key",
        "region": "japan",
        "base_url": (
            "https://ws-player.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1"
        ),
        "model": "workspace-enabled-model",
    }
    tab = APIModelsTab(config, "en")
    qtbot.addWidget(tab)
    tab.load_config(config)

    assert tab._qwen_base_url_input.isReadOnly() is False
    collected = tab.get_config()["translation"]["qianwen"]
    assert collected["region"] == "japan"
    assert collected["base_url"] == config["translation"]["qianwen"]["base_url"]


def test_tabbed_save_blocks_missing_key_and_action_opens_api_provider(
    qtbot,
    monkeypatch,
):
    dialog = SettingsWindowTabbed(_translation_config(), ui_language="en")
    qtbot.addWidget(dialog)
    saved = []
    focused = []

    monkeypatch.setattr(
        settings_window_tabbed.config_manager,
        "save_config",
        lambda _config: saved.append(True),
    )

    def show(_parent, missing, *, ui_language, open_settings=None, **_kwargs):
        assert ui_language == "en"
        assert missing.provider_id == "openai"
        assert open_settings is not None
        open_settings()
        return True

    monkeypatch.setattr(
        settings_window_tabbed,
        "show_missing_credential_prompt",
        show,
    )
    monkeypatch.setattr(
        dialog._api_models_tab,
        "focus_credential",
        lambda missing: focused.append(missing.provider_id),
    )

    dialog._on_save_clicked()

    assert saved == []
    assert dialog._saving is False
    assert dialog._save_btn.isEnabled()
    assert dialog._tabs.currentIndex() == 1
    assert focused == ["openai"]


def test_api_prompt_action_selects_and_focuses_matching_key_input(
    qtbot,
    monkeypatch,
):
    opened = []

    def open_api(missing):
        opened.append(missing.provider_id)

    def show(_parent, missing, *, ui_language, open_settings=None, **_kwargs):
        assert ui_language == "en"
        assert open_settings is not None
        open_settings()
        return True

    monkeypatch.setattr(api_models_tab, "show_missing_credential_prompt", show)
    tab = APIModelsTab(
        _translation_config(),
        "en",
        on_open_api_settings=open_api,
    )
    qtbot.addWidget(tab)
    tab.load_config(_translation_config())

    assert tab._prompt_for_missing_credential("anthropic") is True
    assert opened == ["anthropic"]


def test_tabbed_grok_provider_preserves_custom_model_and_relay_settings(
    qtbot,
    monkeypatch,
):
    prompts = []
    monkeypatch.setattr(
        api_models_tab,
        "show_missing_credential_prompt",
        _recording_prompt(prompts),
    )
    config = _translation_config(backend="grok_compatible")
    config["translation"]["grok_compatible"] = {
        "api_key": "relay-key",
        "base_url": "https://relay.example.com/openai/v1",
        "model": "Custom-GROK/router:model-001",
        "timeout_s": 21,
        "custom_headers": {"X-Tenant": "player-one"},
        "streaming": False,
    }
    tab = APIModelsTab(config, "en")
    qtbot.addWidget(tab)
    tab.load_config(config)

    assert tab._provider_combo.currentData() == "grok_compatible"
    assert tab._model_combo.currentText() == "Custom-GROK/router:model-001"
    assert tab._grok_url_input.text() == "https://relay.example.com/openai/v1"
    assert tab._grok_headers_input.text() == '{"X-Tenant":"player-one"}'
    assert tab._grok_streaming_check.isChecked() is False

    saved = tab.get_config()["translation"]["grok_compatible"]
    assert saved["model"] == "Custom-GROK/router:model-001"
    assert saved["base_url"] == "https://relay.example.com/openai/v1"
    assert saved["timeout_s"] == 21.0
    assert saved["custom_headers"] == {"X-Tenant": "player-one"}
    assert saved["streaming"] is False
    assert prompts == []
