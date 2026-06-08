from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout


@dataclass(frozen=True)
class ModeWizardResult:
    mode_id: str
    apply_recommendation: bool


_COPY = {
    "zh-CN": {
        "title": "Mio 新手设置",
        "subtitle": "先选一个最像你要做的事。Mio 会帮你打开常用开关；麦克风、翻译账号和音色之后还能慢慢改。",
        "chatbox_title": "我说话，发到聊天框",
        "chatbox_body": "你对着麦克风说话，Mio 翻译后发到 VRChat 聊天框。",
        "listen_title": "听别人说话",
        "listen_body": "Mio 听电脑里 VRChat 的声音，把别人说的话翻译给你看。",
        "tts_title": "让 Mio 替我朗读",
        "tts_body": "翻译完成后，Mio 用选好的声音读出来，也可以送进 VRChat。",
        "manual_title": "我打字，让 Mio 翻译",
        "manual_body": "手动输入一句话，Mio 翻译后可以发到聊天框。",
        "overlay_title": "在 VR 里看字幕",
        "overlay_body": "把听到的内容显示在悬浮字幕里，戴头显时也能看。",
        "steps": "下一步：确认 VRChat 里的 OSC 开关已打开，选好麦克风和翻译服务，然后试着发一句话。",
        "open_settings": "选这个并去设置",
        "apply": "选这个",
        "skip": "先跳过",
    },
    "en": {
        "title": "Mio First-run Guide",
        "subtitle": "Pick the closest usage mode. Mio will save recommended toggles; API keys, devices, and models can still be tuned in Settings.",
        "chatbox_title": "Chatbox Translation",
        "chatbox_body": "Speech → ASR → translation → VRChat Chatbox.",
        "listen_title": "Listen to Others",
        "listen_body": "Capture VRChat / desktop audio and translate what others say.",
        "tts_title": "Interpretation / TTS",
        "tts_body": "Auto-read translated text and route it to VRChat through a virtual device.",
        "manual_title": "Manual Input",
        "manual_body": "Type text manually, translate it, then send it to Chatbox.",
        "overlay_title": "VR Captions",
        "overlay_body": "Show live captions in the floating window and future VR Overlay.",
        "steps": "Next checks: 1. Enable OSC in VRChat  2. Pick microphone  3. Pick desktop / VRChat audio source  4. Pick ASR and translator  5. Test Chatbox sending.",
        "open_settings": "Apply and Open Settings",
        "apply": "Apply Recommendation",
        "skip": "Skip",
    },
}

_MODE_ORDER = ("chatbox", "listen", "tts", "manual", "overlay")


def _lang(ui_language: str) -> str:
    return "zh-CN" if str(ui_language or "").lower().startswith("zh") else "en"


class ModeWizardDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        ui_language: str = "zh-CN",
        on_done: Callable[[ModeWizardResult | None], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._copy = _COPY[_lang(ui_language)]
        self._on_done = on_done
        self._result: ModeWizardResult | None = None
        self._mode_buttons: dict[str, QRadioButton] = {}
        self._button_group = QButtonGroup(self)
        self.setObjectName("modeWizardDialog")
        self.setWindowTitle(self._copy["title"])
        self.setMinimumSize(620, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel(self._copy["title"])
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(self._copy["subtitle"])
        subtitle.setObjectName("hintLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        for index, mode_id in enumerate(_MODE_ORDER):
            card = self._mode_card(
                mode_id,
                self._copy[f"{mode_id}_title"],
                self._copy[f"{mode_id}_body"],
                checked=index == 0,
            )
            layout.addWidget(card)

        steps = QLabel(self._copy["steps"])
        steps.setObjectName("hintLabel")
        steps.setWordWrap(True)
        layout.addWidget(steps)

        actions = QHBoxLayout()
        actions.addStretch(1)
        skip_btn = QPushButton(self._copy["skip"])
        skip_btn.clicked.connect(self._skip)
        actions.addWidget(skip_btn)
        apply_btn = QPushButton(self._copy["apply"])
        apply_btn.clicked.connect(lambda: self._finish(open_settings=False))
        actions.addWidget(apply_btn)
        start_btn = QPushButton(self._copy["open_settings"])
        start_btn.setObjectName("primaryButton")
        start_btn.clicked.connect(lambda: self._finish(open_settings=True))
        actions.addWidget(start_btn)
        layout.addLayout(actions)

    def _mode_card(self, mode_id: str, title: str, body: str, *, checked: bool = False) -> QFrame:
        frame = QFrame()
        frame.setObjectName("subCard")
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        radio = QRadioButton()
        radio.setChecked(checked)
        self._button_group.addButton(radio)
        self._mode_buttons[mode_id] = radio
        row.addWidget(radio, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("fieldLabel")
        body_label = QLabel(body)
        body_label.setObjectName("hintLabel")
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_col.addWidget(title_label)
        text_col.addWidget(body_label)
        row.addLayout(text_col, 1)
        frame.mousePressEvent = lambda _event, button=radio: button.setChecked(True)
        return frame

    def selected_mode(self) -> str:
        for mode_id in _MODE_ORDER:
            button = self._mode_buttons.get(mode_id)
            if button is not None and button.isChecked():
                return mode_id
        return "chatbox"

    def result_payload(self) -> ModeWizardResult | None:
        return self._result

    def _skip(self) -> None:
        self._result = None
        if callable(self._on_done):
            self._on_done(None)
        self.accept()

    def _finish(self, *, open_settings: bool) -> None:
        self._result = ModeWizardResult(self.selected_mode(), open_settings)
        if callable(self._on_done):
            self._on_done(self._result)
        self.accept()
