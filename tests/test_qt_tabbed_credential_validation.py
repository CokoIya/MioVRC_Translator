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
    def show(_parent, missing, *, ui_language, open_settings=None):
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

    def show(_parent, missing, *, ui_language, open_settings=None):
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

    def show(_parent, missing, *, ui_language, open_settings=None):
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
