from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QScrollArea, QSlider

from src.ui_qt.realtime_tweaks_panel import (
    PANEL_MAX_SIZE,
    PANEL_MIN_SIZE,
    PANEL_SIZE,
    RealtimeTweaksPanel,
)
from src.utils.i18n import tr


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
    assert provider_combo.currentText() == "GPT-совместимый сервис"


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
