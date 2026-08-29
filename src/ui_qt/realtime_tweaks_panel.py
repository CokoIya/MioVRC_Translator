"""Bounded quick-switch panel for settings that can apply immediately."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.ui_qt.icon_utils import ui_icon, ui_icon_url
from src.ui_qt.theme import icon_tint, theme_tokens
from src.ui_qt.widgets import NoWheelComboBox
from src.utils.ui_config import get_tts_engine_label
from src.translators.asr_rewriter import (
    get_asr_rewrite_options,
    normalize_asr_rewrite_style,
)
from src.utils.i18n import tr
from src.utils.localization import format_locale_percent, normalize_ui_language
from src.utils.ui_config import (
    get_backend_config_value,
    get_backend_label,
    get_backend_model_options,
    get_backend_order,
    get_output_format_options,
    normalize_backend,
    normalize_output_format,
    ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
    OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION,
)

logger = logging.getLogger(__name__)


QuickSwitchCallback = Callable[[str, object], None]

PANEL_SIZE = (500, 520)
PANEL_MIN_SIZE = (440, 380)
PANEL_MAX_SIZE = (560, 620)

TTS_LANGUAGE_OPTION_KEYS = {
    "style_bert_vits2": (
        ("quick_switch_bert_japanese", "jp"),
        ("quick_switch_bert_english", "en"),
        ("quick_switch_bert_chinese", "zh"),
    ),
}


def _dict_section(mapping: Mapping[str, object], key: str) -> dict:
    value = mapping.get(key, {})
    return value if isinstance(value, dict) else {}


def _safe_strength(value: object) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return 0.0


def _tts_engine(config: Mapping[str, object]) -> str:
    return str(_dict_section(config, "tts").get("engine", "qwen_tts") or "qwen_tts").strip() or "qwen_tts"


def _tts_language(config: Mapping[str, object], engine: str) -> str:
    tts_cfg = _dict_section(config, "tts")
    engine_cfg = tts_cfg.get(engine, {})
    engine_cfg = engine_cfg if isinstance(engine_cfg, dict) else {}
    if engine == "style_bert_vits2":
        style_cfg = tts_cfg.get("style_bert_vits2", {})
        style_cfg = style_cfg if isinstance(style_cfg, dict) else {}
        return str(style_cfg.get("bert_language", "jp") or "jp")
    return ""


def _tts_language_entries(ui_language: str, engine: str) -> list[tuple[str, str]]:
    return [(tr(ui_language, key), code) for key, code in TTS_LANGUAGE_OPTION_KEYS.get(engine, ())]


# Engines whose catalog is a constant in the source tree. Anything not listed
# has to be discovered at runtime, which is a network or filesystem call the
# quick panel must never make on the UI thread.
STATIC_VOICE_CATALOG_ENGINES = frozenset({"mimo_tts", "qwen_tts"})


def _voice_entries_for_engine(
    config: Mapping[str, object],
    engine: str,
    live_voices: Callable[[str], object] | None = None,
) -> list[tuple[str, str]]:
    """Return (label, id) pairs for the engine's selectable voices.

    VOICEVOX and Style-Bert-VITS2 publish their voices from a running local
    service, so the panel asks the host for the list the audio engine already
    holds instead of opening its own connection. Without that, the voice box
    was simply empty for anyone using VOICEVOX while settings showed it fine.
    """

    if engine not in STATIC_VOICE_CATALOG_ENGINES and engine != "qwen_vc":
        if live_voices is None:
            return []
        try:
            return _voice_entries(live_voices(engine))
        except Exception:
            logger.debug(
                "Failed to read live quick-switch voices for %s", engine, exc_info=True
            )
            return []
    try:
        if engine in {"mimo_tts", "qwen_tts"}:
            from src.tts.api_tts_config import get_tts_api_voice_options

            return [(label, voice_id) for voice_id, label, *_rest in get_tts_api_voice_options(engine)]
        if engine == "qwen_vc":
            from src.tts.api_tts_config import get_cloned_voice_options

            tts_cfg = config.get("tts", {}) if isinstance(config, Mapping) else {}
            engine_cfg = tts_cfg.get("qwen_vc", {}) if isinstance(tts_cfg, Mapping) else {}
            return [
                (label, voice_id)
                for voice_id, label, *_rest in get_cloned_voice_options(
                    engine_cfg if isinstance(engine_cfg, Mapping) else {}
                )
            ]
    except Exception:
        logger.debug("Failed to load quick-switch TTS voices for %s", engine, exc_info=True)
    return []


def _voice_entries(voices: object) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for index, voice in enumerate(voices or []):
        voice_id = getattr(voice, "id", None) or str(index)
        display = getattr(voice, "name", None) or voice_id
        entries.append((str(display), str(voice_id)))
    return entries


class RealtimeTweaksPanel(QDialog):
    """Small floating quick-switch panel with bounded size and scrolling."""

    def __init__(
        self,
        parent: QWidget | None,
        config: Mapping[str, object],
        ui_language: str = "zh-CN",
        theme: str = "dark",
        on_change: QuickSwitchCallback | None = None,
        live_voices: Callable[[str], object] | None = None,
        available_engines: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(None)
        self._owner = parent
        self._config = config
        self._live_voices = live_voices
        self._available_engines = available_engines
        self._ui_lang = normalize_ui_language(ui_language)
        self._theme = theme
        self._on_change = on_change
        self._drag_position = None
        self._icon_labels: list[tuple[QLabel, str, bool]] = []
        self._combos: dict[str, QComboBox] = {}
        self._combo_labels: dict[str, QLabel] = {}
        self._combo_label_keys: dict[str, str] = {}
        self._combo_codes: dict[str, dict[str, str]] = {}
        self._combo_reverse: dict[str, dict[str, str]] = {}
        self._toggles: dict[str, QCheckBox] = {}
        self._toggle_label_keys: dict[str, str] = {}
        self._section_frames: list[QFrame] = []
        self._section_labels: dict[str, QLabel] = {}
        self._noise_slider: QSlider | None = None
        self._noise_value_label: QLabel | None = None
        self._noise_label: QLabel | None = None
        self._refreshing_controls = False

        self.setWindowTitle(tr(self._ui_lang, "quick_switch_title"))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(*PANEL_MIN_SIZE)
        self.setMaximumSize(*PANEL_MAX_SIZE)
        self.resize(*PANEL_SIZE)

        self._build_ui()
        self._refresh_controls()
        self._apply_styles()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._container = QWidget()
        self._container.setObjectName("quickSwitchContainer")
        container_layout = QVBoxLayout(self._container)
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self._icon_label("sliders.svg", strong=True))
        self._title_label = QLabel(tr(self._ui_lang, "quick_switch_title"))
        self._title_label.setObjectName("quickSwitchTitle")
        header.addWidget(self._title_label)
        header.addStretch()

        self._close_btn = QPushButton("")
        self._close_btn.setObjectName("quickSwitchCloseBtn")
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setIconSize(QSize(16, 16))
        self._close_btn.clicked.connect(self.close)
        header.addWidget(self._close_btn)
        container_layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("quickSwitchScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll.viewport().setObjectName("quickSwitchViewport")
        self._scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        scroll_body = QWidget()
        scroll_body.setObjectName("quickSwitchBody")
        scroll_body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._body_layout = QVBoxLayout(scroll_body)
        self._body_layout.setContentsMargins(0, 0, 6, 0)
        self._body_layout.setSpacing(8)
        self._scroll.setWidget(scroll_body)
        container_layout.addWidget(self._scroll, 1)

        self._translation_section = self._section("languages.svg", "quick_switch_section_translation")
        self._add_combo(self._translation_section, "translation_provider", "quick_switch_translation_provider")
        self._add_combo(self._translation_section, "translation_model", "quick_switch_translation_model")
        self._add_combo(self._translation_section, "output_format", "quick_switch_output_format")
        self._add_toggle(
            self._translation_section,
            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
            "quick_switch_original_only_read_translation_wait_for_tts",
        )

        self._audio_section = self._section("mic.svg", "quick_switch_section_audio")
        self._add_noise_slider(self._audio_section, "quick_switch_noise_reduction")

        self._tts_section = self._section("volume.svg", "quick_switch_section_tts")
        self._tts_engine_label = QLabel("")
        self._tts_engine_label.setObjectName("quickSwitchMeta")
        self._tts_section.layout().addWidget(self._tts_engine_label)
        self._add_combo(self._tts_section, "tts_engine", "quick_switch_tts_engine")
        self._add_combo(self._tts_section, "tts_language", "quick_switch_tts_language")
        self._add_combo(self._tts_section, "tts_voice", "quick_switch_tts_voice")

        self._persona_section = self._section("message.svg", "quick_switch_section_persona")
        self._add_combo(
            self._persona_section,
            "asr_rewrite_style",
            "quick_switch_asr_rewrite_style",
        )
        self._add_toggle(
            self._persona_section,
            "rewrite_typed_text",
            "quick_switch_rewrite_typed_text",
        )

        self._body_layout.addStretch(1)
        root_layout.addWidget(self._container)

    def _section(self, icon_name: str, title_key: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("quickSwitchSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(7)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(self._icon_label(icon_name))
        label = QLabel(tr(self._ui_lang, title_key))
        label.setObjectName("quickSwitchSectionTitle")
        heading.addWidget(label)
        heading.addStretch()
        layout.addLayout(heading)

        self._section_labels[title_key] = label
        self._section_frames.append(frame)
        self._body_layout.addWidget(frame)
        return frame

    def _add_combo(self, section: QFrame, key: str, label_key: str) -> None:
        layout = section.layout()
        label = QLabel(tr(self._ui_lang, label_key))
        label.setObjectName("quickSwitchLabel")
        layout.addWidget(label)
        self._combo_labels[key] = label
        self._combo_label_keys[key] = label_key

        combo = NoWheelComboBox()
        combo.setObjectName("quickSwitchCombo")
        combo.setFixedHeight(34)
        combo.setMinimumWidth(0)
        combo.setMinimumContentsLength(10)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.currentTextChanged.connect(lambda text, combo_key=key: self._on_combo_changed(combo_key, text))
        layout.addWidget(combo)
        self._combos[key] = combo

    def _add_toggle(self, section: QFrame, key: str, label_key: str) -> None:
        toggle = QCheckBox(tr(self._ui_lang, label_key))
        toggle.setObjectName("quickSwitchToggle")
        toggle.setMinimumHeight(30)
        toggle.toggled.connect(
            lambda checked, toggle_key=key: self._on_toggle_changed(
                toggle_key,
                checked,
            )
        )
        section.layout().addWidget(toggle)
        self._toggles[key] = toggle
        self._toggle_label_keys[key] = label_key

    def _add_noise_slider(self, section: QFrame, label_key: str) -> None:
        layout = section.layout()
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(10)
        row.setVerticalSpacing(6)

        label = QLabel(tr(self._ui_lang, label_key))
        label.setObjectName("quickSwitchLabel")
        self._noise_label = label
        row.addWidget(label, 0, 0)

        self._noise_value_label = QLabel("0%")
        self._noise_value_label.setObjectName("quickSwitchValue")
        self._noise_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._noise_value_label, 0, 1)

        self._noise_slider = QSlider(Qt.Orientation.Horizontal)
        self._noise_slider.setObjectName("quickSwitchSlider")
        self._noise_slider.setRange(0, 100)
        self._noise_slider.setSingleStep(5)
        self._noise_slider.setPageStep(10)
        self._noise_slider.valueChanged.connect(self._on_noise_slider_changed)
        row.addWidget(self._noise_slider, 1, 0, 1, 2)
        layout.addLayout(row)

    def _icon_label(self, filename: str, *, strong: bool = False) -> QLabel:
        label = QLabel()
        label.setObjectName("quickSwitchIcon")
        label.setFixedSize(18, 18)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_labels.append((label, filename, strong))
        return label

    def _refresh_controls(self) -> None:
        self._refreshing_controls = True
        try:
            self._refresh_combo_controls()
            self._refresh_toggle_controls()
            self._refresh_noise_control()
        finally:
            self._refreshing_controls = False

    def _refresh_combo_controls(self) -> None:
        trans_cfg = _dict_section(self._config, "translation")
        backend = normalize_backend(str(trans_cfg.get("backend", "")))
        backend_options = [
            (get_backend_label(code, self._ui_lang), code)
            for code in get_backend_order()
        ]
        self._set_combo_options("translation_provider", backend_options, backend)

        current_model = get_backend_config_value(trans_cfg, backend, "model")
        model_options = [(model, model) for model in get_backend_model_options(backend, current_model)]
        self._set_combo_options("translation_model", model_options, str(current_model or ""))

        output_format = normalize_output_format(str(trans_cfg.get("output_format", "")))
        self._set_combo_options("output_format", list(get_output_format_options(self._ui_lang)), output_format)

        engine = _tts_engine(self._config)
        self._tts_engine_label.setText(tr(self._ui_lang, "quick_switch_current_tts_engine", engine=engine))

        # Only engines that are ready right now: switching to one that still
        # needs a key, a download or a local service is not a quick switch, it
        # is a trip to settings.
        engine_options = self._engine_options(engine)
        self._set_combo_visible("tts_engine", len(engine_options) > 1)
        if engine_options:
            self._set_combo_options("tts_engine", engine_options, engine)

        language_options = _tts_language_entries(self._ui_lang, engine)
        self._set_combo_visible("tts_language", bool(language_options))
        if language_options:
            self._set_combo_options("tts_language", language_options, _tts_language(self._config, engine))

        tts_cfg = _dict_section(self._config, "tts")
        engine_cfg = tts_cfg.get(engine, {})
        engine_cfg = engine_cfg if isinstance(engine_cfg, dict) else {}
        voice_options = _voice_entries_for_engine(self._config, engine, self._live_voices)
        self._set_combo_visible("tts_voice", bool(voice_options))
        self._set_combo_options("tts_voice", voice_options, str(engine_cfg.get("voice", "") or ""))

        rewrite_style = normalize_asr_rewrite_style(
            trans_cfg.get("asr_rewrite_style", "off")
        )
        self._set_combo_options(
            "asr_rewrite_style",
            list(get_asr_rewrite_options(self._ui_lang)),
            rewrite_style,
        )

    def _engine_options(self, current: str) -> list[tuple[str, str]]:
        """List the switchable speech engines, current one always included."""

        if self._available_engines is None:
            return []
        try:
            engines = list(self._available_engines() or ())
        except Exception:
            logger.debug("Failed to read switchable TTS engines", exc_info=True)
            return []
        codes: list[str] = []
        for engine in engines:
            code = str(engine or "").strip()
            if code and code not in codes:
                codes.append(code)
        if current and current not in codes:
            codes.insert(0, current)
        return [(get_tts_engine_label(code, self._ui_lang), code) for code in codes]

    def _refresh_toggle_controls(self) -> None:
        trans_cfg = _dict_section(self._config, "translation")
        rewrite_toggle = self._toggles.get("rewrite_typed_text")
        if rewrite_toggle is not None:
            rewrite_toggle.blockSignals(True)
            try:
                rewrite_toggle.setChecked(bool(trans_cfg.get("rewrite_typed_text", False)))
            finally:
                rewrite_toggle.blockSignals(False)

        tts_wait_toggle = self._toggles.get(
            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY
        )
        if tts_wait_toggle is not None:
            output_format = normalize_output_format(
                str(trans_cfg.get("output_format", ""))
            )
            visible = output_format == OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION
            tts_wait_toggle.setVisible(visible)
            tts_wait_toggle.blockSignals(True)
            try:
                tts_wait_toggle.setChecked(
                    bool(
                        trans_cfg.get(
                            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
                            True,
                        )
                    )
                )
            finally:
                tts_wait_toggle.blockSignals(False)

    def _refresh_noise_control(self) -> None:
        if self._noise_slider is None:
            return
        audio_cfg = _dict_section(self._config, "audio")
        value = int(round(_safe_strength(audio_cfg.get("denoise_strength", 0.0)) * 100))
        self._noise_slider.blockSignals(True)
        try:
            self._noise_slider.setValue(value)
        finally:
            self._noise_slider.blockSignals(False)
        self._update_noise_value_label(value)

    def _update_noise_value_label(self, value: int) -> None:
        if self._noise_value_label is not None:
            self._noise_value_label.setText(
                format_locale_percent(int(value), self._ui_lang)
            )

    def _set_combo_visible(self, key: str, visible: bool) -> None:
        combo = self._combos.get(key)
        if combo is None:
            return
        label = self._combo_labels.get(key)
        if label is not None:
            label.setVisible(visible)
        combo.setVisible(visible)

    def _set_combo_options(self, key: str, entries: list[tuple[str, str]], current_code: str) -> None:
        combo = self._combos[key]
        combo.blockSignals(True)
        try:
            combo.clear()
            self._combo_codes[key] = {}
            self._combo_reverse[key] = {}
            for label, code in entries:
                label_text = str(label)
                code_text = str(code)
                combo.addItem(label_text)
                self._combo_codes[key][label_text] = code_text
                self._combo_reverse[key].setdefault(code_text, label_text)
            if not entries:
                combo.addItem(tr(self._ui_lang, "quick_switch_no_option"))
                combo.setEnabled(False)
                return
            combo.setEnabled(True)
            selected_label = self._combo_reverse[key].get(str(current_code), entries[0][0])
            combo.setCurrentText(str(selected_label))
        finally:
            combo.blockSignals(False)

    def _on_combo_changed(self, key: str, text: str) -> None:
        if self._refreshing_controls:
            return
        code = self._combo_codes.get(key, {}).get(text)
        if code is None:
            return
        if self._on_change is not None:
            self._on_change(key, code)
        if key in {"translation_provider", "tts_language", "output_format"}:
            self._refresh_controls()

    def _on_toggle_changed(self, key: str, checked: bool) -> None:
        if self._refreshing_controls or self._on_change is None:
            return
        self._on_change(key, bool(checked))

    def _on_noise_slider_changed(self, value: int) -> None:
        self._update_noise_value_label(value)
        if self._refreshing_controls or self._on_change is None:
            return
        self._on_change("noise_reduction", round(value / 100.0, 2))

    def _refresh_icons(self) -> None:
        tokens = theme_tokens(self._theme)
        for label, filename, strong in self._icon_labels:
            color = str(tokens["ACCENT"]) if strong else icon_tint(self._theme)
            icon = ui_icon(filename, 16, color)
            if icon.isNull():
                label.clear()
            else:
                label.setPixmap(icon.pixmap(16, 16))
        icon = ui_icon("x.svg", 16, icon_tint(self._theme, strong=True))
        close_text = tr(self._ui_lang, "text_input_close")
        self._close_btn.setIcon(icon)
        self._close_btn.setText(close_text if icon.isNull() else "")
        self._close_btn.setToolTip(close_text)

    def _apply_styles(self) -> None:
        tokens = theme_tokens(self._theme)
        combo_arrow = ui_icon_url("chevron-down-muted.svg")
        slider_handle = ui_icon_url("slider-thumb.svg")
        self.setStyleSheet(
            f"""
            #quickSwitchContainer {{
                background: {tokens['PANEL_BG']};
                border: 1px solid {tokens['PANEL_BORDER']};
                border-radius: {tokens['RADIUS_L']}px;
            }}
            #quickSwitchTitle {{
                color: {tokens['TEXT_PRIMARY']};
                font-size: 15px;
                font-weight: 700;
            }}
            #quickSwitchSection {{
                background: {tokens['PANEL_ALT_BG']};
                border: 1px solid {tokens['PANEL_BORDER']};
                border-radius: {tokens['RADIUS_M']}px;
            }}
            #quickSwitchSectionTitle {{
                color: {tokens['TEXT_PRIMARY']};
                font-size: 13px;
                font-weight: 700;
            }}
            #quickSwitchLabel {{
                color: {tokens['TEXT_SECONDARY']};
                font-size: 12px;
                font-weight: 600;
            }}
            #quickSwitchToggle {{
                color: {tokens['TEXT_SECONDARY']};
                font-size: 12px;
                font-weight: 600;
                spacing: 9px;
            }}
            #quickSwitchToggle::indicator {{
                width: 34px;
                height: 18px;
                border: 1px solid {tokens['FIELD_BORDER']};
                border-radius: 9px;
                background: {tokens['FIELD_BG']};
            }}
            #quickSwitchToggle::indicator:checked {{
                border-color: {tokens['ACCENT_BORDER']};
                background: {tokens['ACCENT']};
            }}
            #quickSwitchMeta {{
                color: {tokens['TEXT_MUTED']};
                font-size: 11px;
                font-weight: 600;
            }}
            #quickSwitchValue {{
                color: {tokens['TEXT_MUTED']};
                font-size: 12px;
                font-weight: 700;
                font-variant-numeric: tabular-nums;
            }}
            #quickSwitchCombo {{
                background: {tokens['FIELD_BG']};
                border: 1px solid {tokens['FIELD_BORDER']};
                border-radius: {tokens['RADIUS_M']}px;
                color: {tokens['INPUT_TEXT']};
                min-height: 32px;
                padding: 5px 30px 5px 10px;
            }}
            #quickSwitchCombo:hover {{
                background: {tokens['FIELD_HOVER']};
                border-color: {tokens['PANEL_BORDER']};
            }}
            #quickSwitchCombo::drop-down {{
                border: 0;
                width: 26px;
            }}
            #quickSwitchCombo::down-arrow {{
                image: {combo_arrow};
                width: 13px;
                height: 13px;
                margin-right: 8px;
            }}
            #quickSwitchCloseBtn {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 0;
            }}
            #quickSwitchCloseBtn:hover {{
                background: {tokens['FIELD_HOVER']};
                border-color: {tokens['PANEL_BORDER']};
            }}
            #quickSwitchScroll {{
                background: transparent;
                border: 0;
            }}
            #quickSwitchViewport,
            #quickSwitchBody {{
                background: transparent;
            }}
            #quickSwitchSlider {{
                min-height: 24px;
            }}
            #quickSwitchSlider::groove:horizontal {{
                background: {tokens['FIELD_BG']};
                border: 1px solid {tokens['FIELD_BORDER']};
                height: 6px;
                border-radius: 3px;
            }}
            #quickSwitchSlider::sub-page:horizontal {{
                background: {tokens['ACCENT']};
                border-radius: 3px;
            }}
            #quickSwitchSlider::handle:horizontal {{
                image: {slider_handle};
                background: {tokens['PANEL_BG']};
                width: 18px;
                height: 18px;
                margin: -7px 0;
                border: 1px solid {tokens['ACCENT_BORDER']};
                border-radius: 9px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 2px 0 2px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {tokens['PANEL_BORDER']};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                border: 0;
                background: transparent;
                height: 0;
            }}
            """
        )
        self._refresh_icons()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 44:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_position is not None:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.ActivationChange:
            QTimer.singleShot(0, self._refresh_controls)
        super().changeEvent(event)

    def refresh_theme(self, theme: str) -> None:
        self._theme = theme
        self._apply_styles()

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = normalize_ui_language(ui_language)
        self._refresh_static_texts()
        self._refresh_controls()
        self._refresh_icons()

    def _refresh_static_texts(self) -> None:
        title = tr(self._ui_lang, "quick_switch_title")
        self.setWindowTitle(title)
        if hasattr(self, "_title_label"):
            self._title_label.setText(title)
        for key, label in self._section_labels.items():
            label.setText(tr(self._ui_lang, key))
        for combo_key, label in self._combo_labels.items():
            label_key = self._combo_label_keys.get(combo_key)
            if label_key:
                label.setText(tr(self._ui_lang, label_key))
        for toggle_key, toggle in self._toggles.items():
            label_key = self._toggle_label_keys.get(toggle_key)
            if label_key:
                toggle.setText(tr(self._ui_lang, label_key))
        if self._noise_label is not None:
            self._noise_label.setText(tr(self._ui_lang, "quick_switch_noise_reduction"))
