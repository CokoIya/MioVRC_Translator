from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QScrollArea, QSlider

from src.ui_qt.realtime_tweaks_panel import (
    PANEL_MAX_SIZE,
    PANEL_MIN_SIZE,
    PANEL_SIZE,
    RealtimeTweaksPanel,
)
from src.utils.i18n import tr
from src.utils.ui_config import backend_config_source_key, get_backend_label


def _quick_switch_config() -> dict:
    return {
        "translation": {
            "backend": "qianwen",
            "output_format": "translated_with_original",
            "asr_rewrite_style": "off",
            "rewrite_typed_text": False,
            "qianwen": {"model": "custom-hot-switch-model"},
        },
        "tts": {
            "engine": "edge",
            "edge": {"voice": "en-US-AriaNeural"},
        },
        "audio": {"denoise_strength": 0.35},
    }


def test_realtime_tweaks_panel_is_bounded_and_uses_project_icon_assets(qtbot):
    panel = RealtimeTweaksPanel(None, _quick_switch_config(), "en", "light")
    qtbot.addWidget(panel)

    flags = panel.windowFlags()
    assert not bool(flags & Qt.WindowType.WindowStaysOnTopHint)
    assert bool(flags & Qt.WindowType.FramelessWindowHint)
    assert panel.size().width() == PANEL_SIZE[0]
    assert panel.size().height() == PANEL_SIZE[1]
    assert panel.minimumSize().width() == PANEL_MIN_SIZE[0]
    assert panel.minimumSize().height() == PANEL_MIN_SIZE[1]
    assert panel.maximumSize().width() == PANEL_MAX_SIZE[0]
    assert panel.maximumSize().height() == PANEL_MAX_SIZE[1]
    assert panel.findChild(QScrollArea) is not None
    assert panel.findChild(QSlider) is not None
    assert panel.findChild(QCheckBox) is not None
    assert not panel._close_btn.icon().isNull()
    assert "chevron-down-muted.svg" in panel.styleSheet()
    assert "slider-thumb.svg" in panel.styleSheet()
    assert "#quickSwitchBody" in panel.styleSheet()
    assert panel._icon_labels
    assert all(not label.pixmap().isNull() for label, _filename, _strong in panel._icon_labels)


def test_realtime_tweaks_panel_only_exposes_quick_switch_controls(qtbot):
    changes: list[tuple[str, object]] = []
    panel = RealtimeTweaksPanel(
        None,
        _quick_switch_config(),
        "en",
        "dark",
        lambda key, value: changes.append((key, value)),
    )
    qtbot.addWidget(panel)

    assert set(panel._combos) == {
        "translation_provider",
        "translation_model",
        "output_format",
        "tts_engine",
        "tts_language",
        "tts_voice",
        "asr_rewrite_style",
    }
    assert set(panel._toggles) == {
        "rewrite_typed_text",
        "original_only_read_translation_wait_for_tts",
    }
    assert panel._toggles["rewrite_typed_text"].isChecked() is False
    assert panel._toggles[
        "original_only_read_translation_wait_for_tts"
    ].isHidden() is True
    assert "frieren" in panel._combo_reverse["asr_rewrite_style"]
    assert "language_exchange" in panel._combo_reverse["asr_rewrite_style"]
    assert (
        "sunny_popular_honor_student"
        in panel._combo_reverse["asr_rewrite_style"]
    )
    assert not hasattr(panel, "mic_gain_slider")
    assert not hasattr(panel, "manual_close_toggle")
    assert panel._noise_slider is not None
    assert panel._noise_slider.value() == 35

    model_combo = panel._combos["translation_model"]
    assert model_combo.count() >= 2
    model_combo.setCurrentIndex(1)

    assert changes[-1] == ("translation_model", model_combo.itemText(1))

    panel._noise_slider.setValue(60)

    assert changes[-1] == ("noise_reduction", 0.6)

    panel._toggles["rewrite_typed_text"].setChecked(True)

    assert changes[-1] == ("rewrite_typed_text", True)


def test_realtime_tweaks_panel_shows_tts_wait_toggle_only_for_original_read_mode(qtbot):
    config = _quick_switch_config()
    config["translation"]["output_format"] = "original_only_read_translation"
    changes: list[tuple[str, object]] = []
    panel = RealtimeTweaksPanel(
        None,
        config,
        "en",
        "dark",
        lambda key, value: changes.append((key, value)),
    )
    qtbot.addWidget(panel)

    toggle = panel._toggles["original_only_read_translation_wait_for_tts"]
    assert toggle.isHidden() is False
    assert toggle.isChecked() is True

    toggle.setChecked(False)
    assert changes[-1] == ("original_only_read_translation_wait_for_tts", False)

    config["translation"]["output_format"] = "translated_only"
    panel._refresh_controls()
    assert toggle.isHidden() is True


def test_realtime_tweaks_panel_localizes_shared_rewrite_controls(qtbot):
    panel = RealtimeTweaksPanel(None, _quick_switch_config(), "en", "dark")
    qtbot.addWidget(panel)

    for language in ("zh-CN", "en", "ja", "ru", "ko"):
        panel.update_language(language)
        assert panel._combo_labels["asr_rewrite_style"].text() == tr(
            language,
            "quick_switch_asr_rewrite_style",
        )
        assert panel._toggles["rewrite_typed_text"].text() == tr(
            language,
            "quick_switch_rewrite_typed_text",
        )
        assert panel._toggles[
            "original_only_read_translation_wait_for_tts"
        ].text() == tr(
            language,
            "quick_switch_original_only_read_translation_wait_for_tts",
        )


def test_realtime_tweaks_panel_hot_switches_translation_provider(qtbot):
    changes: list[tuple[str, object]] = []
    panel = RealtimeTweaksPanel(
        None,
        _quick_switch_config(),
        "en",
        "dark",
        lambda key, value: changes.append((key, value)),
    )
    qtbot.addWidget(panel)

    provider_combo = panel._combos["translation_provider"]
    assert provider_combo.currentText() == "Qwen"

    openai_index = provider_combo.findText("GPT")
    assert openai_index >= 0
    provider_combo.setCurrentIndex(openai_index)

    assert changes[-1] == ("translation_provider", "openai")


def test_realtime_tweaks_panel_localizes_backend_qualifiers(qtbot):
    config = _quick_switch_config()
    config["translation"]["backend"] = "openai_compatible"
    panel = RealtimeTweaksPanel(None, config, "ru", "dark")
    qtbot.addWidget(panel)

    provider_combo = panel._combos["translation_provider"]
    # The single relay backend ("Relay Compatible") carries a localized label.
    assert provider_combo.currentText() == get_backend_label("openai_compatible", "ru")
    assert provider_combo.currentText() != get_backend_label("openai_compatible", "en")
    # The merged GPT/Grok/Claude-compatible backends are not offered separately.
    codes = set(panel._combo_codes["translation_provider"].values())
    assert "openai_compatible" in codes
    assert not {"grok_compatible", "anthropic_compatible"} & codes


def test_quick_panel_lists_voicevox_voices_from_the_running_engine(qtbot):
    """VOICEVOX voices were simply missing from the quick panel.

    Its catalog comes from a local service, and the panel only knew how to
    read the two engines whose voices are constants in the source tree, so the
    box was empty for VOICEVOX while settings showed it correctly.
    """

    from types import SimpleNamespace

    config = _quick_switch_config()
    config["tts"] = {"engine": "voicevox", "voicevox": {"voice": "2"}}
    asked: list[str] = []

    def live_voices(engine):
        asked.append(engine)
        return [
            SimpleNamespace(id="2", name="四国めたん（ノーマル）"),
            SimpleNamespace(id="3", name="ずんだもん（ノーマル）"),
        ]

    panel = RealtimeTweaksPanel(
        None,
        config,
        "en",
        "dark",
        lambda key, value: None,
        live_voices=live_voices,
    )
    qtbot.addWidget(panel)

    assert asked == ["voicevox"]
    combo = panel._combos["tts_voice"]
    assert combo.count() == 2
    assert combo.currentText() == "四国めたん（ノーマル）"


def test_quick_panel_never_opens_its_own_connection_for_voices(qtbot):
    """Without a host provider the panel returns nothing rather than blocking.

    A synchronous call to a local speech service freezes the window whenever
    that service is not running.
    """

    config = _quick_switch_config()
    config["tts"] = {"engine": "voicevox", "voicevox": {"voice": "2"}}

    panel = RealtimeTweaksPanel(None, config, "en", "dark", lambda key, value: None)
    qtbot.addWidget(panel)

    combo = panel._combos["tts_voice"]

    # The box shows its "no option" placeholder and offers no real voice.
    assert combo.isEnabled() is False
    assert panel._combo_codes["tts_voice"] == {}


def test_quick_panel_offers_only_engines_that_are_ready(qtbot):
    """Switching to an engine that still needs setup is not a quick switch."""

    config = _quick_switch_config()
    config["tts"] = {"engine": "qwen_tts", "qwen_tts": {"voice": "Cherry"}}

    panel = RealtimeTweaksPanel(
        None,
        config,
        "en",
        "dark",
        lambda key, value: None,
        available_engines=lambda: ["qwen_tts", "voicevox"],
    )
    qtbot.addWidget(panel)

    combo = panel._combos["tts_engine"]
    codes = set(panel._combo_codes["tts_engine"].values())

    assert combo.isVisible() or combo.count() == 2
    assert codes == {"qwen_tts", "voicevox"}


def test_quick_panel_hides_the_engine_picker_when_only_one_is_ready(qtbot):
    config = _quick_switch_config()
    config["tts"] = {"engine": "qwen_tts", "qwen_tts": {"voice": "Cherry"}}

    panel = RealtimeTweaksPanel(
        None,
        config,
        "en",
        "dark",
        lambda key, value: None,
        available_engines=lambda: ["qwen_tts"],
    )
    qtbot.addWidget(panel)

    assert panel._combos["tts_engine"].isVisibleTo(panel) is False


RELAY_URL = "https://relay.example.com/v1"
RELAY_MODELS = ["gpt-6-luna", "claude-opus-5", "deepseek-flash"]


def _relay_config(*, source: str | None = None) -> dict:
    relay_cfg = {
        "api_key": "sk-relay",
        "base_url": RELAY_URL,
        "model": "gpt-6-luna",
        "remote_models": list(RELAY_MODELS),
    }
    relay_cfg["remote_models_source"] = (
        backend_config_source_key("openai_compatible", relay_cfg) if source is None else source
    )
    config = _quick_switch_config()
    config["translation"]["backend"] = "openai_compatible"
    config["translation"]["openai_compatible"] = relay_cfg
    return config


def _combo_items(combo) -> list[str]:
    return [combo.itemText(index) for index in range(combo.count())]


def test_quick_panel_offers_the_model_list_read_from_the_provider(qtbot):
    """The relay has no built-in presets; only the fetched list has choices."""

    panel = RealtimeTweaksPanel(None, _relay_config(), "en", "dark")
    qtbot.addWidget(panel)

    combo = panel._combos["translation_model"]
    assert _combo_items(combo) == RELAY_MODELS
    assert combo.currentText() == "gpt-6-luna"


def test_quick_panel_ignores_a_model_list_read_from_another_endpoint(qtbot):
    stale = _relay_config(source="openai_compatible|https://old-relay.example.com/v1|openai")
    panel = RealtimeTweaksPanel(None, stale, "en", "dark")
    qtbot.addWidget(panel)

    assert _combo_items(panel._combos["translation_model"]) == ["gpt-6-luna"]


def _dashboard_window(monkeypatch, config: dict):
    from src.ui_qt.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window._config = config
    switched: list[tuple[str, object]] = []
    monkeypatch.setattr(
        window, "_on_quick_switch_changed", lambda key, value: switched.append((key, value))
    )
    monkeypatch.setattr(window, "_refresh_vr_dashboard", lambda: None)
    return MainWindow, window, switched




VOICE_30 = "qwen-audio-3.0-tts-flash-mine-0a1b2c"
VOICE_31 = "qwen-audio-3.1-tts-flash-mine-3d4e5f"
VOICE_REJECTED = "qwen-audio-3.0-tts-flash-bad-6g7h8i"


def _clone_voices(*voice_ids: str) -> list[dict]:
    voices = {
        VOICE_30: {
            "voice_id": VOICE_30,
            "display_name": "Mine 3.0",
            "target_model": "qwen-audio-3.0-tts-flash",
            "region": "china_mainland",
            "status": "OK",
        },
        VOICE_31: {
            "voice_id": VOICE_31,
            "display_name": "Mine 3.1",
            "target_model": "qwen-audio-3.1-tts-flash",
            "region": "china_mainland",
            "status": "OK",
        },
        VOICE_REJECTED: {
            "voice_id": VOICE_REJECTED,
            "display_name": "Rejected",
            "target_model": "qwen-audio-3.0-tts-flash",
            "region": "china_mainland",
            "status": "UNDEPLOYED",
        },
    }
    return [dict(voices[voice_id]) for voice_id in voice_ids]


def test_quick_panel_offers_only_cloned_voices_the_current_model_can_speak(qtbot):
    """A sync keeps voices of other Qwen-Audio models; they cannot speak here."""

    config = _quick_switch_config()
    config["tts"] = {
        "engine": "qwen_vc",
        "qwen_vc": {
            "api_key": "sk-test",
            "region": "china_mainland",
            "model": "qwen-audio-3.0-tts-flash",
            "voice": VOICE_30,
            "custom_voices": _clone_voices(VOICE_31, VOICE_30, VOICE_REJECTED),
        },
    }
    panel = RealtimeTweaksPanel(None, config, "en", "dark", lambda key, value: None)
    qtbot.addWidget(panel)

    combo = panel._combos["tts_voice"]
    assert _combo_items(combo) == ["Mine 3.0"]
    assert panel._combo_codes["tts_voice"] == {"Mine 3.0": VOICE_30}


def test_quick_switch_offers_cloning_only_with_a_usable_voice(monkeypatch):
    from src.ui_qt.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    vc_cfg = {
        "api_key": "sk-test",
        "region": "china_mainland",
        "model": "qwen-audio-3.0-tts-flash",
        "custom_voices": _clone_voices(VOICE_31, VOICE_REJECTED),
    }
    window._config = {"tts": {"engine": "edge", "qwen_vc": vc_cfg}}

    assert MainWindow._tts_engine_is_ready(window, "qwen_vc") is False

    vc_cfg["custom_voices"] = _clone_voices(VOICE_31, VOICE_30)
    assert MainWindow._tts_engine_is_ready(window, "qwen_vc") is True

    vc_cfg["api_key"] = ""
    assert MainWindow._tts_engine_is_ready(window, "qwen_vc") is False
