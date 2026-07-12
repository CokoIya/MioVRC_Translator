from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QSlider

from src.ui_qt.realtime_tweaks_panel import (
    PANEL_MAX_SIZE,
    PANEL_MIN_SIZE,
    PANEL_SIZE,
    RealtimeTweaksPanel,
)


def _quick_switch_config() -> dict:
    return {
        "translation": {
            "backend": "qianwen",
            "output_format": "translated_with_original",
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
        "tts_language",
        "tts_voice",
        "asr_rewrite_style",
        "roleplay_profile",
    }
    combo_order = tuple(panel._combos)
    assert combo_order.index("asr_rewrite_style") < combo_order.index(
        "roleplay_profile"
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
