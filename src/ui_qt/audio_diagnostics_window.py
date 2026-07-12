from __future__ import annotations

from typing import Callable, Mapping

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from src.utils.localization import format_locale_number, normalize_ui_language


_COPY = {
    "zh-CN": {
        "title_mic": "查看麦克风声音",
        "title_listen": "查看别人声音",
        "subtitle": "这里会显示 Mio 现在有没有收到声音、声音有多大，以及是否判断为有人在说话。这个窗口不会开始或停止监听。",
        "running": "正在收音",
        "device": "设备",
        "rms": "当前声音大小",
        "peak": "最大声音大小",
        "vad": "是否有人声",
        "threshold": "忽略杂音的大小",
        "frames": "已检查次数",
        "segments": "已切出的句子",
        "rate": "声音采样速度",
        "error": "最近的问题",
        "error_detected": "音频捕获发生错误。技术详情已写入日志。",
        "close": "关闭",
        "yes": "是",
        "no": "否",
        "speech": "有人在说话",
        "silence": "安静",
        "none": "—",
    },
    "en": {
        "title_mic": "Microphone Audio Diagnostics",
        "title_listen": "Listen Audio Diagnostics",
        "subtitle": "Live RMS, VAD, and device status from the active capture pipeline. This window does not start or stop capture.",
        "running": "Capturing",
        "device": "Device",
        "rms": "Current RMS",
        "peak": "Peak RMS",
        "vad": "VAD State",
        "threshold": "Minimum RMS",
        "frames": "Frames",
        "segments": "Segments",
        "rate": "Sample Rate",
        "error": "Last Error",
        "error_detected": "Audio capture failed. Technical details were written to the log.",
        "close": "Close",
        "yes": "Yes",
        "no": "No",
        "speech": "Speech",
        "silence": "Silence",
        "none": "—",
    },
    "ja": {
        "title_mic": "マイク音声診断",
        "title_listen": "聞き取り音声診断",
        "subtitle": "現在 Mio が音を受け取れているか、音量、音声判定の状態を表示します。このウィンドウは聞き取りを開始・停止しません。",
        "running": "取得中",
        "device": "デバイス",
        "rms": "現在の音量",
        "peak": "最大音量",
        "vad": "音声判定",
        "threshold": "最小 RMS",
        "frames": "確認回数",
        "segments": "切り出した文",
        "rate": "サンプルレート",
        "error": "直近の問題",
        "error_detected": "音声取得エラーが発生しました。技術的な詳細はログに記録されています。",
        "close": "閉じる",
        "yes": "はい",
        "no": "いいえ",
        "speech": "発話中",
        "silence": "無音",
        "none": "—",
    },
    "ru": {
        "title_mic": "Диагностика микрофона",
        "title_listen": "Диагностика прослушивания",
        "subtitle": "Здесь показано, получает ли Mio звук, насколько он громкий и определяется ли речь. Это окно не запускает и не останавливает захват.",
        "running": "Захват",
        "device": "Устройство",
        "rms": "Текущий RMS",
        "peak": "Пиковый RMS",
        "vad": "Состояние VAD",
        "threshold": "Минимальный RMS",
        "frames": "Кадры",
        "segments": "Сегменты",
        "rate": "Частота",
        "error": "Последняя ошибка",
        "error_detected": "Произошла ошибка захвата звука. Технические сведения записаны в журнал.",
        "close": "Закрыть",
        "yes": "Да",
        "no": "Нет",
        "speech": "Речь",
        "silence": "Тишина",
        "none": "—",
    },
    "ko": {
        "title_mic": "마이크 오디오 진단",
        "title_listen": "듣기 오디오 진단",
        "subtitle": "Mio가 현재 소리를 받고 있는지, 소리 크기와 음성 감지 상태를 표시합니다. 이 창은 캡처를 시작하거나 중지하지 않습니다.",
        "running": "수음 중",
        "device": "장치",
        "rms": "현재 RMS",
        "peak": "최대 RMS",
        "vad": "VAD 상태",
        "threshold": "최소 RMS",
        "frames": "프레임",
        "segments": "세그먼트",
        "rate": "샘플 레이트",
        "error": "최근 오류",
        "error_detected": "오디오 캡처 오류가 발생했습니다. 기술 세부 정보는 로그에 기록되었습니다.",
        "close": "닫기",
        "yes": "예",
        "no": "아니요",
        "speech": "말하는 중",
        "silence": "조용함",
        "none": "—",
    },
}


def _lang(ui_language: str) -> str:
    return normalize_ui_language(ui_language, default="en")


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_float(value: object, language: str = "en", digits: int = 4) -> str:
    try:
        return format_locale_number(float(value), language, decimals=digits)
    except (TypeError, ValueError):
        return "—"


class AudioDiagnosticsWindow(QDialog):
    def __init__(
        self,
        parent,
        *,
        target: str,
        snapshot_provider: Callable[[str], Mapping[str, object]],
        ui_language: str = "zh-CN",
    ) -> None:
        super().__init__(parent)
        self._target = "vrc_listen" if target == "vrc_listen" else "mic"
        self._snapshot_provider = snapshot_provider
        self._ui_lang = _lang(ui_language)
        self._copy = _COPY[self._ui_lang]
        self._labels: dict[str, QLabel] = {}
        self._name_labels: dict[str, QLabel] = {}
        self._meter_labels: dict[str, QLabel] = {}
        self._rms_bar: QProgressBar | None = None
        self._peak_bar: QProgressBar | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

        self.setObjectName("audioDiagnosticsDialog")
        self.setWindowTitle(self._copy["title_listen" if self._target == "vrc_listen" else "title_mic"])
        self.setMinimumSize(460, 420)
        self._build_ui()
        self.refresh()
        self._timer.start(250)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self._title_label = QLabel(self.windowTitle())
        self._title_label.setObjectName("sectionTitle")
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel(self._copy["subtitle"])
        self._subtitle_label.setObjectName("hintLabel")
        self._subtitle_label.setWordWrap(True)
        layout.addWidget(self._subtitle_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)

        row = 0
        for key in ("running", "device", "vad", "threshold", "frames", "segments", "rate", "error"):
            name = QLabel(self._copy[key])
            name.setObjectName("fieldLabel")
            name.setWordWrap(True)
            value = QLabel(self._copy["none"])
            value.setObjectName("hintLabel")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._labels[key] = value
            self._name_labels[key] = name
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            row += 1

        self._rms_bar = self._meter_row(layout, "rms")
        self._peak_bar = self._meter_row(layout, "peak")

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._close_btn = QPushButton(self._copy["close"])
        self._close_btn.clicked.connect(self.close)
        actions.addWidget(self._close_btn)
        layout.addLayout(actions)

    def _meter_row(self, layout: QVBoxLayout, key: str) -> QProgressBar:
        label = QLabel(self._copy[key])
        label.setObjectName("fieldLabel")
        self._meter_labels[key] = label
        layout.addWidget(label)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        layout.addWidget(bar)
        return bar

    def refresh(self) -> None:
        try:
            snapshot = dict(self._snapshot_provider(self._target) or {})
        except Exception:
            snapshot = {}
        none = self._copy["none"]
        running = bool(snapshot.get("running", False))
        self._labels["running"].setText(self._copy["yes"] if running else self._copy["no"])
        device = snapshot.get("active_device") or snapshot.get("active_output_device") or snapshot.get("configured_device") or none
        self._labels["device"].setText(str(device or none))
        self._labels["vad"].setText(self._copy["speech"] if bool(snapshot.get("vad_in_speech")) else self._copy["silence"])
        self._labels["threshold"].setText(_fmt_float(snapshot.get("vad_min_rms"), self._ui_lang))
        frames = int(snapshot.get("frames_processed") or snapshot.get("total_frames") or 0)
        segments = int(snapshot.get("segments_emitted") or 0)
        self._labels["frames"].setText(format_locale_number(frames, self._ui_lang, grouping=True))
        self._labels["segments"].setText(format_locale_number(segments, self._ui_lang, grouping=True))
        rate = snapshot.get("capture_rate") or snapshot.get("target_rate") or none
        try:
            rendered_rate = format_locale_number(int(rate), self._ui_lang, grouping=True)
        except (TypeError, ValueError):
            rendered_rate = str(rate or none)
        self._labels["rate"].setText(rendered_rate)
        self._labels["error"].setText(
            self._copy["error_detected"] if snapshot.get("last_error") else none
        )

        rms = _to_float(snapshot.get("last_frame_rms") or snapshot.get("last_prepared_rms") or snapshot.get("last_rms") or 0.0)
        peak = _to_float(snapshot.get("peak_frame_rms"), rms)
        if self._rms_bar is not None:
            self._rms_bar.setValue(min(int(rms * 2500), 100))
            self._rms_bar.setFormat(_fmt_float(rms, self._ui_lang))
        if self._peak_bar is not None:
            self._peak_bar.setValue(min(int(peak * 2500), 100))
            self._peak_bar.setFormat(_fmt_float(peak, self._ui_lang))

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = _lang(ui_language)
        self._copy = _COPY[self._ui_lang]
        title_key = "title_listen" if self._target == "vrc_listen" else "title_mic"
        self.setWindowTitle(self._copy[title_key])
        self._title_label.setText(self._copy[title_key])
        self._subtitle_label.setText(self._copy["subtitle"])
        for key, label in self._name_labels.items():
            label.setText(self._copy[key])
        for key, label in self._meter_labels.items():
            label.setText(self._copy[key])
        self._close_btn.setText(self._copy["close"])
        self.refresh()
        self.adjustSize()

    def showEvent(self, event) -> None:  # noqa: N802
        if not self._timer.isActive():
            self.refresh()
            self._timer.start(250)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)
