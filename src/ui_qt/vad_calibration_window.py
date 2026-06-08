from __future__ import annotations

from typing import Callable, Mapping

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from src.audio.vad_calibration_service import VadCalibrationResult, VadCalibrationService


_SAMPLE_MS = 5000
_TICK_MS = 250

_COPY = {
    "zh-CN": {
        "title_mic": "校准麦克风自动断句",
        "title_listen": "校准听别人时的自动断句",
        "subtitle": "先让 Mio 听一会儿房间里的杂音，再用平时音量说话。Mio 会帮你推荐“多小的声音算杂音”和“停多久算说完”。",
        "noise": "背景杂音",
        "speech": "说话声音",
        "start_noise": "开始听背景杂音",
        "start_speech": "开始说话测试",
        "apply": "使用推荐设置",
        "close": "关闭",
        "current_rms": "当前声音大小",
        "noise_floor": "背景杂音大小",
        "speech_floor": "说话声音大小",
        "recommended_rms": "建议忽略的杂音大小",
        "recommended_silence": "建议停多久算说完",
        "samples": "已听取次数",
        "confidence": "建议可靠度",
        "idle": "等待采样",
        "sampling_noise": "请保持安静…",
        "sampling_speech": "请用平时音量说话…",
        "done": "校准完成，可以使用推荐设置。",
    },
    "en": {
        "title_mic": "Microphone VAD Calibration",
        "title_listen": "Listen VAD Calibration",
        "subtitle": "Sample ambient noise first, then speak normally. Calibration recommends and writes VAD thresholds without restarting the whole app.",
        "noise": "Noise sample",
        "speech": "Speech sample",
        "start_noise": "Start noise sample",
        "start_speech": "Start speech test",
        "apply": "Apply recommended settings",
        "close": "Close",
        "current_rms": "Current RMS",
        "noise_floor": "Noise P90",
        "speech_floor": "Speech P30",
        "recommended_rms": "Recommended min RMS",
        "recommended_silence": "Recommended silence",
        "samples": "Samples",
        "confidence": "Confidence",
        "idle": "Waiting for samples",
        "sampling_noise": "Keep quiet…",
        "sampling_speech": "Speak at normal volume…",
        "done": "Sampling complete. You can apply the recommendation.",
    },
    "ja": {
        "title_mic": "マイク自動区切りの調整",
        "title_listen": "聞き取り自動区切りの調整",
        "subtitle": "まず部屋の雑音を少し測り、その後ふだんの音量で話してください。Mio が雑音として無視する音量と、発話終了までの無音時間を提案します。",
        "noise": "背景ノイズ",
        "speech": "発話サンプル",
        "start_noise": "背景ノイズを測る",
        "start_speech": "発話テストを開始",
        "apply": "おすすめ設定を使う",
        "close": "閉じる",
        "current_rms": "現在の音量",
        "noise_floor": "背景ノイズ量",
        "speech_floor": "発話音量",
        "recommended_rms": "推奨 最小 RMS",
        "recommended_silence": "推奨 無音時間",
        "samples": "サンプル数",
        "confidence": "信頼度",
        "idle": "サンプル待ち",
        "sampling_noise": "静かにしてください…",
        "sampling_speech": "ふだんの音量で話してください…",
        "done": "調整が完了しました。おすすめ設定を適用できます。",
    },
    "ru": {
        "title_mic": "Калибровка авторазделения микрофона",
        "title_listen": "Калибровка авторазделения прослушивания",
        "subtitle": "Сначала измерьте фоновый шум, затем говорите обычной громкостью. Mio предложит порог шума и длительность тишины для завершения фразы.",
        "noise": "Фоновый шум",
        "speech": "Речь",
        "start_noise": "Измерить шум",
        "start_speech": "Начать тест речи",
        "apply": "Применить рекомендацию",
        "close": "Закрыть",
        "current_rms": "Текущий RMS",
        "noise_floor": "Шум P90",
        "speech_floor": "Речь P30",
        "recommended_rms": "Рекоменд. мин. RMS",
        "recommended_silence": "Рекоменд. тишина",
        "samples": "Сэмплы",
        "confidence": "Надежность",
        "idle": "Ожидание сэмплов",
        "sampling_noise": "Сохраняйте тишину…",
        "sampling_speech": "Говорите обычной громкостью…",
        "done": "Калибровка завершена. Можно применить рекомендацию.",
    },
    "ko": {
        "title_mic": "마이크 자동 문장 구분 보정",
        "title_listen": "듣기 자동 문장 구분 보정",
        "subtitle": "먼저 주변 소음을 잠시 측정한 뒤 평소 음량으로 말해 주세요. Mio가 잡음 무시 기준과 말이 끝났다고 볼 무음 시간을 추천합니다.",
        "noise": "배경 소음",
        "speech": "말소리",
        "start_noise": "배경 소음 측정",
        "start_speech": "말하기 테스트 시작",
        "apply": "추천 설정 적용",
        "close": "닫기",
        "current_rms": "현재 RMS",
        "noise_floor": "소음 P90",
        "speech_floor": "말소리 P30",
        "recommended_rms": "추천 최소 RMS",
        "recommended_silence": "추천 무음 시간",
        "samples": "샘플",
        "confidence": "신뢰도",
        "idle": "샘플 대기",
        "sampling_noise": "조용히 있어 주세요…",
        "sampling_speech": "평소 음량으로 말해 주세요…",
        "done": "보정이 완료되었습니다. 추천 설정을 적용할 수 있습니다.",
    },
}


def _lang(ui_language: str) -> str:
    normalized = str(ui_language or "").strip()
    if normalized in _COPY:
        return normalized
    lowered = normalized.lower()
    if lowered.startswith("zh"):
        return "zh-CN"
    if lowered.startswith("ja") or lowered.startswith("jp"):
        return "ja"
    if lowered.startswith("ru"):
        return "ru"
    if lowered.startswith("ko"):
        return "ko"
    return "en"


def _fmt(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


class VadCalibrationWindow(QDialog):
    def __init__(
        self,
        parent,
        *,
        target: str,
        snapshot_provider: Callable[[str], Mapping[str, object]],
        apply_callback: Callable[[str, VadCalibrationResult], None],
        current_silence_s: float = 0.65,
        ui_language: str = "zh-CN",
    ) -> None:
        super().__init__(parent)
        self._target = "vrc_listen" if target == "vrc_listen" else "mic"
        self._snapshot_provider = snapshot_provider
        self._apply_callback = apply_callback
        self._copy = _COPY[_lang(ui_language)]
        self._service = VadCalibrationService(current_silence_s=current_silence_s)
        self._phase = "idle"
        self._remaining_ms = 0
        self._labels: dict[str, QLabel] = {}
        self._progress: QProgressBar | None = None
        self._apply_btn: QPushButton | None = None
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)

        self.setObjectName("vadCalibrationDialog")
        self.setWindowTitle(self._copy["title_listen" if self._target == "vrc_listen" else "title_mic"])
        self.setMinimumSize(520, 460)
        self._build_ui()
        self._refresh_result()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel(self.windowTitle())
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(self._copy["subtitle"])
        subtitle.setObjectName("hintLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat(self._copy["idle"])
        layout.addWidget(self._progress)

        actions = QHBoxLayout()
        noise_btn = QPushButton(self._copy["start_noise"])
        noise_btn.clicked.connect(lambda: self._start_phase("noise"))
        speech_btn = QPushButton(self._copy["start_speech"])
        speech_btn.clicked.connect(lambda: self._start_phase("speech"))
        actions.addWidget(noise_btn)
        actions.addWidget(speech_btn)
        layout.addLayout(actions)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)
        for row, key in enumerate(("current_rms", "samples", "noise_floor", "speech_floor", "recommended_rms", "recommended_silence", "confidence")):
            name = QLabel(self._copy[key])
            name.setObjectName("fieldLabel")
            value = QLabel("—")
            value.setObjectName("hintLabel")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._labels[key] = value
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self._apply_btn = QPushButton(self._copy["apply"])
        self._apply_btn.clicked.connect(self._apply)
        bottom.addWidget(self._apply_btn)
        close_btn = QPushButton(self._copy["close"])
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _start_phase(self, phase: str) -> None:
        self._phase = phase
        self._remaining_ms = _SAMPLE_MS
        if phase == "noise":
            self._service.reset_noise()
        else:
            self._service.reset_speech()
        self._tick_timer.start(_TICK_MS)
        self._tick()

    def _tick(self) -> None:
        snapshot = dict(self._snapshot_provider(self._target) or {})
        current_rms = snapshot.get("last_frame_rms") or snapshot.get("last_prepared_rms") or snapshot.get("last_rms") or 0.0
        self._labels["current_rms"].setText(_fmt(current_rms))
        if self._phase == "noise":
            self._service.add_noise_snapshot(snapshot)
        elif self._phase == "speech":
            self._service.add_speech_snapshot(snapshot)
        else:
            self._refresh_result()
            return

        self._remaining_ms = max(0, self._remaining_ms - _TICK_MS)
        elapsed = _SAMPLE_MS - self._remaining_ms
        if self._progress is not None:
            self._progress.setValue(min(int(elapsed * 100 / _SAMPLE_MS), 100))
            self._progress.setFormat(self._copy["sampling_noise" if self._phase == "noise" else "sampling_speech"])
        if self._remaining_ms <= 0:
            self._phase = "idle"
            self._tick_timer.stop()
            if self._progress is not None:
                self._progress.setValue(100)
                self._progress.setFormat(self._copy["done"])
        self._refresh_result()

    def _refresh_result(self) -> None:
        result = self._service.result()
        self._labels["samples"].setText(f"{result.noise_sample_count} / {result.speech_sample_count}")
        self._labels["noise_floor"].setText(_fmt(result.noise_floor))
        self._labels["speech_floor"].setText(_fmt(result.speech_floor))
        self._labels["recommended_rms"].setText(_fmt(result.recommended_min_rms))
        self._labels["recommended_silence"].setText(_fmt(result.recommended_silence_s, 2))
        self._labels["confidence"].setText(result.confidence)
        if self._apply_btn is not None:
            self._apply_btn.setEnabled(result.noise_sample_count > 0 and result.speech_sample_count > 0)

    def _apply(self) -> None:
        result = self._service.result()
        self._apply_callback(self._target, result)
        self._refresh_result()
