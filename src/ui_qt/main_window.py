from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, QObject, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.asr.model_registry import ASR_ENGINE_FOLLOW_MAIN, LISTEN_SELECTABLE_ASR_ENGINES, get_asr_runtime_spec, normalize_asr_engine
from src.core.manual_translation_controller import ManualTranslationController, ManualTranslationRequest
from src.core.mode_manager import AppMode, ModeManager
from src.core.output_dispatcher import OutputDispatcher, OutputMessage
from src.core.overlay_service import OverlayService
from src.core.realtime_pipelines import ListenPipeline, MicPipeline
from src.ui_qt.icon_utils import ui_icon
from src.ui_qt.styles import build_app_stylesheet, build_main_window_styles
from src.ui_qt.theme import MAIN_THEME_CONFIG_KEY, icon_tint, normalize_theme, normalize_theme_preference, resolve_theme, theme_preference_from_config, theme_tokens
from src.ui_qt.window_utils import apply_window_chrome_theme, play_theme_fade
from src.ui_qt.widgets import NoWheelComboBox
from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel
from src.ui_qt.state_manager import AppState
from src.utils import config_manager
from src.utils.app_paths import resource_base_dirs
from src.utils.global_hotkey import GlobalHotkey, DEFAULT_MIC_MUTE_HOTKEY, DEFAULT_TEXT_INPUT_HOTKEY
from src.utils.i18n import tr
from src.utils.lang_detect import detect_language
from src.utils.translation_error_formatter import format_translation_error
from src.utils.translation_config_validation import missing_required_translation_api_key
from src.utils.ui_config import (
    LANGUAGE_DISPLAY_NAMES,
    OUTPUT_FORMAT_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    get_manual_source_language_options,
    get_target_language_options,
    get_ui_language,
    target_language_osc_value,
)

if TYPE_CHECKING:
    from src.audio.desktop_recorder import DesktopAudioRecorder
    from src.audio.recorder import AudioRecorder
    from src.osc.sender import VRCOSCSender
    from src.tts.manager import TTSManager
    from src.updater.update_checker import UpdateInfo

logger = logging.getLogger(__name__)

# --- Constants ---
MIC_SOURCE = "mic"
DESKTOP_SOURCE = "vrc_listen"
PARTIAL_TASK_QUEUE_MAXSIZE = 1
FINAL_TASK_QUEUE_MAXSIZE = 1
DESKTOP_FINAL_TASK_QUEUE_MAXSIZE = 1
CONFIG_SAVE_DEBOUNCE_MS = 280
MAIN_WINDOW_DEFAULT_SIZE = (1180, 720)
MAIN_WINDOW_MIN_SIZE = (1040, 640)
HEADER_ACTION_WIDTH = 148
UI_CALLBACK_DRAIN_MS = 25
UI_CALLBACK_DRAIN_LIMIT = 128
GITHUB_REPO_URL = "https://github.com/CokoIya/MioVRC_Translator"
QQ_GROUP_URL = "https://qm.qq.com/q/1PThd3QBTS"
LINE_GROUP_URL = "https://line.me/ti/g2/uLhASjhfQcsd5tYsEpFr8GWsCcuYVIq1I6iGwA?utm_source=invitation&utm_medium=link_copy&utm_campaign=default"
ICON_GITHUB_FILE = "github-brand.svg"
ICON_QQ_FILE = "qq-brand.svg"
ICON_LINE_FILE = "line-brand.svg"
ICON_SPONSOR_FILE = "sponsor.svg"
APP_ICON_PNG_FILE = "app_icon_mio.png"
LISTEN_TTS_ECHO_SUPPRESS_PENDING_S = 3.0
LISTEN_TTS_ECHO_SUPPRESS_TAIL_S = 0.6
SPONSOR_IMAGE_CANDIDATES = (
    "sponsor_qr.png",
    "sponsor_qr.jpg",
    "sponsor_qr.jpeg",
    "zanzhu.png",
    "sponsor.png",
    "sponsor.jpg",
)

TRANSLATION_FAILURE_COOLDOWN_S = {
    "quota": 18.0,
    "network": 12.0,
    "ready": 8.0,
    "empty": 5.0,
    "auth": 45.0,
    "config": 45.0,
    "model": 45.0,
    "parameter": 45.0,
    "dependency": 45.0,
}
TRANSLATION_FAILURE_MAX_COOLDOWN_S = 60.0

DEFAULT_LISTEN_SELF_SUPPRESS_S = 0.65
DEFAULT_LISTEN_SEGMENT_DURATION_S = 2.0
DEFAULT_LISTEN_TAIL_SILENCE_S = 0.65
LISTEN_DIAGNOSTIC_IDLE_S = 15.0
LISTEN_VIRTUAL_OUTPUT_TOKENS = (
    "mixline",
    "mix line",
    "vb-audio",
    "voicemeeter",
    "cable",
    "sonar",
    "asio",
    "vadpro",
)
LISTEN_REAL_OUTPUT_HINTS = (
    "headphone",
    "headphones",
    "speaker",
    "speakers",
    "realtek",
    "pico",
    "quest",
    "oculus",
    "usb audio",
)

TRANSLATION_COPY = {
    "status_ready": {"zh-CN": "就绪", "en": "Ready", "ja": "準備完了", "ru": "Готово", "ko": "준비됨"},
    "status_running": {"zh-CN": "监听中…", "en": "Listening…", "ja": "リスニング中…", "ru": "Прослушивание…", "ko": "듣는 중…"},
    "status_error": {"zh-CN": "错误", "en": "Error", "ja": "エラー", "ru": "Ошибка", "ko": "오류"},
    "starting": {"zh-CN": "启动中…", "en": "Starting…", "ja": "起動中…", "ru": "Запуск…", "ko": "시작 중…"},
    "model_ready": {"zh-CN": "语音包已准备好", "en": "Model ready", "ja": "モデル準備完了", "ru": "Модель готова", "ko": "모델 준비됨"},
    "model_unloaded": {"zh-CN": "语音包未准备好", "en": "Model unloaded", "ja": "モデル未ロード", "ru": "Модель не загружена", "ko": "모델 미로드"},
    "listen_start_failed_title": {"zh-CN": "启动失败", "en": "Start Failed", "ja": "起動失敗", "ru": "Ошибка запуска", "ko": "시작 실패"},
    "send_failed_title": {"zh-CN": "发送失败", "en": "Send Failed", "ja": "送信失敗", "ru": "Ошибка отправки", "ko": "전송 실패"},
    "window_title": {"zh-CN": "Mio 实时翻译", "en": "Mio RealTime Translator", "ja": "Mio リアルタイム翻訳", "ru": "Mio Realtime Translator", "ko": "Mio 실시간 번역"},
    "source_placeholder": {"zh-CN": "等待语音输入或手动输入文本", "en": "Waiting for input or enter text manually", "ja": "音声入力または手動入力待ち", "ru": "Ожидание ввода", "ko": "음성 입력 또는 수동 입력 대기"},
    "translate": {"zh-CN": "翻译", "en": "Translate", "ja": "翻訳", "ru": "Перевод", "ko": "번역"},
    "translating": {"zh-CN": "翻译中...", "en": "Translating...", "ja": "翻訳中...", "ru": "Перевод...", "ko": "번역 중..."},
    "mic_muted_status": {"zh-CN": "● 麦克风已静音", "en": "● Mic muted", "ja": "● マイクミュート中", "ru": "● Mic muted", "ko": "● 마이크 음소거"},
    "mic_unmuted_status": {"zh-CN": "○ 麦克风正常", "en": "○ Mic unmuted", "ja": "○ マイクミュート解除", "ru": "○ Mic unmuted", "ko": "○ 마이크 음소거 해제"},
}

MAIN_COPY = {
    "settings_short": {
        "zh-CN": "设置",
        "en": "Settings",
        "ja": "設定",
        "ru": "Настройки",
        "ko": "설정",
    },
    "listen_prefix": {
        "zh-CN": "[听]",
        "en": "[Listen]",
        "ja": "[聞く]",
        "ru": "[Слушаю]",
        "ko": "[듣기]",
    },
    "guide_short": {
        "zh-CN": "VRChat 开关",
        "en": "OSC",
        "ja": "OSC",
        "ru": "OSC",
        "ko": "OSC",
    },
    "theme_to_light": {
        "zh-CN": "切换到浅色",
        "en": "Switch to light",
        "ja": "ライトに切替",
        "ru": "Светлая тема",
        "ko": "라이트로 전환",
    },
    "theme_to_dark": {
        "zh-CN": "切换到深色",
        "en": "Switch to dark",
        "ja": "ダークに切替",
        "ru": "Темная тема",
        "ko": "다크로 전환",
    },
    "theme_follow_system": {
        "zh-CN": "跟随系统主题",
        "en": "Follow system theme",
        "ja": "システムテーマに追従",
        "ru": "Следовать системе",
        "ko": "시스템 테마 따르기",
    },
    "report_network_error": {
        "zh-CN": "网络错误",
        "en": "Network error",
        "ja": "ネットワークエラー",
        "ru": "Ошибка сети",
        "ko": "네트워크 오류",
    },
    "report_request_limited": {
        "zh-CN": "请求受限",
        "en": "Request limited",
        "ja": "リクエスト制限",
        "ru": "Запрос ограничен",
        "ko": "요청 제한",
    },
    "report_config_error": {
        "zh-CN": "配置错误",
        "en": "Config error",
        "ja": "設定エラー",
        "ru": "Ошибка настроек",
        "ko": "설정 오류",
    },
    "report_runtime_error": {
        "zh-CN": "运行错误",
        "en": "Runtime error",
        "ja": "実行エラー",
        "ru": "Ошибка выполнения",
        "ko": "실행 오류",
    },
    "sponsors_btn": {
        "zh-CN": "感谢赞助",
        "en": "Sponsors",
        "ja": "支援者",
        "ru": "Спонсоры",
        "ko": "후원자",
    },
    "mode_translation": {
        "zh-CN": "翻译",
        "en": "Translate",
        "ja": "翻訳",
        "ru": "Перевод",
        "ko": "번역",
    },
    "mode_simultaneous": {
        "zh-CN": "朗读",
        "en": "Simul",
        "ja": "同通",
        "ru": "Синхронно",
        "ko": "동시통역",
    },
    "swap_languages": {
        "zh-CN": "交换语言",
        "en": "Swap languages",
        "ja": "言語を入れ替え",
        "ru": "Поменять языки",
        "ko": "언어 바꾸기",
    },
    "mode_switched_translation": {
        "zh-CN": "已切换到只翻译文字，Mio 不会朗读。",
        "en": "Translation mode enabled. TTS is off.",
        "ja": "翻訳モードに切り替えました。TTS はオフです。",
        "ru": "Включен режим перевода. TTS выключен.",
        "ko": "번역 모드로 전환했습니다. TTS는 꺼졌습니다.",
    },
    "mode_switched_simultaneous": {
        "zh-CN": "已切换到朗读模式，Mio 会自动读出翻译，也可以送进 VRChat。",
        "en": "Simultaneous mode enabled. TTS will auto-read and route to VRChat.",
        "ja": "同通モードに切り替えました。TTS は自動読み上げで VRChat へ出力されます。",
        "ru": "Включен синхронный режим. TTS будет читать и выводить звук в VRChat.",
        "ko": "동시통역 모드로 전환했습니다. TTS가 자동으로 읽고 VRChat으로 출력됩니다.",
    },
    "app_subtitle": {
        "zh-CN": "VRChat 实时翻译伴侣",
        "en": "VRChat realtime companion",
        "ja": "VRChat リアルタイム翻訳",
        "ru": "VRChat переводчик в реальном времени",
        "ko": "VRChat 실시간 번역 도우미",
    },
    "source_panel": {
        "zh-CN": "原文",
        "en": "Source",
        "ja": "原文",
        "ru": "Исходный текст",
        "ko": "원문",
    },
    "translation_panel": {
        "zh-CN": "译文",
        "en": "Translation",
        "ja": "翻訳",
        "ru": "Перевод",
        "ko": "번역",
    },
    "quick_controls": {
        "zh-CN": "快捷控制",
        "en": "Quick Controls",
        "ja": "クイック操作",
        "ru": "Быстрые действия",
        "ko": "빠른 조작",
    },
    "desktop_audio_off": {
        "zh-CN": "听别人",
        "en": "Reverse TL",
        "ja": "逆翻訳",
        "ru": "Обратный перевод",
        "ko": "역번역",
    },
    "desktop_audio_on": {
        "zh-CN": "听别人",
        "en": "Reverse TL",
        "ja": "逆翻訳",
        "ru": "Обратный перевод",
        "ko": "역번역",
    },
    "listen_overlay_off": {
        "zh-CN": "悬浮窗",
        "en": "Overlay",
        "ja": "オーバーレイ",
        "ru": "Оверлей",
        "ko": "오버레이",
    },
    "listen_overlay_on": {
        "zh-CN": "悬浮窗",
        "en": "Overlay",
        "ja": "オーバーレイ",
        "ru": "Оверлей",
        "ko": "오버레이",
    },
    "mic_mute_off": {
        "zh-CN": "静音",
        "en": "Mute",
        "ja": "ミュート",
        "ru": "Микрофон",
        "ko": "음소거",
    },
    "mic_mute_on": {
        "zh-CN": "已静音",
        "en": "Muted",
        "ja": "ミュート中",
        "ru": "Микрофон выкл.",
        "ko": "음소거 중",
    },
    "mic_device_none": {
        "zh-CN": "未选择麦克风",
        "en": "No microphone selected",
        "ja": "マイク未選択",
        "ru": "Микрофон не выбран",
        "ko": "마이크 미선택",
    },
    "mic_device_auto_option": {
        "zh-CN": "自动跟随系统默认",
        "en": "Auto Follow System Default",
        "ja": "システム既定を自動追従",
        "ru": "Авто: системный по умолчанию",
        "ko": "시스템 기본 장치 자동 추종",
    },
    "mic_device_auto_current": {
        "zh-CN": "自动 · {name}",
        "en": "Auto · {name}",
        "ja": "自動 · {name}",
        "ru": "Авто · {name}",
        "ko": "자동 · {name}",
    },
    "desktop_audio_saved": {
        "zh-CN": "听别人说话已切换",
        "en": "VRC listen updated",
        "ja": "VRC 音声リスンを更新しました",
        "ru": "Обратный перевод обновлен",
        "ko": "VRC 음성 리슨이 변경되었습니다",
    },
    "vrc_listen_device_missing": {
        "zh-CN": "未检测到可用的桌面音频设备",
        "en": "No desktop audio device was detected",
        "ja": "利用可能なデスクトップ音声デバイスが見つかりません",
        "ru": "Устройство звука рабочего стола не найдено",
        "ko": "사용 가능한 데스크톱 오디오 장치를 찾지 못했습니다",
    },
    "chatbox_send_not_queued": {
        "zh-CN": "聊天框发送未排队",
        "en": "Chatbox send was not queued",
        "ja": "チャットボックス送信をキューに入れられませんでした",
        "ru": "Отправка в чат не поставлена в очередь",
        "ko": "채팅박스 전송이 대기열에 들어가지 않았습니다",
    },
    "update_badge": {
        "zh-CN": "新版本",
        "en": "Update",
        "ja": "更新",
        "ru": "Обновление",
        "ko": "업데이트",
    },
}


def _normalize_main_theme(theme: object) -> str:
    return normalize_theme(theme)


def _normalize_main_theme_preference(theme: object) -> str:
    return normalize_theme_preference(theme)


def _resolve_main_theme(theme_preference: object) -> str:
    return resolve_theme(theme_preference)


def _main_theme_preference_from_config(config: dict) -> str:
    return theme_preference_from_config(config)


def _main_theme_from_config(config: dict) -> str:
    return resolve_theme(theme_preference_from_config(config))


def _main_theme_palette(theme: str) -> dict[str, str | int]:
    return theme_tokens(theme)


def create_asr(config: dict, engine: str | None = None):
    from src.asr.factory import create_asr as _create_asr

    return _create_asr(config, engine=engine)


def create_translator(config: dict):
    from src.translators.factory import create_translator as _create_translator

    return _create_translator(config)


def default_output_device_name() -> str | None:
    from src.audio.desktop_recorder import default_output_device_name as _default_output_device_name

    return _default_output_device_name()


def _list_desktop_output_devices() -> list[dict]:
    from src.audio.desktop_recorder import list_output_devices as _list_out
    return _list_out()


def find_best_virtual_output_device():
    from src.tts.manager import find_best_virtual_output_device as _find_best_virtual_output_device

    return _find_best_virtual_output_device()


def check_for_update(
    on_update: Callable[[UpdateInfo | None], None],
    *,
    on_no_update: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    from src.updater.update_checker import check_for_update as _check_for_update

    _check_for_update(on_update, on_no_update=on_no_update, on_error=on_error)


def _list_microphone_devices() -> list[dict]:
    from src.audio.recorder import AudioRecorder

    return AudioRecorder.list_devices()


def _normalize_chatbox_text(text: str) -> str:
    from src.osc.sender import VRCOSCSender

    return VRCOSCSender._normalize_text(text)


def _coerce_osc_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return None


def _main_asr_engine(config: dict) -> str:
    try:
        return get_asr_runtime_spec(config).engine
    except Exception:
        return "sensevoice-small"


def _listen_asr_engine(config: dict) -> str:
    vrc_cfg = config.get("vrc_listen", {}) if isinstance(config, dict) else {}
    if not isinstance(vrc_cfg, dict):
        return _main_asr_engine(config)
    engine = str(vrc_cfg.get("asr_engine", ASR_ENGINE_FOLLOW_MAIN) or "").strip()
    if not engine or engine == ASR_ENGINE_FOLLOW_MAIN or engine not in LISTEN_SELECTABLE_ASR_ENGINES:
        return _main_asr_engine(config)
    return normalize_asr_engine(engine)


def _listen_asr_reuses_main(config: dict) -> bool:
    vrc_cfg = config.get("vrc_listen", {}) if isinstance(config, dict) else {}
    if not isinstance(vrc_cfg, dict):
        return True
    engine = str(vrc_cfg.get("asr_engine", ASR_ENGINE_FOLLOW_MAIN) or "").strip()
    return not engine or engine == ASR_ENGINE_FOLLOW_MAIN or engine not in LISTEN_SELECTABLE_ASR_ENGINES


def _asr_runtime_signature(config: dict, engine: str) -> tuple[str, str, str, bool]:
    spec = get_asr_runtime_spec(config, engine)
    return spec.engine, spec.model_id, spec.model_revision, spec.requires_local_model


def _create_asr_pair(config: dict):
    main_engine = _main_asr_engine(config)
    listen_engine = _listen_asr_engine(config)
    main_asr = create_asr(config, engine=main_engine)
    if _listen_asr_reuses_main(config) and _asr_runtime_signature(config, listen_engine) == _asr_runtime_signature(config, main_engine):
        return main_asr, main_asr
    return main_asr, create_asr(config, engine=listen_engine)


# ----------------------------------------------------------------
# BackgroundWidget
# ----------------------------------------------------------------
class BackgroundWidget(QWidget):
    def __init__(self, background_path: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._resize_timer: QTimer | None = None
        self._theme = "dark"
        self.setAutoFillBackground(False)
        self.set_background_path(background_path)

    def set_theme(self, theme: str) -> None:
        self._theme = _normalize_main_theme(theme)
        self.update()

    def set_background_path(self, background_path: str) -> None:
        path = Path(background_path).expanduser() if background_path else None
        if path and path.is_file():
            self._pixmap = QPixmap(str(path))
        else:
            self._pixmap = QPixmap()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self._resize_timer is not None:
            self._resize_timer.stop()
        self._resize_timer = QTimer.singleShot(80, self.update)
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        palette = _main_theme_palette(self._theme)
        painter.fillRect(rect, QColor(str(palette["APP_BG"])))

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            overlay = QColor(5, 7, 12, 118) if self._theme == "dark" else QColor(247, 250, 255, 58)
            painter.fillRect(rect, overlay)
        super().paintEvent(event)


# ----------------------------------------------------------------
# Qt MainWindow with real backend wiring
# ----------------------------------------------------------------
class MainWindow(QMainWindow):
    sig_status = Signal(str)
    sig_bottom = Signal(str)
    sig_ui_callback = Signal()

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config
        self._destroying = False
        self._ui_thread_id = threading.get_ident()
        self._ui_callback_queue: queue.Queue[tuple[int, object]] = queue.Queue()

        # Shared state for the realtime tweaks panel.
        self._state = AppState()

        # --- Runtime state ---
        self._running = False
        self._listen_session = 0
        self._startup_cancel_event = threading.Event()
        self._recorder: AudioRecorder | None = None
        self._listen_recorder: DesktopAudioRecorder | None = None
        self._asr = None
        self._listen_asr = None
        self._translator = None
        self._output_dispatcher = OutputDispatcher(lambda: getattr(self, "_config", {}))
        self._mic_pipeline: MicPipeline | None = None
        self._listen_pipeline: ListenPipeline | None = None
        self._manual_translation_controller: ManualTranslationController | None = None
        self._sender: VRCOSCSender | None = None
        self._osc_service = None
        self._overlay_service: OverlayService | None = None
        self._tts_manager: TTSManager | None = None
        self._tts_enabled = bool(config.get("tts", {}).get("enabled", False))
        self._mic_muted = False
        self._mic_in_speech = False
        self._listen_in_speech = False
        self._translating = False
        self._translation_state_lock = threading.Lock()
        self._active_translation_jobs = 0
        self._translation_failure_streak = 0
        self._translation_cooldown_until = 0.0
        self._translation_cooldown_category: str | None = None
        self._listen_tts_echo_suppress_until = 0.0
        self._listen_tts_echo_pending_count = 0
        self._listen_tts_echo_lock = threading.Lock()
        self._last_tts_text = ""
        self._last_tts_at = 0.0
        self._tts_dedup_s = 0.5  # Skip TTS if same text within this window
        self._devices: dict[str, int] = {}
        self._desktop_devices: dict[str, int] = {}
        self._devices_loading = False
        self._active_mic_input_device_name: str | None = None
        self._active_listen_output_device_name: str | None = None
        self._listen_available = False

        # --- Mic audio watch ---
        self._mic_recovery_in_progress = False
        self._last_mic_device_signature: tuple[tuple[str, ...], str | None, str, str | None, str | None] | None = None
        self._mic_audio_watch_timer: QTimer | None = None
        self._last_mic_started_at = 0.0
        self._last_mic_result_at = 0.0
        self._last_mic_diagnostic_log_at = 0.0
        self._desktop_in_speech = False

        # --- Listen diagnostics ---
        self._last_listen_started_at = 0.0
        self._last_listen_result_at = 0.0
        self._last_listen_diagnostic_log_at = 0.0
        self._last_desktop_device_signature = None

        # --- Language ---
        self._current_tgt_lang: str = config.get("translation", {}).get("target_language", "ja")
        self._current_tgt_lang_2: str = config.get("translation", {}).get("target_language_2", "en")
        self._current_tgt_lang_3: str = config.get("translation", {}).get("target_language_3", "")
        self._current_src_lang: str | None = None
        self._current_asr_lang: str | None = None

        # --- Text ---
        self._src_text = ""
        self._src_placeholder = self._t("source_placeholder")
        self._src_rendered_text = ""
        self._src_rendered_count = 0
        self._last_tgt_text = ""
        self._last_tgt2_text = ""
        self._last_tgt3_text = ""
        self._tgt_rendered_text = ""
        self._tgt_rendered_is_error = False
        self._manual_translation_generation = 0
        self._manual_send_after_translate = False
        self._manual_done_callback = None

        # --- Mode ---
        self._mode_manager = ModeManager(
            config,
            virtual_device_resolver=find_best_virtual_output_device,
        )
        self._initial_mode_change = self._mode_manager.apply_current_mode()
        self._sync_tts_enabled_from_config()

        # --- Config save ---
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.timeout.connect(self._flush_config_save)

        # --- Hotkey ---
        self._text_input_hotkey: GlobalHotkey | None = None
        self._mic_mute_hotkey: GlobalHotkey | None = None

        # --- Language ---
        self._ui_lang = get_ui_language(config)
        self._src_lang_var = _LangVar()
        self._tgt_lang_var = _LangVar()
        self._target_lang_codes: dict[str, str] = {}
        self._src_lang_codes: dict[str, str] = {}
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._all_manual_lang_options = list(get_manual_source_language_options(ui_language=self._ui_lang))
        self._all_target_lang_options = list(get_target_language_options(ui_language=self._ui_lang))
        self._desktop_capture_enabled = bool(
            self._config.get("vrc_listen", {}).get("enabled", False)
        )
        self._listen_overlay_enabled = bool(
            self._config.get("vrc_listen", {}).get("show_overlay", False)
        )

        # --- Theme ---
        self._main_theme_preference = _main_theme_preference_from_config(config)
        self._main_theme = _resolve_main_theme(self._main_theme_preference)

        # --- Workers ---
        self._partial_workers: dict[str, threading.Thread] = {}
        self._final_workers: dict[str, threading.Thread] = {}
        self._partial_task_queues: dict[str, queue.Queue] = {}
        self._final_task_queues: dict[str, queue.Queue] = {}
        self._partial_generation = 0

        # --- Avatar ---
        self._avatar_error_after_id: str | None = None

        # --- UI widgets ---
        self._status_label: QLabel | None = None
        self._bottom_bar: QLabel | None = None
        self._bottom_progress: QProgressBar | None = None
        self._status_key = "status_ready"
        self._status_color = "success"
        self._bottom_text = ""
        self._bottom_key: str | None = "status_ready"
        self._bottom_color = "success"
        self._bottom_progress_visible = False
        self._bottom_progress_value = 0.0
        self._src_text_widget: QPlainTextEdit | None = None
        self._tgt_text_widget: QPlainTextEdit | None = None
        self._char_label: QLabel | None = None
        self._ui_lang_combo: QComboBox | None = None
        self._src_lang_combo: QComboBox | None = None
        self._tgt_lang_combo: QComboBox | None = None
        self._brand_title_label: QLabel | None = None
        self._creator_banner_label: QLabel | None = None
        self._update_badge_btn: QPushButton | None = None
        self._theme_btn: QPushButton | None = None
        self._settings_btn: QPushButton | None = None
        self._tweaks_btn: QPushButton | None = None
        self._guide_btn: QPushButton | None = None
        self._guide_btn_secondary: QPushButton | None = None
        self._manual_input_btn: QPushButton | None = None
        self._translate_btn: QPushButton | None = None
        self._clear_btn: QPushButton | None = None
        self._copy_source_btn: QPushButton | None = None
        self._copy_result_btn: QPushButton | None = None
        self._send_to_vrc_btn: QPushButton | None = None
        self._start_btn: QPushButton | None = None
        self._mute_btn: QPushButton | None = None
        self._mode_translation_button: QPushButton | None = None
        self._mode_simultaneous_button: QPushButton | None = None
        self._swap_lang_btn: QPushButton | None = None
        self._assist_label: QLabel | None = None
        self._device_combo: QComboBox | None = None
        self._device_dropdown_btn: QPushButton | None = None
        self._desktop_btn: QPushButton | None = None
        self._listen_overlay_btn: QPushButton | None = None
        self._sponsors_btn: QPushButton | None = None
        self._settings_window = None
        self._text_input_window = None
        self._audio_diagnostics_windows: dict[str, QDialog] = {}
        self._vad_calibration_windows: dict[str, QDialog] = {}
        self._mode_wizard_dialog = None
        self._floating_window = None
        self._tweaks_panel = None
        self._sponsor_window = None
        self._social_buttons: list[tuple[QPushButton, str]] = []
        self._update_win = None
        self._pending_update = None
        self._settings_theme_sync_generation = 0

        self.setWindowTitle(self._t("window_title"))
        self.resize(*MAIN_WINDOW_DEFAULT_SIZE)
        self.setMinimumSize(*MAIN_WINDOW_MIN_SIZE)
        self._build_ui()
        self._register_hotkeys()
        self._start_ui_callback_drain()
        self._refresh_static_texts()
        self._refresh_start_button()
        self._refresh_mic_mute_button()
        self._refresh_mode_buttons()
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        self._set_status(self._t("status_ready"), "success", key="status_ready")
        self._set_bottom(self._t("status_ready"), "success", key="status_ready")

        if self._initial_mode_change.changed:
            QTimer.singleShot(500, lambda: self._schedule_config_save())
        # Defer device loading to give the UI a chance to stabilize first
        QTimer.singleShot(40, self._load_devices_async)
        self._schedule_desktop_audio_watch(2500)
        self._schedule_mic_audio_watch(2500)
        QTimer.singleShot(1200, self._maybe_show_mode_wizard)
        QTimer.singleShot(2000, self._maybe_show_osc_guide)
        if self._startup_update_check_enabled():
            QTimer.singleShot(self._startup_update_check_delay_ms(), self._check_for_update)
        self._schedule_settings_preload(450)
        QTimer.singleShot(0, self._apply_osc_listener_config)

        self._subscribe_realtime_tweaks_state()

        logger.info("Qt MainWindow initialized")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if getattr(self, "_device_combo", None) is not None:
            QTimer.singleShot(0, self._refresh_device_combo)

    # ----------------------------------------------------------------
    # Public
    # ----------------------------------------------------------------
    def _create_settings_window(self, *, preload: bool = False, defer_initial_page: bool = False):
        from src.ui_qt.settings_window import SettingsWindow

        win = SettingsWindow(
            self,
            self._config,
            on_save=self._on_config_saved,
            on_close=lambda: setattr(self, "_settings_window", None),
            on_listen_state_changed=self._on_settings_listen_state_changed,
            on_theme_changed=self._on_settings_theme_changed,
            on_audio_diagnostics_requested=self._open_audio_diagnostics_window,
            on_vad_calibration_requested=self._open_vad_calibration_window,
            on_mode_wizard_requested=self.open_mode_wizard,
            preload=preload,
            defer_initial_page=defer_initial_page,
        )
        self._settings_window = win
        self._sync_settings_window_vrc_listen_state()
        return win

    def _preload_settings_window(self) -> None:
        if self._destroying or self._settings_window is not None:
            return
        try:
            self._create_settings_window(preload=True)
        except Exception:
            logger.debug("Failed to preload settings window", exc_info=True)

    def _settings_preload_enabled(self) -> bool:
        return self._current_tts_engine() != "style_bert_vits2"

    def _schedule_settings_preload(self, delay_ms: int) -> None:
        if not self._settings_preload_enabled():
            logger.debug("Skipping settings preload while Style-Bert-VITS2 is selected")
            return
        QTimer.singleShot(delay_ms, self._preload_settings_window)

    def show_settings(self, page_id: str | None = None) -> None:
        if self._settings_window is not None and getattr(self._settings_window, "_closing", False):
            self._settings_window = None
        if self._settings_window is not None:
            self._sync_settings_window_vrc_listen_state()
            if page_id and hasattr(self._settings_window, "select_page"):
                self._settings_window.select_page(page_id)
            self._settings_window.show()
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        win = self._create_settings_window(defer_initial_page=True)
        if page_id and hasattr(win, "select_page"):
            win.select_page(page_id)
        win.show()
        win.raise_()
        win.activateWindow()

    def open_mode_wizard(self) -> None:
        self._open_mode_wizard(mark_seen=True)

    def destroy(self) -> None:
        self._shutdown()
        super().close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        logger.info("Qt MainWindow shutdown requested")
        self._stop_hotkeys()
        self._close_independent_tool_windows()
        self._flush_config_save()
        if self._running:
            self._do_stop()
        else:
            self._stop_listen()
            self._stop_microphone_capture()
        self._stop_workers()
        self._reset_tts_manager()
        service = getattr(self, "_osc_service", None)
        if service is not None:
            try:
                service.close()
            except Exception:
                pass
            self._osc_service = None
            self._sender = None
        elif self._sender:
            try:
                self._sender.close()
            except Exception:
                pass
            self._sender = None

    def _close_independent_tool_windows(self) -> None:
        text_window = self._text_input_window
        self._text_input_window = None
        if text_window is not None:
            try:
                text_window.close()
            except Exception:
                logger.debug("Failed to close text input window during shutdown", exc_info=True)

        floating_window = self._floating_window
        self._floating_window = None
        if floating_window is not None:
            previous_on_close = getattr(floating_window, "_on_close", None)
            try:
                floating_window._on_close = None
                floating_window.close()
            except Exception:
                logger.debug("Failed to close floating window during shutdown", exc_info=True)
            finally:
                try:
                    floating_window._on_close = previous_on_close
                except Exception:
                    pass

        for windows_attr in ("_audio_diagnostics_windows", "_vad_calibration_windows"):
            windows = getattr(self, windows_attr, {})
            if isinstance(windows, dict):
                for tool_window in list(windows.values()):
                    try:
                        tool_window.close()
                    except Exception:
                        logger.debug("Failed to close tool window during shutdown", exc_info=True)
                windows.clear()
        mode_wizard = getattr(self, "_mode_wizard_dialog", None)
        self._mode_wizard_dialog = None
        if mode_wizard is not None:
            try:
                mode_wizard.close()
            except Exception:
                logger.debug("Failed to close mode wizard during shutdown", exc_info=True)

        settings_window = self._settings_window
        self._settings_window = None
        if settings_window is not None:
            try:
                settings_window.close()
            except Exception:
                logger.debug("Failed to close settings window during shutdown", exc_info=True)

    # ----------------------------------------------------------------
    # UI Construction
    # ----------------------------------------------------------------
    def _build_ui(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_app_stylesheet(self._main_theme))
        self.setStyleSheet(build_main_window_styles(self._main_theme))
        apply_window_chrome_theme(self, self._main_theme)

        background = BackgroundWidget(self._background_image_path(), self)
        background.set_theme(self._main_theme)
        self.setCentralWidget(background)

        outer_layout = QVBoxLayout(background)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        shell = QFrame(background)
        shell.setObjectName("appChrome")
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_shadow(shell, blur=36, alpha=20 if self._main_theme == "light" else 78, y_offset=12)
        outer_layout.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(6)
        shell_layout.addWidget(self._build_header())
        shell_layout.addWidget(self._build_content(), 1)
        shell_layout.addWidget(self._build_footer())

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("headerPanel")
        header.setFixedHeight(78)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        icon_label = QLabel()
        icon = self._load_icon_pixmap(APP_ICON_PNG_FILE, 44)
        if icon is not None:
            icon_label.setPixmap(icon)
        icon_label.setFixedSize(44, 44)
        brand.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        self._brand_title_label = QLabel(self._t("window_title"))
        self._brand_title_label.setObjectName("brandTitle")
        self._creator_banner_label = QLabel(self._t("creator_banner"))
        self._creator_banner_label.setObjectName("brandSubtitle")
        self._creator_banner_label.setWordWrap(True)
        brand_text.addWidget(self._brand_title_label)
        brand_text.addWidget(self._creator_banner_label)
        brand.addLayout(brand_text)
        layout.addLayout(brand, 1)

        self._status_label = QLabel(self._t("status_ready"))
        self._status_label.setObjectName("statusPill")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setFixedHeight(30)
        self._status_label.setMinimumWidth(132)
        self._status_label.setMaximumWidth(220)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._update_badge_btn = QPushButton(self._t("update_badge"))
        self._update_badge_btn.setObjectName("updateBadge")
        self._update_badge_btn.setFixedHeight(30)
        self._update_badge_btn.clicked.connect(self._open_update_window)
        self._update_badge_btn.hide()
        layout.addWidget(self._update_badge_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._ui_lang_combo = NoWheelComboBox()
        self._ui_lang_combo.setObjectName("headerCombo")
        self._ui_lang_combo.setFixedSize(HEADER_ACTION_WIDTH, 40)
        self._ui_lang_combo.addItems([label for label, _ in UI_LANGUAGE_OPTIONS])
        self._ui_lang_combo.currentTextChanged.connect(self._on_ui_lang_selected)
        action_row.addWidget(self._ui_lang_combo)

        self._settings_btn = QPushButton(self._copy("settings_short"))
        self._settings_btn.setObjectName("headerButton")
        self._settings_btn.setFixedSize(HEADER_ACTION_WIDTH, 40)
        self._settings_btn.clicked.connect(self.show_settings)
        action_row.addWidget(self._settings_btn)

        # 实时调整按钮
        self._tweaks_btn = QPushButton("实时")
        self._tweaks_btn.setObjectName("headerButton")
        self._tweaks_btn.setToolTip("实时调整参数（麦克风、VAD、TTS等）")
        self._tweaks_btn.setFixedSize(HEADER_ACTION_WIDTH, 40)
        self._tweaks_btn.clicked.connect(self._toggle_tweaks_panel)
        self._refresh_tweaks_button()
        action_row.addWidget(self._tweaks_btn)

        self._theme_btn = QPushButton("")
        self._theme_btn.setObjectName("themeIconButton")
        self._theme_btn.setFixedSize(40, 40)
        self._theme_btn.setIconSize(QSize(17, 17))
        self._theme_btn.clicked.connect(self._on_theme_toggle)
        action_row.addWidget(self._theme_btn)

        layout.addLayout(action_row)
        return header

    def _build_content(self) -> QWidget:
        content = QFrame()
        content.setObjectName("workspacePanel")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_translation_card(), 5)
        layout.addWidget(self._build_side_card(), 2)
        return content

    def _build_translation_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("translationCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        flow_panel = QFrame()
        flow_panel.setObjectName("langFlowPanel")
        flow_layout = QHBoxLayout(flow_panel)
        flow_layout.setContentsMargins(12, 10, 12, 10)
        flow_layout.setSpacing(10)
        self._src_header_label = QLabel(self._copy("source_panel"))
        self._src_header_label.setObjectName("sectionTitleMain")
        flow_layout.addWidget(self._src_header_label)
        self._src_lang_combo = NoWheelComboBox()
        self._src_lang_combo.setObjectName("langCombo")
        self._src_lang_combo.setFixedSize(132, 32)
        self._src_lang_combo.currentTextChanged.connect(self._on_src_lang_change)
        flow_layout.addWidget(self._src_lang_combo)
        self._swap_lang_btn = QPushButton("")
        self._swap_lang_btn.setObjectName("swapIconButton")
        self._swap_lang_btn.setFixedSize(32, 32)
        self._swap_lang_btn.setIconSize(QSize(15, 15))
        self._swap_lang_btn.clicked.connect(self._swap_langs)
        flow_layout.addWidget(self._swap_lang_btn)
        flow_layout.addStretch(1)
        self._char_label = QLabel(self._t("char_count", count=0))
        self._char_label.setObjectName("counterLabel")
        flow_layout.addWidget(self._char_label)
        self._tgt_header_label = QLabel(self._copy("translation_panel"))
        self._tgt_header_label.setObjectName("sectionTitleMain")
        flow_layout.addWidget(self._tgt_header_label)
        self._tgt_lang_combo = NoWheelComboBox()
        self._tgt_lang_combo.setObjectName("langCombo")
        self._tgt_lang_combo.setFixedSize(146, 32)
        self._tgt_lang_combo.currentTextChanged.connect(self._on_tgt_lang_change)
        flow_layout.addWidget(self._tgt_lang_combo)
        layout.addWidget(flow_panel)

        panes = QHBoxLayout()
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(12)

        self._left_panel = self._panel()
        self._left_panel.setObjectName("editorPanel")
        self._left_panel.setProperty("role", "source")
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        self._src_text_widget = QPlainTextEdit()
        self._src_text_widget.setObjectName("textPane")
        self._src_text_widget.setPlaceholderText(self._src_placeholder)
        self._src_text_widget.setReadOnly(True)
        self._src_text_widget.setMinimumHeight(280)
        left_layout.addWidget(self._src_text_widget, 1)
        panes.addWidget(self._left_panel, 1)

        self._right_panel = self._panel()
        self._right_panel.setObjectName("editorPanel")
        self._right_panel.setProperty("role", "target")
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)
        self._tgt_text_widget = QPlainTextEdit()
        self._tgt_text_widget.setObjectName("textPane")
        self._tgt_text_widget.setReadOnly(True)
        self._tgt_text_widget.setMinimumHeight(280)
        right_layout.addWidget(self._tgt_text_widget, 1)
        panes.addWidget(self._right_panel, 1)
        layout.addLayout(panes, 1)

        action_strip = QFrame()
        action_strip.setObjectName("actionStrip")
        action_layout = QHBoxLayout(action_strip)
        action_layout.setContentsMargins(10, 10, 10, 10)
        action_layout.setSpacing(8)

        self._manual_input_btn = QPushButton(self._t("manual_input"))
        self._manual_input_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._manual_input_btn, min_width=104, height=38)
        self._manual_input_btn.clicked.connect(self._open_text_input_popup)
        action_layout.addWidget(self._manual_input_btn)

        self._translate_btn = QPushButton(self._t("translate"))
        self._translate_btn.setObjectName("primaryButton")
        self._fit_button_to_text(self._translate_btn, min_width=88, height=38)
        self._translate_btn.clicked.connect(self._on_translate_clicked)
        action_layout.addWidget(self._translate_btn)

        self._clear_btn = QPushButton(self._t("clear"))
        self._clear_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._clear_btn, min_width=84, height=38)
        self._clear_btn.clicked.connect(self._clear_input)
        action_layout.addWidget(self._clear_btn)

        action_layout.addStretch(1)

        self._copy_source_btn = QPushButton(self._t("copy_source"))
        self._copy_source_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._copy_source_btn, min_width=112, height=38)
        self._copy_source_btn.clicked.connect(self._copy_source)
        action_layout.addWidget(self._copy_source_btn)

        self._copy_result_btn = QPushButton(self._t("copy"))
        self._copy_result_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._copy_result_btn, min_width=112, height=38)
        self._copy_result_btn.clicked.connect(self._copy_result)
        action_layout.addWidget(self._copy_result_btn)

        self._send_to_vrc_btn = QPushButton(self._t("send_to_vrc"))
        self._send_to_vrc_btn.setObjectName("primaryButton")
        send_icon = ui_icon("send.svg", 15, "#ffffff")
        if not send_icon.isNull():
            self._send_to_vrc_btn.setIcon(send_icon)
            self._send_to_vrc_btn.setIconSize(QSize(15, 15))
        self._fit_button_to_text(self._send_to_vrc_btn, min_width=142, height=38, icon_gap=18)
        self._send_to_vrc_btn.clicked.connect(self._on_send_clicked)
        action_layout.addWidget(self._send_to_vrc_btn)

        layout.addWidget(action_strip)
        return card

    def _build_side_card(self) -> QFrame:
        tokens = _main_theme_palette(self._main_theme)
        card = QFrame()
        card.setObjectName("sidePanel")
        card.setMinimumWidth(max(286, int(tokens["SIDE_WIDTH"]) - 18))
        card.setMaximumWidth(max(286, int(tokens["SIDE_WIDTH"])))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title_row = QVBoxLayout()
        title_row.setSpacing(4)
        self._quick_controls_label = QLabel(self._copy("quick_controls"))
        self._quick_controls_label.setObjectName("controlSectionTitle")
        title_row.addWidget(self._quick_controls_label)
        self._quick_controls_hint = None
        layout.addLayout(title_row)

        self._start_btn = QPushButton(self._t("start_listening"))
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.setFixedHeight(48)
        self._start_btn.clicked.connect(self._toggle_listening)
        layout.addWidget(self._start_btn)

        mode_box = QFrame()
        mode_box.setObjectName("modeBox")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(4, 4, 4, 4)
        mode_layout.setSpacing(4)
        self._mode_translation_button = QPushButton(self._copy("mode_translation"))
        self._mode_translation_button.setObjectName("modeButton")
        self._mode_translation_button.setCheckable(True)
        self._mode_translation_button.setProperty("modeActive", "false")
        self._mode_translation_button.clicked.connect(lambda: self._set_app_mode(AppMode.TRANSLATION, persist=True))
        self._mode_translation_button.setFixedHeight(36)
        mode_layout.addWidget(self._mode_translation_button, 1)
        self._mode_simultaneous_button = QPushButton(self._copy("mode_simultaneous"))
        self._mode_simultaneous_button.setObjectName("modeButton")
        self._mode_simultaneous_button.setCheckable(True)
        self._mode_simultaneous_button.setProperty("modeActive", "false")
        self._mode_simultaneous_button.clicked.connect(lambda: self._set_app_mode(AppMode.SIMULTANEOUS, persist=True))
        self._mode_simultaneous_button.setFixedHeight(36)
        mode_layout.addWidget(self._mode_simultaneous_button, 1)
        layout.addWidget(mode_box)

        mic_group = QFrame()
        mic_group.setObjectName("controlGroup")
        mic_layout = QVBoxLayout(mic_group)
        mic_layout.setContentsMargins(12, 12, 12, 12)
        mic_layout.setSpacing(8)
        self._microphone_label = QLabel(self._t("microphone"))
        self._microphone_label.setObjectName("controlLabel")
        mic_layout.addWidget(self._microphone_label)
        self._device_combo = NoWheelComboBox()
        self._device_combo.setObjectName("deviceCombo")
        self._device_combo.setFixedHeight(38)
        self._device_combo.currentTextChanged.connect(self._on_device_combo_changed)
        mic_layout.addWidget(self._device_combo)
        self._refresh_device_combo()
        self._device_dropdown_btn = None

        mic_actions = QHBoxLayout()
        mic_actions.setSpacing(8)
        self._mute_btn = QPushButton("")
        self._mute_btn.setObjectName("activeButton")
        self._mute_btn.setFixedHeight(38)
        self._mute_btn.clicked.connect(self._toggle_mic_mute)
        mic_actions.addWidget(self._mute_btn, 1)
        self._desktop_btn = QPushButton("")
        self._desktop_btn.setObjectName("activeButton")
        self._desktop_btn.setFixedHeight(38)
        self._desktop_btn.clicked.connect(self._toggle_listen)
        mic_actions.addWidget(self._desktop_btn, 1)
        mic_layout.addLayout(mic_actions)
        layout.addWidget(mic_group)

        assist_group = QFrame()
        assist_group.setObjectName("controlGroup")
        assist_layout = QVBoxLayout(assist_group)
        assist_layout.setContentsMargins(12, 12, 12, 12)
        assist_layout.setSpacing(8)
        self._assist_label = QLabel(self._copy("guide_short"))
        self._assist_label.setObjectName("controlLabel")
        assist_layout.addWidget(self._assist_label)
        self._listen_overlay_btn = QPushButton("")
        self._listen_overlay_btn.setObjectName("activeButton")
        self._listen_overlay_btn.setFixedHeight(40)
        self._listen_overlay_btn.clicked.connect(self._toggle_listen_overlay)
        assist_layout.addWidget(self._listen_overlay_btn)
        self._guide_btn_secondary = QPushButton(self._copy("guide_short"))
        self._guide_btn_secondary.setObjectName("secondaryButton")
        self._guide_btn_secondary.setFixedHeight(38)
        self._guide_btn_secondary.clicked.connect(self._open_osc_guide)
        assist_layout.addWidget(self._guide_btn_secondary)
        layout.addWidget(assist_group)

        layout.addStretch(1)
        return card

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footerPanel")
        footer.setFixedHeight(58)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(4)
        self._bottom_bar = QLabel(self._bottom_text)
        self._bottom_bar.setObjectName("bottomLabel")
        self._bottom_bar.setWordWrap(False)
        if not self._bottom_text:
            self._bottom_bar.hide()
        left.addWidget(self._bottom_bar)
        self._bottom_progress = QProgressBar()
        self._bottom_progress.setRange(0, 100)
        self._bottom_progress.setValue(0)
        self._bottom_progress.setTextVisible(False)
        self._bottom_progress.setFixedHeight(6)
        self._bottom_progress.hide()
        left.addWidget(self._bottom_progress)
        layout.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(8)
        self._sponsors_btn = QPushButton(self._copy("sponsors_btn"))
        self._sponsors_btn.setObjectName("sponsorButton")
        self._sponsors_btn.setFixedHeight(42)
        self._sponsors_btn.setIconSize(QSize(18, 18))
        self._sponsors_btn.setIcon(ui_icon(ICON_SPONSOR_FILE, 18, "#ffffff"))
        self._sponsors_btn.clicked.connect(self._open_sponsor_window)
        right.addWidget(self._sponsors_btn)
        right.addWidget(self._social_button(ICON_GITHUB_FILE, "Git", GITHUB_REPO_URL))
        right.addWidget(self._social_button(ICON_QQ_FILE, "QQ", QQ_GROUP_URL))
        right.addWidget(self._social_button(ICON_LINE_FILE, "LINE", LINE_GROUP_URL))
        layout.addLayout(right, 0)
        return footer

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("translationPanel")
        panel.setFrameShape(QFrame.Shape.NoFrame)
        return panel

    @staticmethod
    def _fit_button_to_text(btn: QPushButton | None, *, min_width: int, height: int, padding: int = 32, icon_gap: int = 0) -> None:
        if btn is None:
            return
        text_width = btn.fontMetrics().horizontalAdvance(btn.text())
        btn.setFixedSize(max(min_width, text_width + padding + icon_gap), height)

    def _copy(self, key: str, **kwargs) -> str:
        table = MAIN_COPY.get(key)
        if table:
            ui_lang = getattr(self, "_ui_lang", None) or get_ui_language(getattr(self, "_config", {}))
            text = table.get(ui_lang) or table.get(str(ui_lang).split("-")[0]) or table.get("en") or next(iter(table.values()))
            return text.format(**kwargs) if kwargs else text
        return self._t(key, **kwargs)

    def _refresh_static_texts(self) -> None:
        self.setWindowTitle(self._t("window_title"))
        self._src_placeholder = self._t("source_placeholder")
        if self._brand_title_label:
            self._brand_title_label.setText(self._t("window_title"))
        if self._creator_banner_label:
            self._creator_banner_label.setText(self._t("creator_banner"))
        if self._ui_lang_combo:
            label = self._ui_lang_reverse.get(self._ui_lang)
            if label:
                self._set_combo_text(self._ui_lang_combo, label)
        if self._settings_btn:
            self._settings_btn.setText(self._copy("settings_short"))
            self._settings_btn.setFixedSize(HEADER_ACTION_WIDTH, 40)
        self._refresh_tweaks_button()
        if self._guide_btn:
            self._guide_btn.setText(self._copy("guide_short"))
        if self._guide_btn_secondary:
            self._guide_btn_secondary.setText(self._copy("guide_short"))
        if self._sponsors_btn:
            self._sponsors_btn.setText(self._copy("sponsors_btn"))
            self._sponsors_btn.setIcon(ui_icon(ICON_SPONSOR_FILE, 18, "#ffffff"))
        self._refresh_update_badge()
        if self._manual_input_btn:
            self._manual_input_btn.setText(self._t("manual_input"))
            self._fit_button_to_text(self._manual_input_btn, min_width=104, height=38)
        if self._translate_btn:
            self._translate_btn.setText(self._t("translating") if not self._translate_btn.isEnabled() else self._t("translate"))
            self._fit_button_to_text(self._translate_btn, min_width=88, height=38)
        if self._clear_btn:
            self._clear_btn.setText(self._t("clear"))
            self._fit_button_to_text(self._clear_btn, min_width=84, height=38)
        if self._copy_source_btn:
            self._copy_source_btn.setText(self._t("copy_source"))
            self._fit_button_to_text(self._copy_source_btn, min_width=112, height=38)
        if self._copy_result_btn:
            self._copy_result_btn.setText(self._t("copy"))
            self._fit_button_to_text(self._copy_result_btn, min_width=112, height=38)
        if self._send_to_vrc_btn:
            self._send_to_vrc_btn.setText(self._t("send_to_vrc"))
            self._fit_button_to_text(self._send_to_vrc_btn, min_width=142, height=38, icon_gap=18)
        if getattr(self, "_quick_controls_label", None):
            self._quick_controls_label.setText(self._copy("quick_controls"))
        if getattr(self, "_src_header_label", None):
            self._src_header_label.setText(self._copy("source_panel"))
        if getattr(self, "_tgt_header_label", None):
            self._tgt_header_label.setText(self._copy("translation_panel"))
        if getattr(self, "_microphone_label", None):
            self._microphone_label.setText(self._t("microphone"))
        self._refresh_device_combo()
        if getattr(self, "_assist_label", None):
            self._assist_label.setText(self._copy("guide_short"))
        if self._status_label is not None and getattr(self, "_status_key", None):
            self._set_status(self._t(self._status_key), self._status_color, key=self._status_key)
        if self._bottom_bar is not None and getattr(self, "_bottom_key", None):
            self._set_bottom(self._t(self._bottom_key), self._bottom_color, key=self._bottom_key)
        self._refresh_language_combos()
        self._set_source_text(self._src_text)
        self._refresh_start_button()
        self._refresh_mic_mute_button()
        self._refresh_mode_buttons()
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        self._refresh_theme_button()

    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            blocked = combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(blocked)

    def _refresh_language_combos(self) -> None:
        if getattr(self, "_refreshing_language_combos", False):
            return
        self._refreshing_language_combos = True
        try:
            self._all_target_lang_options = list(get_target_language_options(ui_language=self._ui_lang))
            self._target_lang_codes = {label: code for label, code in self._all_target_lang_options}
            target_reverse = {code: label for label, code in self._all_target_lang_options}
            tgt_code = str(self._config.get("translation", {}).get("target_language", self._current_tgt_lang) or "ja")
            self._current_tgt_lang = tgt_code

            self._all_manual_lang_options = list(get_manual_source_language_options({tgt_code}, ui_language=self._ui_lang))
            self._src_lang_codes = {label: code for label, code in self._all_manual_lang_options}
            src_reverse = {code: label for label, code in self._all_manual_lang_options}
            src_code = str(self._config.get("translation", {}).get("source_language", "auto") or "auto")
            self._current_src_lang = None if src_code == "auto" else src_code
            self._current_asr_lang = self._current_src_lang if self._current_src_lang in {"zh", "yue", "ja", "en", "ko"} else None

            if self._src_lang_combo:
                blocked = self._src_lang_combo.blockSignals(True)
                self._src_lang_combo.clear()
                self._src_lang_combo.addItems([label for label, _ in self._all_manual_lang_options])
                self._src_lang_combo.setCurrentText(src_reverse.get(src_code, self._src_lang_combo.itemText(0)))
                self._src_lang_combo.blockSignals(blocked)

            if self._tgt_lang_combo:
                blocked = self._tgt_lang_combo.blockSignals(True)
                self._tgt_lang_combo.clear()
                self._tgt_lang_combo.addItems([label for label, _ in self._all_target_lang_options])
                self._tgt_lang_combo.setCurrentText(target_reverse.get(tgt_code, self._tgt_lang_combo.itemText(0)))
                self._tgt_lang_combo.blockSignals(blocked)
        finally:
            self._refreshing_language_combos = False

    def _on_ui_lang_selected(self, selected_label: str) -> None:
        code = self._ui_lang_codes.get(selected_label)
        if not code or code == self._ui_lang:
            return
        self._ui_lang = code
        self._config.setdefault("ui", {})["language"] = code
        self._refresh_static_texts()
        if self._floating_window is not None:
            try:
                self._floating_window.update_language(code)
            except Exception:
                logger.debug("Failed to update floating window language", exc_info=True)
        if self._text_input_window is not None:
            try:
                self._text_input_window.update_language(code)
            except Exception:
                logger.debug("Failed to update text input window language", exc_info=True)
        if self._tweaks_panel is not None:
            try:
                self._tweaks_panel.update_language(code)
            except Exception:
                logger.debug("Failed to update realtime tweaks panel language", exc_info=True)
        self._schedule_config_save()

    def _on_tgt_lang_change(self, selected_label: str | None = None) -> None:
        if selected_label is None and self._tgt_lang_combo:
            selected_label = self._tgt_lang_combo.currentText()
        code = self._target_lang_codes.get(str(selected_label or ""), self._current_tgt_lang or "ja")
        self._current_tgt_lang = code
        self._config.setdefault("translation", {})["target_language"] = code
        self._refresh_language_combos()
        self._schedule_config_save()

    def _on_src_lang_change(self, selected_label: str | None = None) -> None:
        if selected_label is None and self._src_lang_combo:
            selected_label = self._src_lang_combo.currentText()
        code = self._src_lang_codes.get(str(selected_label or ""), "auto")
        self._current_src_lang = None if code == "auto" else code
        self._current_asr_lang = self._current_src_lang if self._current_src_lang in {"zh", "yue", "ja", "en", "ko"} else None
        self._config.setdefault("translation", {})["source_language"] = code
        if not getattr(self, "_refreshing_language_combos", False):
            self._refresh_language_combos()
            self._schedule_config_save()

    def _swap_langs(self) -> None:
        if not self._src_lang_combo or not self._tgt_lang_combo:
            return
        src_code = self._src_lang_codes.get(self._src_lang_combo.currentText(), "auto")
        tgt_code = self._target_lang_codes.get(self._tgt_lang_combo.currentText(), "ja")
        if src_code == "auto":
            return
        target_reverse = {code: label for label, code in self._all_target_lang_options}
        manual_all = list(get_manual_source_language_options({src_code}, ui_language=self._ui_lang))
        src_reverse = {code: label for label, code in manual_all}
        if tgt_code in src_reverse and src_code in target_reverse:
            self._config.setdefault("translation", {})["source_language"] = tgt_code
            self._config.setdefault("translation", {})["target_language"] = src_code
            self._refresh_language_combos()
            self._set_source_text(self._last_tgt_text or self._src_text)
            self._show_tgt("")
            self._schedule_config_save()

    def _set_source_text(self, text: str, text_color: str | None = None) -> None:
        safe = (text or "").strip()
        if len(safe) > 500:
            safe = safe[:500]
        self._src_text = safe
        shown = safe or getattr(self, "_src_placeholder", "")
        if shown == getattr(self, "_src_rendered_text", "") and len(safe) == getattr(self, "_src_rendered_count", -1):
            return
        self._src_rendered_text = shown
        self._src_rendered_count = len(safe)
        src_text_widget = getattr(self, "_src_text_widget", None)
        if src_text_widget:
            palette = _main_theme_palette(getattr(self, "_main_theme", "dark"))
            src_text_widget.setPlainText(shown)
            src_text_widget.setStyleSheet(
                "QPlainTextEdit#textPane { color: %s; }" % (text_color or (palette["TEXT_PRIMARY"] if safe else palette["EDITOR_MUTED"]))
            )
        char_label = getattr(self, "_char_label", None)
        if char_label:
            char_label.setText(self._t("char_count", count=len(safe)))

    def _open_text_input_popup(self, _event=None) -> None:
        from src.ui_qt.text_input_window import TextInputWindow
        if self._text_input_window is not None and self._text_input_window.isVisible():
            self._text_input_window.raise_()
            self._text_input_window.activateWindow()
            return
        self._text_input_window = TextInputWindow(
            None,
            self._config,
            initial_text=self._src_text,
            on_send=self._translate_and_send_from_text_window,
        )
        self._text_input_window.finished.connect(lambda _result: setattr(self, "_text_input_window", None))
        self._text_input_window.show()
        self._text_input_window.activateWindow()

    def _translate_and_send_from_text_window(self, text: str) -> bool:
        clean = str(text or "").strip()
        if not clean:
            return False
        if self._translating:
            return False
        self._set_source_text(clean)
        self._manual_send_after_translate = True
        self._do_manual_translate()
        return True

    def _clear_input(self) -> None:
        self._set_source_text("")
        self._last_tgt2_text = ""
        self._last_tgt3_text = ""
        self._show_tgt("")

    def _copy_source(self) -> None:
        if self._src_text:
            QApplication.clipboard().setText(self._src_text)

    def _copy_result(self) -> None:
        text = self._tgt_rendered_text or self._last_tgt_text
        if text:
            QApplication.clipboard().setText(text)

    def _toggle_listening(self) -> None:
        self._on_start_clicked()

    def _toggle_mic_mute(self) -> None:
        self._set_mic_muted(not self._mic_muted)

    def _set_mic_muted(self, muted: bool, *, bottom_key: str | None = None) -> None:
        self._mic_muted = bool(muted)
        self._refresh_mic_mute_button()
        key = bottom_key or ("mic_mute_on" if self._mic_muted else "mic_mute_off")
        self._set_bottom(self._copy(key))
        self._sync_avatar_muted_state(force=True)
        self._sync_avatar_speaking_state(force=True)

    def _set_app_mode(self, mode: AppMode, *, persist: bool) -> None:
        try:
            change = self._mode_manager.set_mode(mode)
        except Exception:
            logger.exception("Failed to set application mode")
            return
        self._sync_tts_enabled_from_config()
        if getattr(change, "tts_changed", False) or getattr(change, "output_device_changed", False):
            self._reset_tts_manager()
        self._refresh_mode_buttons()
        if persist:
            self._schedule_config_save()
        if getattr(change, "changed", False):
            self._set_bottom(
                self._copy("mode_switched_simultaneous" if mode is AppMode.SIMULTANEOUS else "mode_switched_translation")
            )

    def _refresh_mode_buttons(self) -> None:
        mode = getattr(self._mode_manager, "mode", AppMode.TRANSLATION)
        for button, active in (
            (self._mode_translation_button, mode is AppMode.TRANSLATION),
            (self._mode_simultaneous_button, mode is AppMode.SIMULTANEOUS),
        ):
            if button is None:
                continue
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.setChecked(active)
            button.setProperty("modeActive", "true" if active else "false")
            button.setText(self._copy("mode_translation") if button is self._mode_translation_button else self._copy("mode_simultaneous"))
            button.style().unpolish(button)
            button.style().polish(button)

    def _current_device_display_name(self, device_name: str | None = None) -> str:
        resolved_name = str(device_name or self._resolve_mic_input_device_name(refresh=False) or "").strip()
        if not resolved_name:
            return self._copy("mic_device_auto_option")
        return resolved_name

    def _device_combo_items(self) -> list[str]:
        auto_label = self._copy("mic_device_auto_option")
        missing_label = self._copy("input_device_missing")
        items = [auto_label, *self._devices.keys()]
        configured = str(self._config.get("audio", {}).get("input_device") or "").strip()
        if configured and configured not in items:
            items.append(configured)
        if len(items) == 1:
            items.append(missing_label)
        return items

    def _refresh_device_combo(self) -> None:
        combo = self._device_combo
        if combo is None:
            return
        current = self._current_device_display_name()
        items = self._device_combo_items()
        if current not in items:
            current = items[0]
        blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current)
        combo.blockSignals(blocked)
        combo.setToolTip(current)

    def _on_device_combo_changed(self, item: str) -> None:
        value = str(item or "").strip()
        if not value or value == self._copy("input_device_missing"):
            return
        audio_cfg = self._config.setdefault("audio", {})
        if value == self._copy("mic_device_auto_option"):
            audio_cfg["input_device_mode"] = "auto"
            audio_cfg["input_device"] = ""
        else:
            audio_cfg["input_device_mode"] = "fixed"
            audio_cfg["input_device"] = value
        self._refresh_device_combo()
        self._schedule_config_save()
        if self._running:
            self._restart_microphone_capture("microphone device changed by user")

    def _toggle_listen(self) -> None:
        self._set_desktop_capture_enabled(not self._desktop_capture_enabled, persist=True)

    def _set_desktop_capture_enabled(self, enabled: bool, *, persist: bool) -> None:
        new_value = bool(enabled)
        if new_value and self._running:
            try:
                self._start_listen()
            except Exception as exc:
                logger.warning("Desktop listen failed to start: %s", exc)
                self._desktop_capture_enabled = False
                self._listen_in_speech = False
                self._config.setdefault("vrc_listen", {})["enabled"] = False
                self._refresh_desktop_capture_button()
                self._refresh_floating_window_status(False)
                self._sync_settings_window_vrc_listen_state()
                self._set_bottom(str(exc))
                if persist:
                    self._schedule_config_save()
                return
        elif not new_value:
            self._stop_listen()

        self._desktop_capture_enabled = new_value
        if not self._desktop_capture_enabled:
            self._listen_in_speech = False
        self._config.setdefault("vrc_listen", {})["enabled"] = self._desktop_capture_enabled
        self._refresh_desktop_capture_button()
        self._refresh_floating_window_status(False)
        self._sync_settings_window_vrc_listen_state()
        if persist:
            self._schedule_config_save()
        self._set_bottom(self._copy("desktop_audio_saved"))

    def _refresh_desktop_capture_button(self) -> None:
        if self._desktop_btn is None:
            return
        self._desktop_btn.setText(self._copy("desktop_audio_on" if self._desktop_capture_enabled else "desktop_audio_off"))
        self._desktop_btn.setProperty("active", self._desktop_capture_enabled)
        self._desktop_btn.style().unpolish(self._desktop_btn)
        self._desktop_btn.style().polish(self._desktop_btn)

    def _set_floating_listen_status(self, listening: bool) -> None:
        service = getattr(self, "_overlay_service", None)
        if service is not None:
            service.set_listen_status(bool(listening))
            return
        win = getattr(self, "_floating_window", None)
        if win is None:
            return
        setter = getattr(win, "set_listen_status", None)
        if callable(setter):
            setter(bool(listening))

    def _refresh_floating_window_status(self, listening: bool | None = None) -> None:
        if listening is None:
            listening = bool(getattr(self, "_listen_in_speech", False))
        self._set_floating_listen_status(bool(listening))

    def _restore_floating_window_waiting_if_idle(self) -> None:
        if not bool(getattr(self, "_listen_in_speech", False)):
            self._refresh_floating_window_status(False)

    def _toggle_listen_overlay(self) -> None:
        self._set_listen_overlay_enabled(not self._listen_overlay_enabled, persist=True)

    def _set_listen_overlay_enabled(self, enabled: bool, *, persist: bool) -> None:
        self._listen_overlay_enabled = bool(enabled)
        self._config.setdefault("vrc_listen", {})["show_overlay"] = self._listen_overlay_enabled
        service = self._ensure_overlay_service(create_backend=self._listen_overlay_enabled)
        service.set_enabled(self._listen_overlay_enabled, reveal=self._listen_overlay_enabled)
        self._sync_avatar_overlay_state(force=True)
        self._refresh_listen_overlay_button()
        self._sync_settings_window_vrc_listen_state()
        if persist:
            self._schedule_config_save()

    def _refresh_listen_overlay_button(self) -> None:
        if self._listen_overlay_btn is None:
            return
        self._listen_overlay_btn.setText(self._copy("listen_overlay_on" if self._listen_overlay_enabled else "listen_overlay_off"))
        self._listen_overlay_btn.setProperty("active", self._listen_overlay_enabled)
        self._listen_overlay_btn.style().unpolish(self._listen_overlay_btn)
        self._listen_overlay_btn.style().polish(self._listen_overlay_btn)

    def _ensure_floating_window(self):
        from src.ui_qt.floating_window import FloatingWindow
        if self._floating_window is None:
            self._floating_window = FloatingWindow(
                None,
                self._ui_lang,
                on_resend=lambda text, source="listen": self._resend_history_to_vrc(text, source),
                on_close=lambda: self._set_listen_overlay_enabled(False, persist=True),
                theme=self._main_theme,
            )
            service = getattr(self, "_overlay_service", None)
            if service is not None:
                service.set_backend(self._floating_window, backend_name="desktop")
        self._refresh_floating_window_status()
        return self._floating_window

    def _ensure_overlay_service(self, *, create_backend: bool = True) -> OverlayService:
        service = getattr(self, "_overlay_service", None)
        if service is None:
            backend = self._ensure_floating_window() if create_backend else getattr(self, "_floating_window", None)
            service = OverlayService(backend, backend_name="desktop")
            try:
                service.setParent(self)
            except Exception:
                logger.debug("Overlay service created before MainWindow QObject init")
            service.error.connect(lambda message: getattr(self, "_set_bottom", lambda *_args, **_kwargs: None)(str(message), "warning"))
            self._overlay_service = service
            self._ensure_output_dispatcher().register_sink("overlay", service.show_message)
        elif create_backend and getattr(self, "_floating_window", None) is None:
            service.set_backend(self._ensure_floating_window(), backend_name="desktop")
        service.set_enabled(bool(self._listen_overlay_enabled), reveal=False)
        return service

    def _show_listen_translation(self, text: str, *, payload: str | None = None, source: str = "listen") -> None:
        message = OutputMessage(
            source=source,
            original_text=str(text or ""),
            translated_text=str(text or ""),
            display_text=str(text or ""),
            chatbox_text=str(payload or text or ""),
            is_error=(source == "error"),
        )
        self._dispatch_output_message(message, sinks=("overlay",))

    def _resend_history_to_vrc(self, text: str, source: str = "listen") -> None:
        if text:
            self._last_tgt_text = text
            self._send_to_vrc()

    def _sync_settings_window_vrc_listen_state(self) -> None:
        win = self._settings_window
        if win is not None and hasattr(win, "sync_vrc_listen_state"):
            try:
                win.sync_vrc_listen_state(
                    enabled=self._desktop_capture_enabled,
                    show_overlay=self._listen_overlay_enabled,
                    send_to_chatbox=self._listen_send_to_chatbox_enabled(),
                )
            except Exception:
                logger.debug("Failed to sync Qt settings VRC listen state", exc_info=True)

    def _listen_send_to_chatbox_enabled(self) -> bool:
        return bool(self._config.get("vrc_listen", {}).get("send_to_chatbox", True))

    def _set_listen_send_to_chatbox_enabled(self, enabled: bool, *, persist: bool) -> None:
        self._config.setdefault("vrc_listen", {})["send_to_chatbox"] = bool(enabled)
        self._sync_settings_window_vrc_listen_state()
        if persist:
            self._schedule_config_save()

    def _on_settings_listen_state_changed(
        self,
        enabled: bool | None,
        show_overlay: bool | None,
        send_to_chatbox: bool | None = None,
    ) -> None:
        if enabled is not None and bool(enabled) != self._desktop_capture_enabled:
            self._set_desktop_capture_enabled(bool(enabled), persist=True)
        if show_overlay is not None and bool(show_overlay) != self._listen_overlay_enabled:
            self._set_listen_overlay_enabled(bool(show_overlay), persist=True)
        if send_to_chatbox is not None and bool(send_to_chatbox) != self._listen_send_to_chatbox_enabled():
            self._set_listen_send_to_chatbox_enabled(bool(send_to_chatbox), persist=True)

    def _open_audio_diagnostics_window(self, target: str = MIC_SOURCE) -> None:
        normalized = DESKTOP_SOURCE if target == DESKTOP_SOURCE else MIC_SOURCE
        existing = self._audio_diagnostics_windows.get(normalized)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        from src.ui_qt.audio_diagnostics_window import AudioDiagnosticsWindow

        dialog = AudioDiagnosticsWindow(
            self,
            target=normalized,
            snapshot_provider=self.audio_diagnostics_snapshot,
            ui_language=self._ui_lang,
        )
        dialog.setStyleSheet(self._base_stylesheet())
        dialog.finished.connect(lambda _result, key=normalized: self._audio_diagnostics_windows.pop(key, None))
        self._audio_diagnostics_windows[normalized] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_vad_calibration_window(self, target: str = MIC_SOURCE) -> None:
        normalized = DESKTOP_SOURCE if target == DESKTOP_SOURCE else MIC_SOURCE
        existing = self._vad_calibration_windows.get(normalized)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        from src.ui_qt.vad_calibration_window import VadCalibrationWindow

        if normalized == DESKTOP_SOURCE:
            current_silence = self._listen_tail_silence_s()
        else:
            try:
                current_silence = float(self._config.get("audio", {}).get("vad_silence_threshold", 0.65))
            except (TypeError, ValueError):
                current_silence = 0.65
        dialog = VadCalibrationWindow(
            self,
            target=normalized,
            snapshot_provider=self.audio_diagnostics_snapshot,
            apply_callback=self._apply_vad_calibration_result,
            current_silence_s=current_silence,
            ui_language=self._ui_lang,
        )
        dialog.setStyleSheet(self._base_stylesheet())
        dialog.finished.connect(lambda _result, key=normalized: self._vad_calibration_windows.pop(key, None))
        self._vad_calibration_windows[normalized] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def audio_diagnostics_snapshot(self, target: str = MIC_SOURCE) -> dict[str, object]:
        normalized = DESKTOP_SOURCE if target == DESKTOP_SOURCE else MIC_SOURCE
        if normalized == DESKTOP_SOURCE:
            recorder = self._listen_recorder
            snapshot = recorder.diagnostics_snapshot() if recorder is not None else {}
            snapshot = dict(snapshot)
            snapshot.setdefault("running", recorder is not None and recorder.is_running)
            snapshot.setdefault("configured_device", self._desktop_output_device_name())
            snapshot.setdefault("active_device", self._active_listen_output_device_name)
            snapshot["vad_in_speech"] = bool(getattr(self, "_listen_in_speech", False))
            snapshot.setdefault("vad_min_rms", self._config.get("vrc_listen", {}).get("vad_min_rms", 0.02))
            snapshot.setdefault("segments_emitted", 0)
            return snapshot

        recorder = self._recorder
        snapshot = recorder.diagnostics_snapshot() if recorder is not None else {}
        snapshot = dict(snapshot)
        snapshot.setdefault("running", recorder is not None and recorder.is_running)
        snapshot.setdefault("configured_device", self._resolve_mic_input_device_name(refresh=False))
        snapshot.setdefault("active_device", self._active_mic_input_device_name)
        snapshot["vad_in_speech"] = bool(getattr(self, "_mic_in_speech", False))
        snapshot.setdefault("vad_min_rms", self._config.get("audio", {}).get("vad_min_rms", 0.012))
        snapshot.setdefault("segments_emitted", 0)
        return snapshot

    def _apply_vad_calibration_result(self, target: str, result) -> None:
        normalized = DESKTOP_SOURCE if target == DESKTOP_SOURCE else MIC_SOURCE
        if normalized == DESKTOP_SOURCE:
            cfg = self._config.setdefault("vrc_listen", {})
            cfg["vad_min_rms"] = round(float(result.recommended_min_rms), 4)
            cfg["tail_silence_s"] = round(float(result.recommended_silence_s), 2)
            if self._settings_window is not None:
                try:
                    self._settings_window._listen_vad_min_rms_var.set(str(cfg["vad_min_rms"]))
                    self._settings_window._listen_tail_silence_var.set(str(cfg["tail_silence_s"]))
                except Exception:
                    logger.debug("Failed to sync listen calibration fields", exc_info=True)
            self._schedule_config_save()
            if self._running and self._desktop_capture_enabled:
                self._restart_desktop_capture("VAD calibration applied")
            return

        cfg = self._config.setdefault("audio", {})
        cfg["vad_min_rms"] = round(float(result.recommended_min_rms), 4)
        cfg["vad_silence_threshold"] = round(float(result.recommended_silence_s), 2)
        if self._settings_window is not None:
            try:
                self._settings_window._vad_min_rms_var.set(str(cfg["vad_min_rms"]))
                self._settings_window._vad_var.set(str(cfg["vad_silence_threshold"]))
            except Exception:
                logger.debug("Failed to sync mic calibration fields", exc_info=True)
        self._schedule_config_save()
        if self._running:
            self._restart_microphone_capture("VAD calibration applied")

    def _on_settings_theme_changed(self, theme_preference: str) -> None:
        if self._destroying:
            return
        self._apply_theme_change(theme_preference, animate=True)

    def _apply_settings_theme_change(self, theme_preference: str, generation: int) -> None:
        if generation != self._settings_theme_sync_generation or self._destroying:
            return
        self._apply_theme_change(theme_preference, animate=True)

    def _open_sponsor_window(self) -> None:
        from src.ui_qt.sponsor_window import SponsorWindow
        if self._sponsor_window is not None and self._sponsor_window.isVisible():
            self._sponsor_window.raise_()
            self._sponsor_window.activateWindow()
            return
        self._sponsor_window = SponsorWindow(
            self,
            self._find_sponsor_image(),
            on_close=lambda: setattr(self, "_sponsor_window", None),
        )
        self._sponsor_window.show()

    def _open_update_window(self) -> None:
        pending = getattr(self, "_pending_update", None)
        if not pending:
            return
        from src.ui_qt.update_window import UpdateWindow
        self._update_win = UpdateWindow(self, pending, self._ui_lang)
        self._update_win.show()

    def _check_for_update(self) -> None:
        if self._destroying:
            return
        if not self.isVisible():
            return

        def on_update(update_info: UpdateInfo | None) -> None:
            if update_info is not None:
                self._call_in_ui(lambda info=update_info: self._handle_update_available(info))

        try:
            check_for_update(on_update)
        except Exception:
            logger.debug("Qt update check failed", exc_info=True)

    def _ignore_update_version(self, version: str) -> None:
        self._config.setdefault("ui", {})["ignored_update_version"] = version
        self._pending_update = None
        self._refresh_update_badge()
        self._schedule_config_save()

    def _handle_update_available(self, update_info: UpdateInfo) -> None:
        ignored = str(self._config.get("ui", {}).get("ignored_update_version", "") or "").strip()
        if ignored and update_info.version == ignored:
            return
        self._pending_update = update_info
        self._refresh_update_badge()

    def _refresh_update_badge(self) -> None:
        if self._update_badge_btn is None:
            return
        self._update_badge_btn.setText(self._copy("update_badge"))
        self._update_badge_btn.setVisible(self._pending_update is not None)

    def _open_osc_guide(self) -> None:
        existing = getattr(self, "_guide_win", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        dialog = QDialog(self)
        self._guide_win = dialog
        dialog.setObjectName("oscGuideDialog")
        dialog.setWindowTitle(self._t("guide_title"))
        dialog.setStyleSheet(self._base_stylesheet())
        dialog.setFixedSize(520, 430)
        dialog.finished.connect(lambda _result: setattr(self, "_guide_win", None))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel(self._t("guide_title"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(self._t("guide_subtitle"))
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        for index, (step_title, step_body, path) in enumerate(self._guide_pages(), start=1):
            card = QFrame()
            card.setObjectName("guideStepCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)
            step_label = QLabel(f"{index}. {step_title}")
            step_label.setObjectName("sectionTitle")
            body_label = QLabel(step_body)
            body_label.setObjectName("mutedLabel")
            body_label.setWordWrap(True)
            path_label = QLabel("  >  ".join(path))
            path_label.setObjectName("statusPill")
            card_layout.addWidget(step_label)
            card_layout.addWidget(body_label)
            card_layout.addWidget(path_label)
            layout.addWidget(card)

        footer = QLabel(self._t("guide_footer"))
        footer.setObjectName("mutedLabel")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        layout.addStretch(1)

        btn = QPushButton(self._t("guide_done"))
        btn.setObjectName("primaryButton")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
        dialog.show()
        dialog.activateWindow()

    def _guide_pages(self) -> list[tuple[str, str, list[str]]]:
        return [
            (
                self._t("guide_step_1_title"),
                self._t("guide_step_1_body"),
                ["Action Menu", "Options"],
            ),
            (
                self._t("guide_step_2_title"),
                self._t("guide_step_2_body"),
                ["Options", "OSC"],
            ),
            (
                self._t("guide_step_3_title"),
                self._t("guide_step_3_body"),
                ["OSC", "Enabled"],
            ),
        ]

    def _maybe_show_osc_guide(self) -> None:
        if self._destroying:
            return
        if not self.isVisible():
            return
        ui_cfg = self._config.setdefault("ui", {})
        if ui_cfg.get("osc_guide_seen"):
            return
        ui_cfg["osc_guide_seen"] = True
        self._schedule_config_save()
        self._open_osc_guide()

    def _maybe_show_mode_wizard(self) -> None:
        if self._destroying or not self.isVisible():
            return
        ui_cfg = self._config.setdefault("ui", {})
        if ui_cfg.get("mode_wizard_seen"):
            return
        self._open_mode_wizard(mark_seen=True)

    def _open_mode_wizard(self, *, mark_seen: bool) -> None:
        if self._destroying:
            return
        existing = getattr(self, "_mode_wizard_dialog", None)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        if mark_seen:
            self._config.setdefault("ui", {})["mode_wizard_seen"] = True
            self._schedule_config_save()
        from src.ui_qt.mode_wizard_dialog import ModeWizardDialog, ModeWizardResult

        def on_done(result: ModeWizardResult | None) -> None:
            self._mode_wizard_dialog = None
            if result is None:
                return
            self._apply_mode_wizard_result(result.mode_id)
            if result.apply_recommendation:
                target_page = self._settings_page_for_mode_wizard(result.mode_id)
                QTimer.singleShot(0, lambda page_id=target_page: self.show_settings(page_id=page_id))

        dialog = ModeWizardDialog(self, ui_language=self._ui_lang, on_done=on_done)
        self._mode_wizard_dialog = dialog
        dialog.setStyleSheet(self._base_stylesheet())
        dialog.finished.connect(lambda _result: setattr(self, "_mode_wizard_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @staticmethod
    def _settings_page_for_mode_wizard(mode_id: str) -> str:
        mode = str(mode_id or "chatbox").strip().lower()
        return {
            "chatbox": "voice",
            "listen": "vrc_listen",
            "tts": "tts",
            "manual": "translation",
            "overlay": "vrc_listen",
        }.get(mode, "voice")

    def _apply_mode_wizard_result(self, mode_id: str) -> None:
        mode = str(mode_id or "chatbox").strip().lower()
        trans_cfg = self._config.setdefault("translation", {})
        if not isinstance(trans_cfg, dict):
            trans_cfg = {}
            self._config["translation"] = trans_cfg
        vrc_cfg = self._config.setdefault("vrc_listen", {})
        if not isinstance(vrc_cfg, dict):
            vrc_cfg = {}
            self._config["vrc_listen"] = vrc_cfg
        tts_cfg = self._config.setdefault("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
            self._config["tts"] = tts_cfg
        ui_cfg = self._config.setdefault("ui", {})
        if not isinstance(ui_cfg, dict):
            ui_cfg = {}
            self._config["ui"] = ui_cfg

        trans_cfg["send_to_chatbox"] = mode in {"chatbox", "manual", "tts"}
        vrc_cfg["enabled"] = mode in {"listen", "overlay"}
        vrc_cfg["show_overlay"] = mode in {"listen", "overlay"}
        vrc_cfg.setdefault("send_to_chatbox", True)
        tts_cfg["enabled"] = mode == "tts"
        tts_cfg["auto_read"] = mode == "tts"
        if mode == "tts":
            tts_cfg["output_to_vrchat"] = True
            self._set_app_mode(AppMode.SIMULTANEOUS, persist=False)
        else:
            self._set_app_mode(AppMode.TRANSLATION, persist=False)
        if mode == "manual":
            ui_cfg["preferred_entry"] = "text_input"
        elif mode == "overlay":
            ui_cfg["preferred_entry"] = "overlay"
        else:
            ui_cfg["preferred_entry"] = mode
        ui_cfg["mode_wizard_seen"] = True

        self._desktop_capture_enabled = bool(vrc_cfg.get("enabled", False))
        self._listen_overlay_enabled = bool(vrc_cfg.get("show_overlay", False))
        if self._listen_overlay_enabled or getattr(self, "_overlay_service", None) is not None:
            self._ensure_overlay_service(create_backend=self._listen_overlay_enabled).set_enabled(
                self._listen_overlay_enabled,
                reveal=False,
            )
        self._sync_avatar_overlay_state(force=True)
        self._sync_tts_enabled_from_config()
        self._refresh_mode_buttons()
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        self._sync_settings_window_vrc_listen_state()
        self._schedule_config_save()
        self._set_bottom(self._t("settings_saved"))

    def _open_external_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _social_button(self, icon_name: str, fallback_text: str, url: str) -> QPushButton:
        btn = QPushButton(fallback_text)
        btn.setObjectName("socialButton")
        btn.setFixedSize(42, 42)
        btn.setIconSize(QSize(26, 26))
        btn.setProperty("iconFile", icon_name)
        btn.setProperty("fallbackText", fallback_text)
        self._social_buttons.append((btn, icon_name))
        self._apply_social_button_icon(btn, icon_name)
        btn.clicked.connect(lambda: self._open_external_url(url))
        return btn

    def _apply_social_button_icon(self, btn: QPushButton, icon_name: str) -> None:
        palette = _main_theme_palette(self._main_theme)
        if icon_name == ICON_GITHUB_FILE:
            color = "#ffffff" if self._main_theme == "dark" else "#24292f"
        elif icon_name == ICON_QQ_FILE:
            color = "#4cc9ff" if self._main_theme == "dark" else "#12b7f5"
        elif icon_name == ICON_LINE_FILE:
            color = "#4ade80" if self._main_theme == "dark" else "#06c755"
        else:
            color = palette["TEXT_PRIMARY"]
        icon = ui_icon(icon_name, 26, color)
        btn.setIcon(icon)
        btn.setText("" if not icon.isNull() else str(btn.property("fallbackText") or ""))

    def _refresh_social_buttons(self) -> None:
        for btn, icon_name in getattr(self, "_social_buttons", []):
            self._apply_social_button_icon(btn, icon_name)

    def _load_icon_pixmap(self, filename: str, size: int) -> QPixmap | None:
        path = self._find_icon_file(filename)
        if path is None:
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _find_icon_file(self, filename: str) -> Path | None:
        for base_dir in resource_base_dirs():
            for rel in (Path("assets") / "icons" / filename, Path("docs") / "assets" / "icons" / filename):
                path = base_dir / rel
                if path.is_file():
                    return path
        return None

    def _find_sponsor_image(self) -> Path | None:
        for base_dir in resource_base_dirs():
            for filename in SPONSOR_IMAGE_CANDIDATES:
                for rel in (Path("assets") / filename, Path("assets") / "icons" / filename):
                    path = base_dir / rel
                    if path.is_file():
                        return path
        return None

    # ----------------------------------------------------------------
    # Button handlers
    # ----------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        if self._running:
            self._do_stop()
        else:
            self._do_start()

    def _on_mute_clicked(self) -> None:
        self._toggle_mic_mute()

    def _on_text_input_clicked(self) -> None:
        self._open_text_input_popup()

    def _on_translate_clicked(self) -> None:
        if not self._src_text:
            return
        if self._translating:
            return
        self._do_manual_translate()

    def _on_copy_clicked(self) -> None:
        self._copy_result()

    def _on_send_clicked(self) -> None:
        self._send_to_vrc()

    def _toggle_tweaks_panel(self) -> None:
        """切换实时调整面板的显示/隐藏"""
        if self._tweaks_panel is None:
            # 首次创建
            from src.ui_qt.state_manager import AppState
            state = getattr(self, '_state', None)
            if state is None:
                state = AppState()
                self._state = state

            self._tweaks_panel = RealtimeTweaksPanel(
                parent=self,
                state_manager=state,
                ui_language=self._ui_lang,
                theme=self._main_theme
            )
            self._tweaks_panel.finished.connect(self._on_tweaks_panel_closed)

        if self._tweaks_panel.isVisible():
            self._tweaks_panel.hide()
        else:
            self._tweaks_panel.show()
            self._tweaks_panel.raise_()
            self._tweaks_panel.activateWindow()

    def _on_tweaks_panel_closed(self) -> None:
        """实时调整面板关闭时的回调"""
        if self._tweaks_panel:
            self._tweaks_panel.deleteLater()
            self._tweaks_panel = None

    def _subscribe_realtime_tweaks_state(self) -> None:
        """订阅实时调整参数的状态变化"""
        from src.ui_qt.state_manager import AppState

        # 获取或创建状态管理器
        if not hasattr(self, '_state'):
            self._state = AppState()

        # 订阅实时调整参数
        self._state.subscribe('mic_gain', self._on_mic_gain_changed)
        self._state.subscribe('envelope_attack_rate', self._on_envelope_params_changed)
        self._state.subscribe('envelope_release_rate', self._on_envelope_params_changed)
        self._state.subscribe('overlay_opacity', self._on_overlay_opacity_changed)
        logger.debug("Realtime tweaks state subscriptions registered")

    def _on_mic_gain_changed(self, gain: float) -> None:
        """麦克风增益变化处理"""
        logger.debug(f"Mic gain changed to: {gain}")
        # 实际的增益应用在音频处理管道中实现

    def _on_envelope_params_changed(self, _value: float) -> None:
        """包络参数变化处理"""
        if not hasattr(self, '_state'):
            return
        attack = self._state.get('envelope_attack_rate', 0.6)
        release = self._state.get('envelope_release_rate', 0.12)
        logger.debug(f"Envelope params changed: attack={attack}, release={release}")
        # 更新 VAD 检测器的包络参数（如果需要）

    def _on_overlay_opacity_changed(self, opacity: float) -> None:
        """悬浮窗透明度变化处理"""
        # 更新悬浮窗透明度
        if hasattr(self, '_floating_window') and self._floating_window:
            try:
                self._floating_window.setWindowOpacity(opacity)
                logger.debug(f"Floating window opacity changed to: {opacity}")
            except Exception:
                logger.debug("Failed to update floating window opacity", exc_info=True)

        # 同步更新文本输入窗口透明度
        if hasattr(self, '_text_input_window') and self._text_input_window:
            try:
                self._text_input_window.setWindowOpacity(opacity)
                logger.debug(f"Text input window opacity changed to: {opacity}")
            except Exception:
                logger.debug("Failed to update text input window opacity", exc_info=True)

    def _on_theme_toggle(self) -> None:
        new_theme = "light" if self._main_theme == "dark" else "dark"
        self._apply_theme_change(new_theme, animate=True)

    def _apply_theme_change(self, new_theme: str, *, animate: bool = False) -> None:
        old_theme = self._main_theme
        preference = _normalize_main_theme_preference(new_theme)
        resolved_theme = _resolve_main_theme(preference)
        theme_changed = resolved_theme != old_theme

        def apply_theme() -> None:
            ui_cfg = self._config.setdefault("ui", {})
            ui_cfg[MAIN_THEME_CONFIG_KEY] = preference
            self._main_theme_preference = preference
            self._main_theme = resolved_theme
            central = self.centralWidget()
            if isinstance(central, BackgroundWidget):
                central.set_theme(self._main_theme)
            self._schedule_config_save()
            self._reload_theme_style()
            self._refresh_child_windows(animate=animate and theme_changed)

        if animate and theme_changed:
            play_theme_fade(
                self.centralWidget() or self,
                update=apply_theme,
                duration_ms=220,
            )
            return
        apply_theme()

    def _on_desktop_toggle(self) -> None:
        self._toggle_listen()

    # ----------------------------------------------------------------
    # Pipeline start / stop
    # ----------------------------------------------------------------
    def _do_start(self) -> None:
        if self._start_btn is None:
            return
        missing_api_key, _backend_label = missing_required_translation_api_key(self._config)
        if missing_api_key:
            QMessageBox.warning(
                self,
                tr(self._ui_lang, "api_missing_title"),
                tr(self._ui_lang, "api_missing_message"),
            )
            self._set_status(self._t("status_error"), "danger", key="status_error")
            return
        self._start_btn.setEnabled(False)
        self._start_btn.setText(self._t("starting"))
        self._set_status(self._t("starting"), "accent", key="starting")
        self._listen_session += 1
        self._reset_streaming_state()
        self._reset_translation_failure_backoff()
        self._startup_cancel_event = threading.Event()
        session = self._listen_session

        def run() -> None:
            try:
                self._init_pipeline(session)
            except _StartupCancelled:
                self._call_in_ui(lambda: self._cleanup_startup_failure(show_error=False))
            except Exception as e:
                self._call_in_ui(lambda msg=str(e): self._cleanup_startup_failure(msg, show_error=True))

        threading.Thread(target=run, daemon=True, name="pipeline-startup").start()

    def _init_pipeline(self, session_id: int) -> None:
        self._raise_if_cancelled(session_id)

        self._asr, self._listen_asr = _create_asr_pair(self._config)
        self._refresh_asr_transcribe_locks()
        self._asr.load(
            progress_callback=lambda event: self._call_in_ui(
                lambda e=event: self._handle_model_progress(e)
            )
        )
        if self._listen_asr is not self._asr:
            self._listen_asr.load(
                progress_callback=lambda event: self._call_in_ui(
                    lambda e=event: self._handle_model_progress(e)
                )
            )
        self._raise_if_cancelled(session_id)

        self._sender = self._create_sender()

        self._raise_if_cancelled(session_id)
        self._start_workers()
        self._start_microphone_capture()
        self._raise_if_cancelled(session_id)

        self._running = True
        if self._desktop_capture_enabled:
            try:
                self._start_listen()
            except Exception as exc:
                logger.warning("Desktop listen did not start: %s", exc)
                self._desktop_capture_enabled = False
                self._config.setdefault("vrc_listen", {})["enabled"] = False
                self._call_in_ui(lambda msg=str(exc): self._set_bottom(msg))
                self._call_in_ui(self._refresh_desktop_capture_button)
        self._call_in_ui(self._on_started)

    def _raise_if_cancelled(self, session_id: int) -> None:
        if self._destroying or session_id != self._listen_session:
            raise _StartupCancelled()

    def _cleanup_startup_failure(self, msg: str = "", *, show_error: bool) -> None:
        self._running = False
        self._reset_streaming_state()
        self._stop_listen()
        self._stop_microphone_capture()
        self._stop_workers()
        self._close_osc_sender()
        self._refresh_start_button()
        if show_error:
            self._on_start_error(msg)
        else:
            self._set_status(self._t("status_ready"), "success", key="status_ready")

    def _do_stop(self) -> None:
        self._startup_cancel_event.set()
        self._listen_session += 1
        self._running = False
        self._reset_streaming_state()
        self._reset_translation_failure_backoff()
        self._reset_avatar_params()

        self._stop_listen()
        self._stop_microphone_capture()
        self._stop_workers()

        self._close_osc_sender()

        self._refresh_start_button()
        self._set_status(self._t("status_ready"), "success", key="status_ready")

    def _start_workers(self) -> None:
        for source in (MIC_SOURCE, DESKTOP_SOURCE):
            self._partial_task_queues[source] = queue.Queue(maxsize=PARTIAL_TASK_QUEUE_MAXSIZE)
            final_maxsize = (
                DESKTOP_FINAL_TASK_QUEUE_MAXSIZE
                if source == DESKTOP_SOURCE
                else FINAL_TASK_QUEUE_MAXSIZE
            )
            self._final_task_queues[source] = queue.Queue(maxsize=final_maxsize)

            p = threading.Thread(
                target=self._partial_worker_loop,
                args=(source,),
                daemon=True,
                name=f"partial-{source}",
            )
            p.start()
            self._partial_workers[source] = p

            f = threading.Thread(
                target=self._final_worker_loop,
                args=(source,),
                daemon=True,
                name=f"final-{source}",
            )
            f.start()
            self._final_workers[source] = f

    def _stop_workers(self) -> None:
        for q in self._partial_task_queues.values():
            self._enqueue_latest(q, None)
        for q in self._final_task_queues.values():
            self._enqueue_latest(q, None)
        self._partial_workers.clear()
        self._final_workers.clear()

    @staticmethod
    def _drain_queue(work_queue: queue.Queue) -> None:
        while True:
            try:
                work_queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _enqueue_latest(work_queue: queue.Queue, payload) -> str:
        try:
            work_queue.put_nowait(payload)
            return "enqueued"
        except queue.Full:
            pass
        try:
            work_queue.get_nowait()
        except queue.Empty:
            return "dropped"
        try:
            work_queue.put_nowait(payload)
            return "replaced"
        except queue.Full:
            return "dropped"

    def _partial_worker_loop(self, source: str) -> None:
        q = self._partial_task_queues.get(source)
        if q is None:
            return
        while True:
            payload = q.get()
            if payload is None:
                return
            audio, asr_lang, generation, session_id, src = payload
            self._process_partial_audio_chunk(audio, asr_lang, generation, session_id, src)

    def _final_worker_loop(self, source: str) -> None:
        q = self._final_task_queues.get(source)
        if q is None:
            return
        while True:
            payload = q.get()
            if payload is None:
                return
            audio, asr_lang, selected_src_lang, session_id, src = payload
            self._process_final_audio_segment(audio, asr_lang, selected_src_lang, session_id, src)

    # ----------------------------------------------------------------
    # Audio capture
    # ----------------------------------------------------------------
    def _start_microphone_capture(self) -> None:
        device_name = self._resolve_mic_input_device_name(refresh=True)
        if device_name and device_name not in self._devices:
            try:
                devices = _list_microphone_devices()
            except Exception:
                logger.debug("Failed to enumerate microphone devices", exc_info=True)
                devices = []
            self._devices = {
                str(d.get("name", "")).strip(): int(d.get("index", -1))
                for d in devices
                if str(d.get("name", "")).strip()
            }
        dev_idx = self._devices.get(device_name)
        from src.audio.recorder import AudioRecorder
        audio_cfg = self._config.get("audio", {})
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}

        self._recorder = AudioRecorder(
            on_segment=self._on_audio_segment,
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            frame_duration_ms=int(audio_cfg.get("frame_duration_ms", 30)),
            vad_sensitivity=int(audio_cfg.get("vad_sensitivity", 2)),
            silence_threshold_s=float(audio_cfg.get("vad_silence_threshold", 0.65)),
            vad_speech_ratio=float(audio_cfg.get("vad_speech_ratio", 0.6)),
            vad_activation_threshold_s=float(audio_cfg.get("vad_activation_threshold_s", 0.2)),
            vad_min_rms=float(audio_cfg.get("vad_min_rms", 0.012)),
            min_segment_s=float(audio_cfg.get("min_segment_s", 0.45)),
            partial_min_speech_s=float(audio_cfg.get("partial_min_speech_s", 0.45)),
            max_segment_s=float(audio_cfg.get("max_segment_s", 6.0)),
            denoise_strength=float(audio_cfg.get("denoise_strength", 0.0)),
            input_device=dev_idx,
            on_vad_state=lambda active: self._call_in_ui(
                lambda state=active: self._handle_mic_vad_state(state)
            ),
        )
        self._recorder.start()
        self._active_mic_input_device_name = self._recorder.active_input_device_name or device_name
        self._last_mic_started_at = time.monotonic()
        self._last_mic_result_at = self._last_mic_started_at
        self._last_mic_diagnostic_log_at = 0.0
        logger.info("Microphone capture started: %s", self._active_mic_input_device_name)

    def _stop_microphone_capture(self) -> None:
        self._mic_in_speech = False
        self._reset_streaming_state(MIC_SOURCE)
        if self._recorder:
            self._recorder.stop()
            self._recorder = None
        self._active_mic_input_device_name = None
        logger.info("Microphone capture stopped")

    def _start_listen(self) -> None:
        if self._listen_recorder is not None:
            return
        try:
            self._log_listen_environment("before_start")
            if not self._desktop_devices:
                self._load_desktop_devices()
            device_name = self._desktop_output_device_name()
            if not device_name:
                raise RuntimeError(self._copy("vrc_listen_device_missing"))
        except Exception:
            raise RuntimeError(self._copy("vrc_listen_device_missing"))
        audio_cfg = self._config.get("audio", {}) if isinstance(self._config.get("audio", {}), dict) else {}
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        segment_duration_s = self._listen_segment_duration_s()
        chunk_interval_ms = min(max(int(round(segment_duration_s * 500.0)), 700), 1400)
        from src.audio.desktop_recorder import DesktopAudioRecorder

        self._listen_recorder = DesktopAudioRecorder(
            on_segment=lambda audio: self._on_audio_segment(audio, DESKTOP_SOURCE),
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            frame_duration_ms=int(audio_cfg.get("frame_duration_ms", 30)),
            vad_sensitivity=int(audio_cfg.get("vad_sensitivity", 1)),
            silence_threshold_s=self._listen_tail_silence_s(),
            vad_speech_ratio=float(listen_cfg.get("vad_speech_ratio", audio_cfg.get("vad_speech_ratio", 0.4))),
            vad_activation_threshold_s=float(listen_cfg.get("vad_activation_threshold_s", audio_cfg.get("vad_activation_threshold_s", 0.06))),
            vad_min_rms=float(listen_cfg.get("vad_min_rms", 0.020)),
            min_segment_s=float(audio_cfg.get("min_segment_s", 0.45)),
            partial_min_speech_s=float(audio_cfg.get("partial_min_speech_s", 0.45)),
            max_segment_s=float(audio_cfg.get("max_segment_s", 6.0)),
            denoise_strength=float(listen_cfg.get("denoise_strength", 0.35)),
            silero_speech_threshold=float(listen_cfg.get("silero_speech_threshold", 0.15)),
            vad_type=str(listen_cfg.get("vad_type", "webrtc")).strip().lower(),
            output_device_name=device_name,
            chunk_interval_ms=chunk_interval_ms,
            chunk_window_s=segment_duration_s,
            on_vad_state=lambda active: self._call_in_ui(
                lambda state=active: self._handle_listen_vad_state(state)
            ),
            on_runtime_error=lambda message: self._call_in_ui(
                lambda m=message: self._handle_desktop_capture_runtime_error(m)
            ),
        )
        try:
            self._listen_recorder.start()
            self._active_listen_output_device_name = device_name
            self._listen_in_speech = False
            self._refresh_floating_window_status(False)
            self._last_listen_started_at = time.monotonic()
            self._last_listen_result_at = self._last_listen_started_at
            self._last_listen_diagnostic_log_at = 0.0
            self._last_desktop_device_signature = (tuple(sorted(self._desktop_devices)), device_name)
            self._log_listen_environment("after_start")
            logger.info("Desktop listen started successfully on output device: %s", device_name)
        except Exception:
            self._listen_recorder = None
            self._active_listen_output_device_name = None
            raise

    def _stop_listen(self) -> None:
        self._reset_streaming_state(DESKTOP_SOURCE)
        self._listen_in_speech = False
        self._refresh_floating_window_status(False)
        self._active_listen_output_device_name = None
        if self._listen_recorder is not None:
            try:
                self._listen_recorder.stop()
            finally:
                self._listen_recorder = None
        logger.info("Desktop listen stopped")

    def _handle_desktop_capture_runtime_error(self, message: str) -> None:
        self._set_bottom(str(message or "Desktop listen stopped"))
        self._set_desktop_capture_enabled(False, persist=True)

    def _desktop_capture_config(self) -> dict:
        cfg = self._config.setdefault("vrc_listen", {})
        if not isinstance(cfg, dict):
            cfg = {}
            self._config["vrc_listen"] = cfg
        return cfg

    def _tts_config(self) -> dict:
        cfg = self._config.setdefault("tts", {})
        if not isinstance(cfg, dict):
            cfg = {}
            self._config["tts"] = cfg
        return cfg

    def _sync_tts_enabled_from_config(self) -> bool:
        tts_cfg = self._tts_config()
        if "enabled" in tts_cfg:
            self._tts_enabled = bool(tts_cfg.get("enabled", False))
        return bool(getattr(self, "_tts_enabled", False))

    @staticmethod
    def _normalize_audio_device_name(name: str | None) -> str:
        return " ".join(str(name or "").casefold().split())

    def _desktop_device_names_match(self, left: str | None, right: str | None) -> bool:
        left_norm = self._normalize_audio_device_name(left)
        right_norm = self._normalize_audio_device_name(right)
        return bool(
            left_norm
            and right_norm
            and (left_norm == right_norm or left_norm in right_norm or right_norm in left_norm)
        )

    def _match_desktop_device_name(self, name: str | None) -> str | None:
        clean = str(name or "").strip()
        if not clean:
            return None
        devices = getattr(self, "_desktop_devices", {}) or {}
        if clean in devices:
            return clean
        for candidate in devices:
            if self._desktop_device_names_match(clean, candidate):
                return candidate
        return clean if not devices else None

    def _listen_process_output_probe_enabled(self) -> bool:
        return bool(self._desktop_capture_config().get("follow_process_output", False))

    def _listen_target_process_names(self) -> list[str]:
        configured = self._desktop_capture_config().get("target_process_names", ["VRChat.exe"])
        if isinstance(configured, str):
            configured = [configured]
        if not isinstance(configured, list):
            configured = []
        names: list[str] = []
        for name in configured:
            clean = str(name or "").strip()
            if clean and clean not in names:
                names.append(clean)
        return names or ["VRChat.exe"]

    def _listen_process_snapshot(self) -> dict[str, object]:
        names = self._listen_target_process_names()
        try:
            from src.audio.windows_audio import inspect_process_output_state

            snapshot = inspect_process_output_state(names)
        except Exception:
            logger.debug("Failed to inspect target process output device", exc_info=True)
            snapshot = {
                "process_names": names,
                "is_running": False,
                "default_output_device": default_output_device_name(),
                "active_device": None,
                "has_active_audio_session": False,
                "matches": [],
            }
        snapshot["probe_enabled"] = self._listen_process_output_probe_enabled()
        return snapshot

    def _log_listen_environment(self, stage: str) -> None:
        process_audio = self._listen_process_snapshot()
        logger.info(
            "Desktop listen environment [%s] enabled=%s running=%s available=%s selected_output=%s active_output=%s default_output=%s process_audio=%s",
            stage,
            self._desktop_capture_enabled,
            self._listen_recorder is not None,
            self._listen_available,
            self._desktop_output_device_name(),
            self._active_listen_output_device_name,
            default_output_device_name(),
            process_audio,
        )

    def _listen_auto_should_avoid_output_device(self, device_name: str | None) -> bool:
        name = str(device_name or "").strip()
        if not name:
            return False
        tts_cfg = self._tts_config()
        if not (
            bool(tts_cfg.get("enabled", False))
            and bool(tts_cfg.get("output_to_vrchat", False))
        ):
            return False
        tts_device = self._match_desktop_device_name(
            str(tts_cfg.get("output_device_name") or "").strip()
        )
        if tts_device is not None and self._desktop_device_names_match(name, tts_device):
            return True
        normalized = self._normalize_audio_device_name(name)
        return "mixline" in normalized or "mix line" in normalized

    def _listen_auto_fallback_output_device_name(self, avoided_name: str | None) -> str | None:
        for name in getattr(self, "_desktop_devices", {}) or {}:
            if self._desktop_device_names_match(name, avoided_name):
                continue
            if self._listen_auto_should_avoid_output_device(name):
                continue
            normalized = self._normalize_audio_device_name(name)
            if "mixline" not in normalized and "mix line" not in normalized:
                return name
        return None

    def _auto_detect_listen_device_name(self) -> str | None:
        if self._listen_process_output_probe_enabled():
            detected = self._detect_vrchat_output_device_name()
            if detected is not None:
                return detected
        try:
            default_name = default_output_device_name()
        except Exception:
            logger.debug("Failed to resolve default desktop output device", exc_info=True)
            return None
        matched = self._match_desktop_device_name(default_name)
        if self._listen_auto_should_avoid_output_device(matched):
            fallback = self._listen_auto_fallback_output_device_name(matched)
            if fallback is not None:
                return fallback
        return matched

    def _desktop_output_device_name(self) -> str | None:
        listen_cfg = self._desktop_capture_config()
        configured = str(listen_cfg.get("loopback_device") or "").strip()
        matched_configured = self._match_desktop_device_name(configured)
        if matched_configured is not None:
            return matched_configured
        return self._auto_detect_listen_device_name()

    def _listen_uses_auto_output_device(self) -> bool:
        configured = str(self._desktop_capture_config().get("loopback_device") or "").strip()
        return self._match_desktop_device_name(configured) is None

    def _desktop_device_signature(self, *, refresh: bool = False) -> tuple[tuple[str, ...], str | None]:
        if refresh or not self._desktop_devices:
            self._load_desktop_devices()
        return tuple(sorted(self._desktop_devices)), self._desktop_output_device_name()

    def _detect_target_process_output_device_name(self) -> str | None:
        try:
            from src.audio.windows_audio import detect_process_output_device_name

            detected = detect_process_output_device_name(self._listen_target_process_names())
            return self._match_desktop_device_name(detected)
        except Exception:
            logger.debug("Failed to detect target process output device", exc_info=True)
            return None

    def _detect_vrchat_output_device_name(self) -> str | None:
        return self._detect_target_process_output_device_name()

    def _listen_source_language(self) -> str | None:
        listen_cfg = self._desktop_capture_config()
        source = str(listen_cfg.get("source_language", "auto") or "auto").strip()
        return None if source == "auto" else source

    def _listen_target_language(self) -> str:
        listen_cfg = self._desktop_capture_config()
        return str(listen_cfg.get("target_language", "zh") or "zh").strip() or "zh"

    def _final_segment_may_need_translation_api(
        self,
        source: str,
        selected_src_lang: str | None,
        output_format: str,
    ) -> bool:
        if source == DESKTOP_SOURCE:
            src_lang = selected_src_lang or "auto"
            return not (src_lang != "auto" and src_lang == self._listen_target_language())
        if output_format == "original_only":
            return False
        src_lang = selected_src_lang or "auto"
        tgt_lang = getattr(self, "_current_tgt_lang", "")
        return not (src_lang != "auto" and src_lang == tgt_lang)

    def _listen_segment_duration_s(self) -> float:
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        try:
            return max(0.5, float(listen_cfg.get("segment_duration_s", 2.0)))
        except (TypeError, ValueError):
            return 2.0

    def _listen_tail_silence_s(self) -> float:
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        try:
            return max(0.2, float(listen_cfg.get("tail_silence_s", 0.65)))
        except (TypeError, ValueError):
            return 0.65

    def _listen_self_suppress_seconds(self) -> float:
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        try:
            value = float(listen_cfg.get("self_suppress_seconds", DEFAULT_LISTEN_SELF_SUPPRESS_S))
            if value <= 0:
                raise ValueError
            return value
        except (TypeError, ValueError):
            return DEFAULT_LISTEN_SELF_SUPPRESS_S

    def _listen_asr_language(self) -> str | None:
        source = self._listen_source_language()
        if source in ("zh", "en", "ja", "ko", "fr", "de", "es", "ru"):
            return source
        return None

    # ----------------------------------------------------------------
    # Periodic desktop audio watch
    # ----------------------------------------------------------------
    def _schedule_desktop_audio_watch(self, delay_ms: int = 2500) -> None:
        if self._destroying:
            return
        if hasattr(self, "_desktop_watch_timer") and self._desktop_watch_timer is not None:
            try:
                self._desktop_watch_timer.stop()
            except Exception:
                pass
        if not hasattr(self, "_desktop_watch_timer"):
            self._desktop_watch_timer = QTimer(self)
            self._desktop_watch_timer.timeout.connect(self._poll_desktop_audio_watch)
        self._desktop_watch_timer.start(delay_ms)

    def _poll_desktop_audio_watch(self) -> None:
        if self._destroying:
            return

        try:
            was_available = self._listen_available
            self._refresh_listen_availability(refresh_devices=(
                self._listen_recorder is None and bool(self._desktop_capture_enabled)
            ))
            self._refresh_desktop_capture_button()
            recorder = self._listen_recorder

            # 检查1：录音线程意外停止 → 重启
            if (
                recorder is not None
                and not recorder.is_running
                and self._running
                and self._desktop_capture_enabled
            ):
                last_error = getattr(recorder, "last_error", None) or "Desktop audio capture stopped unexpectedly"
                logger.warning("Desktop listen recorder stopped unexpectedly; triggering restart")
                self._restart_desktop_capture(message=last_error)
                return

            if (
                self._running
                and self._desktop_capture_enabled
                and self._listen_recorder is None
                and not was_available
                and self._listen_available
            ):
                self._restart_desktop_capture()
                return

            if self._running and self._desktop_capture_enabled and self._listen_recorder is not None:
                self._maybe_log_listen_diagnostics()

            if not (
                self._running
                and self._desktop_capture_enabled
                and self._listen_recorder is not None
            ):
                self._last_desktop_device_signature = None
                return

            signature = self._desktop_device_signature(refresh=True)
            previous = self._last_desktop_device_signature
            self._last_desktop_device_signature = signature
            if previous is None:
                return
            if signature != previous:
                logger.info(
                    "Detected desktop output device change (previous=%s current=%s)",
                    previous,
                    signature,
                )
                self._restart_desktop_capture()
                return
        except Exception as exc:
            logger.exception("Desktop audio watch poll failed: %s", exc)

    def _restart_desktop_capture(self, message: str | None = None) -> None:
        self._stop_listen()
        if not self._running or not self._desktop_capture_enabled:
            return
        try:
            self._start_listen()
        except Exception as exc:
            logger.warning("Desktop listen failed to restart: %s", exc)
            self._desktop_capture_enabled = False
            self._listen_in_speech = False
            listen_cfg = self._config.setdefault("vrc_listen", {})
            if isinstance(listen_cfg, dict):
                listen_cfg["enabled"] = False
            self._refresh_desktop_capture_button()
            self._refresh_floating_window_status(False)
            self._sync_settings_window_vrc_listen_state()
            self._set_bottom(str(message or exc))

    def _maybe_log_listen_diagnostics(self) -> None:
        now = time.monotonic()
        if (now - self._last_listen_diagnostic_log_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        recorder = self._listen_recorder
        if recorder is None or not hasattr(recorder, "diagnostics_snapshot"):
            return
        idle_anchor = max(
            float(self.__dict__.get("_last_listen_result_at", 0.0) or 0.0),
            float(self.__dict__.get("_last_listen_started_at", 0.0) or 0.0),
        )
        if idle_anchor > 0 and (now - idle_anchor) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        stats = recorder.diagnostics_snapshot()
        last_non_silent_at = float(stats.get("last_non_silent_at") or 0.0)
        audio_state = "no_loopback_audio"
        if last_non_silent_at > 0 and (now - last_non_silent_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            audio_state = "audio_present_but_no_result"
        process_audio = self._listen_process_snapshot()
        log_fn = logger.warning
        if audio_state == "no_loopback_audio" and not bool(process_audio.get("has_active_audio_session", False)):
            log_fn = logger.info
        log_fn(
            "Desktop listen diagnostics state=%s idle_for=%.1fs stats=%s process_audio=%s mic_active=%s output_format=%s self_suppress=%s",
            audio_state,
            now - idle_anchor if idle_anchor > 0 else 0.0,
            stats,
            process_audio,
            self._active_mic_input_device_name,
            self._get_output_format(),
            bool(self._desktop_capture_config().get("self_suppress", False)),
        )
        self._last_listen_diagnostic_log_at = now

    def _refresh_listen_availability(self, *, refresh_devices: bool = False) -> bool:
        try:
            from src.audio.desktop_recorder import desktop_audio_supported
            available = bool(desktop_audio_supported())
        except Exception:
            available = False
        self._listen_available = available
        return available

    def _current_default_input_device_name(self, devices: list[dict]) -> str | None:
        if not devices:
            return None
        try:
            import sounddevice as sd
            default_in = sd.default.device[0]
            if default_in is None or int(default_in) < 0:
                return None
            default_info = sd.query_devices(int(default_in))
            return str(default_info["name"]).strip() if default_info else None
        except Exception:
            return None

    def _mic_input_device_mode(self) -> str:
        cfg = self._config.get("audio", {})
        if isinstance(cfg, dict):
            return str(cfg.get("input_device_mode", "fixed") or "fixed")
        return "fixed"

    def _configured_mic_input_device_name(self) -> str | None:
        cfg = self._config.get("audio", {})
        if isinstance(cfg, dict):
            return str(cfg.get("input_device") or "").strip() or None
        return None

    def _microphone_device_signature(self) -> tuple[tuple[str, ...], str | None, str, str | None, str | None]:
        from src.audio.recorder import AudioRecorder
        devices = AudioRecorder.list_devices()
        self._devices = {str(d.get("name", "")).strip(): int(d.get("index", -1)) for d in devices}
        default_name = self._current_default_input_device_name(devices)
        mode = self._mic_input_device_mode()
        configured = self._configured_mic_input_device_name()
        resolved = self._resolve_mic_input_device_name(refresh=False)
        return tuple(sorted(self._devices)), default_name, mode, configured, resolved

    def _schedule_mic_audio_watch(self, delay_ms: int = 2500) -> None:
        if self._destroying:
            return
        if self._mic_audio_watch_timer is not None:
            try:
                self._mic_audio_watch_timer.stop()
            except Exception:
                pass
        if not hasattr(self, "_mic_audio_watch_timer") or self._mic_audio_watch_timer is None:
            self._mic_audio_watch_timer = QTimer(self)
            self._mic_audio_watch_timer.timeout.connect(self._poll_mic_audio_watch)
        self._mic_audio_watch_timer.start(delay_ms)

    def _poll_mic_audio_watch(self) -> None:
        if self._destroying:
            return
        try:
            previous = self._last_mic_device_signature
            signature = self._microphone_device_signature()
            self._last_mic_device_signature = signature
            _, default_name, mode, configured_name, resolved_name = signature
            if not self._running:
                return
            recorder = self._recorder
            if recorder is None and not self._mic_recovery_in_progress:
                self._restart_microphone_capture("microphone recorder missing while running")
                return
            if recorder is not None and not recorder.is_running:
                self._restart_microphone_capture("microphone recorder stopped unexpectedly")
                return
            if recorder is not None:
                self._maybe_log_mic_diagnostics()
            active_norm = self._normalize_audio_device_name(self._active_mic_input_device_name or "")
            resolved_norm = self._normalize_audio_device_name(resolved_name or "")
            if previous is None:
                return
            previous_default = previous[1]
            previous_resolved = previous[4]
            if mode == "auto":
                if resolved_norm and resolved_norm != active_norm and (
                    previous_default != default_name
                    or self._normalize_audio_device_name(previous_resolved or "") != resolved_norm
                ):
                    logger.info(
                        "Detected default microphone change (previous_default=%s current_default=%s active=%s resolved=%s)",
                        previous_default,
                        default_name,
                        self._active_mic_input_device_name,
                        resolved_name,
                    )
                    self._restart_microphone_capture("system default microphone changed")
                    return
            else:
                configured_exists = bool(configured_name and configured_name in self._devices)
                if not configured_exists and resolved_norm and resolved_norm != active_norm:
                    logger.warning(
                        "Configured microphone is unavailable, falling back (configured=%s fallback=%s)",
                        configured_name,
                        resolved_name,
                    )
                    self._restart_microphone_capture("configured microphone unavailable")
                    return
        except Exception:
            logger.exception("Microphone device watch failed")
        finally:
            if not self._destroying:
                self._schedule_mic_audio_watch()

    def _maybe_log_mic_diagnostics(self) -> None:
        now = time.monotonic()
        if (now - self._last_mic_diagnostic_log_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        recorder = self._recorder
        if recorder is None or not hasattr(recorder, "diagnostics_snapshot"):
            return
        idle_anchor = max(
            float(self.__dict__.get("_last_mic_result_at", 0.0) or 0.0),
            float(self.__dict__.get("_last_mic_started_at", 0.0) or 0.0),
        )
        if idle_anchor > 0 and (now - idle_anchor) < LISTEN_DIAGNOSTIC_IDLE_S:
            return
        self._last_mic_diagnostic_log_at = now
        stats = recorder.diagnostics_snapshot()
        last_non_silent_at = float(stats.get("last_non_silent_at") or 0.0)
        audio_state = "no_mic_audio"
        if last_non_silent_at > 0 and (now - last_non_silent_at) < LISTEN_DIAGNOSTIC_IDLE_S:
            audio_state = "audio_present_but_no_segment"
        logger.warning(
            "Microphone diagnostics state=%s idle_for=%.1fs stats=%s active=%s resolved=%s muted=%s output_format=%s",
            audio_state,
            now - idle_anchor if idle_anchor > 0 else 0.0,
            stats,
            self._active_mic_input_device_name,
            self._resolve_mic_input_device_name(refresh=False),
            bool(getattr(self, "_mic_muted", False)),
            self._get_output_format(),
        )

    def _restart_microphone_capture(self, reason: str) -> None:
        if self._destroying or not self._running or self._mic_recovery_in_progress:
            return
        logger.warning("Restarting microphone capture (reason=%s)", reason)
        self._mic_recovery_in_progress = True
        try:
            self._stop_microphone_capture()
            self._start_microphone_capture()
        except Exception as exc:
            logger.warning("Microphone capture restart failed: %s", exc)
        finally:
            self._mic_recovery_in_progress = False

    def _on_audio_segment(self, audio, source: str = MIC_SOURCE) -> None:
        if not self._running:
            return
        if source == MIC_SOURCE:
            if getattr(self, "_mic_muted", False):
                self._reset_streaming_state(MIC_SOURCE)
                return
            self._last_mic_result_at = time.monotonic()
            self._reset_streaming_state(MIC_SOURCE)
            asr_lang = self._current_asr_lang
            selected_src_lang = self._current_src_lang
        else:
            if self._desktop_listen_should_yield_to_mic():
                return
            self._reset_streaming_state(DESKTOP_SOURCE)
            selected_src_lang = self._listen_source_language()
            asr_lang = selected_src_lang
        q = self._final_task_queues.get(source)
        if q is None:
            return
        result = self._enqueue_latest(
            q,
            (audio, asr_lang, selected_src_lang, self._listen_session, source),
        )
        if result != "enqueued":
            logger.debug("Final queue update result=%s source=%s", result, source)

    # ----------------------------------------------------------------
    # ASR processing
    # ----------------------------------------------------------------
    def _process_partial_audio_chunk(self, audio, asr_lang, generation: int, session_id: int, source: str) -> None:
        if not self._running or session_id != self._listen_session:
            return
        if source == MIC_SOURCE and getattr(self, "_mic_muted", False):
            return
        if generation != self._partial_generation:
            return
        if not self._should_process_partial_asr(source):
            return
        try:
            text = self._transcribe_for_source(source, audio, asr_lang, is_final=False)
            if not text or not self._running or session_id != self._listen_session:
                return
            if generation != self._partial_generation:
                return
            self._call_in_ui(lambda t=text: self._on_partial_result(t))
        except Exception as e:
            logger.debug("Partial transcription failed: %s", e)

    def _asr_for_source(self, source: str):
        if source == DESKTOP_SOURCE and self._listen_asr is not None:
            return self._listen_asr
        return self._asr

    def _asr_runtime_device_for_source(self, source: str) -> str:
        try:
            asr = self._asr_for_source(source)
            device = getattr(asr, "runtime_device", None)
            if device is None:
                device = getattr(asr, "device", "")
            return str(device or "").strip().lower()
        except Exception:
            return ""

    def _should_process_partial_asr(self, source: str) -> bool:
        asr = self._asr_for_source(source)
        if not bool(getattr(asr, "supports_partial", True)):
            return False
        if self._performance_profile() == "low_power":
            return False
        return self._asr_runtime_device_for_source(source) == "cuda"

    def _refresh_asr_transcribe_locks(self) -> None:
        guard = threading.Lock()
        locks: dict[int, threading.Lock] = {}
        for asr in (self._asr, self._listen_asr):
            if asr is not None:
                locks.setdefault(id(asr), threading.Lock())
        self._asr_transcribe_lock_guard = guard
        self._asr_transcribe_locks = locks

    def _asr_transcribe_lock_for(self, asr) -> threading.Lock:
        guard = self.__dict__.get("_asr_transcribe_lock_guard")
        locks = self.__dict__.get("_asr_transcribe_locks")
        if guard is None or locks is None:
            guard = threading.Lock()
            locks = {}
            self._asr_transcribe_lock_guard = guard
            self._asr_transcribe_locks = locks
        with guard:
            lock = locks.get(id(asr))
            if lock is None:
                lock = threading.Lock()
                locks[id(asr)] = lock
            return lock

    def _desktop_listen_should_yield_to_mic(self) -> bool:
        mic_asr = getattr(self, "_asr", None)
        listen_asr = getattr(self, "_listen_asr", None)
        if mic_asr is None or listen_asr is not mic_asr:
            return False
        if self._mic_in_speech:
            return True
        for task_queue in (
            self._final_task_queues.get(MIC_SOURCE),
            self._partial_task_queues.get(MIC_SOURCE),
        ):
            try:
                if task_queue is not None and task_queue.qsize() > 0:
                    return True
            except Exception:
                continue
        return False

    def _transcribe_for_source(
        self,
        source: str,
        audio,
        asr_lang=None,
        *,
        language=None,
        is_final: bool,
    ) -> str:
        if asr_lang is None:
            asr_lang = language
        asr = self._asr_for_source(source)
        if asr is None:
            raise RuntimeError("ASR is not ready")
        lock = self._asr_transcribe_lock_for(asr)
        if not lock.acquire(blocking=False):
            if source == DESKTOP_SOURCE and asr is self._asr:
                logger.debug("Dropping desktop ASR because shared microphone ASR is busy")
                return ""
            lock.acquire()
        try:
            return asr.transcribe(audio, language=asr_lang, is_final=is_final)
        finally:
            lock.release()

    @staticmethod
    def _format_listen_translation(original: str, translated: str) -> str:
        return ListenPipeline.format_translation(original, translated)

    def _format_listen_text(self, text: str) -> str:
        return ListenPipeline.format_chatbox_text(self._copy("listen_prefix"), text)

    def _process_listen_final_text(self, text: str, selected_src_lang: str | None, session_id: int) -> None:
        clean = str(text or "").strip()
        if len(clean) < 2:
            return
        pipeline = self._ensure_listen_pipeline()
        plan = pipeline.create_plan(
            clean,
            source_language=selected_src_lang or "auto",
            target_language=self._listen_target_language(),
            listen_prefix=self._copy("listen_prefix"),
        )
        if plan.needs_api_translation and self._translation_cooldown_active(DESKTOP_SOURCE):
            return
        try:
            result, translator = pipeline.translate_plan(plan, self._translator)
            self._translator = translator
            if result.api_translation_used:
                self._record_translation_success()
        except Exception as exc:
            friendly = self._format_translation_error(exc)
            self._record_translation_failure(friendly)
            raise
        if not self._running or session_id != self._listen_session:
            return
        self._last_listen_result_at = time.monotonic()
        output_message = result.output_message
        if output_message is not None:
            self._call_in_ui(lambda message=output_message: self._dispatch_output_message(message, sinks=("overlay",)))
        if self._listen_send_to_chatbox_enabled():
            self._call_in_ui(lambda payload=result.chatbox_text, sid=session_id: self._send_listen_chatbox(payload, session_id=sid))

    def _send_chatbox_payload(self, message: str, *, session_id: int | None = None) -> None:
        if session_id is not None and (not self._running or session_id != self._listen_session):
            return
        clean = _normalize_chatbox_text(message)
        if not clean:
            return
        try:
            sent = self._ensure_output_dispatcher().send_chatbox_text(self._ensure_sender(), clean)
            if not sent:
                self._set_bottom(self._copy("chatbox_send_not_queued"))
        except Exception as exc:
            self._set_bottom(str(exc))
            self._pulse_avatar_error()

    def _send_listen_chatbox(self, message: str, *, session_id: int | None = None) -> None:
        self._send_chatbox_payload(message, session_id=session_id)

    def _mic_send_to_chatbox_enabled(self) -> bool:
        trans_cfg = self._config.get("translation", {})
        if not isinstance(trans_cfg, dict):
            return True
        return bool(trans_cfg.get("send_to_chatbox", True))

    def _process_final_audio_segment(self, audio, asr_lang, selected_src_lang: str | None, session_id: int, source: str) -> None:
        if not self._running or session_id != self._listen_session:
            return
        if source == MIC_SOURCE and getattr(self, "_mic_muted", False):
            return
        if source == DESKTOP_SOURCE and self._listen_tts_echo_suppress_active():
            return
        if source == DESKTOP_SOURCE and self._desktop_listen_should_yield_to_mic():
            return
        try:
            if source == MIC_SOURCE:
                self._call_in_ui(lambda: self._set_runtime_status("status_translating", "accent"))
            elif source == DESKTOP_SOURCE:
                self._call_in_ui(lambda: self._set_floating_listen_status(True))
            text = self._transcribe_for_source(source, audio, asr_lang, is_final=True)
            if not text or not self._running or session_id != self._listen_session:
                return
            if source == DESKTOP_SOURCE:
                self._process_listen_final_text(text, selected_src_lang, session_id)
                return
            pipeline = self._ensure_mic_pipeline()
            plan = pipeline.create_plan(
                text,
                source_language=selected_src_lang or "auto",
                target_language=self._current_tgt_lang,
                second_target_language=getattr(self, "_current_tgt_lang_2", "en") or "en",
                third_target_language=getattr(self, "_current_tgt_lang_3", "") or "",
                context_source=source,
            )
            if plan.needs_api_translation and self._translation_cooldown_active(source):
                return
            result, translator = pipeline.translate_plan(plan, self._translator)
            self._translator = translator
            if not self._running or session_id != self._listen_session:
                return
            if result.api_translation_used:
                self._record_translation_success()
            output_message = result.output_message

            def update_translation_ui() -> None:
                if not self._running or session_id != self._listen_session:
                    return
                if output_message is not None:
                    self._dispatch_output_message(output_message, sinks=("ui", "overlay"))

            self._call_in_ui(update_translation_ui)
            if self._mic_send_to_chatbox_enabled():
                self._call_in_ui(lambda payload=result.chatbox_text, sid=session_id: self._send_chatbox_payload(payload, session_id=sid))
            if output_message is not None:
                self._call_in_ui(
                    lambda message=output_message, sid=session_id: (
                        self._dispatch_output_message(message, sinks=("tts",))
                        if self._running and sid == self._listen_session
                        else False
                    )
                )
        except Exception as e:
            logger.debug("Final transcription failed: %s", e)
            if source == DESKTOP_SOURCE:
                friendly = self._format_translation_error(e)
                self._call_in_ui(lambda message=friendly.short_message: self._set_bottom(message))
                self._call_in_ui(lambda message=friendly.inline_message: self._show_listen_translation(message, source="error"))
            else:
                friendly = self._format_translation_error(e)
                self._record_translation_failure(friendly)
                self._call_in_ui(lambda message=friendly.short_message: self._set_bottom(message, "danger"))
                self._call_in_ui(self._pulse_avatar_error)
        finally:
            if source == MIC_SOURCE:
                self._mic_in_speech = False
                self._call_in_ui(
                    lambda: self._restore_runtime_status("status_speaking", "status_translating")
                )
            elif source == DESKTOP_SOURCE:
                self._call_in_ui(self._restore_floating_window_waiting_if_idle)

    def _on_partial_result(self, text: str) -> None:
        self._set_source_text(text)

    def _on_final_result(self, text: str, src_lang: str | None, source: str) -> None:
        if source == MIC_SOURCE:
            self._set_source_text(text)
            self._last_tgt2_text = ""
            self._last_tgt3_text = ""
            self._last_tgt_text = text
            self._show_tgt(text)
        else:
            self._show_listen_translation(text, source=source, payload=text)

    # ----------------------------------------------------------------
    # Manual translation
    # ----------------------------------------------------------------
    def _ensure_output_dispatcher(self) -> OutputDispatcher:
        dispatcher = getattr(self, "_output_dispatcher", None)
        if dispatcher is None:
            dispatcher = OutputDispatcher(lambda: getattr(self, "_config", {}))
            self._output_dispatcher = dispatcher
        dispatcher.register_sink("ui", self._dispatch_ui_sink)
        dispatcher.register_sink("tts", self._dispatch_tts_sink)
        overlay_service = getattr(self, "_overlay_service", None)
        if overlay_service is not None:
            dispatcher.register_sink("overlay", overlay_service.show_message)
        return dispatcher

    def _dispatch_output_message(
        self,
        message: OutputMessage,
        *,
        sinks: tuple[str, ...] | list[str] | set[str] | None = None,
    ) -> dict[str, bool]:
        sink_names = {str(name or "").strip() for name in sinks} if sinks is not None else None
        if sink_names is None or "overlay" in sink_names:
            try:
                self._ensure_overlay_service(create_backend=bool(getattr(self, "_listen_overlay_enabled", False)))
            except Exception:
                logger.debug("Failed to prepare overlay output sink", exc_info=True)
        return self._ensure_output_dispatcher().dispatch(message, sinks=sinks)

    def _dispatch_ui_sink(self, message: OutputMessage) -> bool:
        source = str(message.source or "")
        if message.is_error:
            self._show_tgt(message.display_text or message.translated_text, is_error=True)
            return True
        if source not in {"manual", "mic"}:
            return False
        if message.original_text:
            self._set_source_text(message.original_text)
        self._show_tgt(message.display_text or message.translated_text)
        self._last_tgt_text = message.translated_text
        self._last_tgt2_text = message.translated_text_2
        self._last_tgt3_text = message.translated_text_3
        return True

    def _dispatch_tts_sink(self, message: OutputMessage) -> bool:
        source = str(message.source or "")
        if message.is_error or source == "listen":
            return False
        if source == "manual":
            return self._auto_read_translation_result(
                original_text=message.original_text,
                translated_text=message.translated_text,
            )
        if source == "mic":
            return self._auto_read_mic_translation(
                original_text=message.original_text,
                translated_text=message.translated_text,
            )
        return False

    def _ensure_mic_pipeline(self) -> MicPipeline:
        pipeline = getattr(self, "_mic_pipeline", None)
        if pipeline is None:
            pipeline = MicPipeline(
                lambda: getattr(self, "_config", {}),
                self._ensure_output_dispatcher(),
                translator_factory=create_translator,
            )
            self._mic_pipeline = pipeline
        return pipeline

    def _ensure_listen_pipeline(self) -> ListenPipeline:
        pipeline = getattr(self, "_listen_pipeline", None)
        if pipeline is None:
            pipeline = ListenPipeline(
                lambda: getattr(self, "_config", {}),
                self._ensure_output_dispatcher(),
                translator_factory=create_translator,
            )
            self._listen_pipeline = pipeline
        return pipeline

    def _ensure_manual_translation_controller(self) -> ManualTranslationController:
        controller = getattr(self, "_manual_translation_controller", None)
        if controller is not None:
            return controller
        controller = ManualTranslationController(
            self._config,
            self._ensure_output_dispatcher(),
            translator_factory=create_translator,
            language_detector=self._detect_source_lang,
            error_formatter=self._format_translation_error,
        )
        controller.started.connect(self._on_manual_translate_started)
        controller.succeeded.connect(self._on_manual_translate_success)
        controller.failed.connect(self._on_manual_translate_error)
        controller.worker_finished.connect(self._finish_manual_translate_worker)
        self._manual_translation_controller = controller
        return controller

    def _manual_generation_is_current(self, generation: int) -> bool:
        if generation == getattr(self, "_manual_translation_generation", 0):
            return True
        controller = getattr(self, "_manual_translation_controller", None)
        if controller is not None and generation == controller.generation and not getattr(self, "_translating", False):
            self._manual_translation_generation = generation
            return True
        return False

    def _do_manual_translate(self) -> None:
        src_text = self._src_text
        if not src_text:
            return
        controller = self._ensure_manual_translation_controller()
        controller.translator = getattr(self, "_translator", None)
        request = ManualTranslationRequest(
            text=src_text,
            source_language=getattr(self, "_current_src_lang", None),
            target_language=getattr(self, "_current_tgt_lang", "ja") or "ja",
            second_target_language=getattr(self, "_current_tgt_lang_2", "en") or "en",
            third_target_language=getattr(self, "_current_tgt_lang_3", "") or "",
        )
        generation = controller.start(request)
        self._translator = controller.translator
        if generation is not None:
            self._manual_translation_generation = generation

    def _on_manual_translate_started(self, generation: int) -> None:
        if not self._manual_generation_is_current(generation):
            return
        self._translating = True
        self._set_status(self._t("status_translating"), "accent", key="status_translating")
        if getattr(self, "_translate_btn", None):
            self._translate_btn.setText(self._t("translating"))
            self._translate_btn.setEnabled(False)
        self._schedule_manual_translation_watchdog(generation)

    def _on_manual_translate_success(self, result) -> None:
        if not self._manual_generation_is_current(result.generation):
            return
        self._translator = self._ensure_manual_translation_controller().translator
        output_message = self._ensure_output_dispatcher().build_message(
            source="manual",
            original_text=result.original_text,
            translated_text=result.translated_text,
            translated_text_2=result.translated_text_2,
            translated_text_3=result.translated_text_3,
            display_text=result.display_text,
        )
        self._dispatch_output_message(output_message, sinks=("ui", "overlay"))
        self._finish_manual_translation(output_message=output_message)

    def _on_manual_translate_error(self, error) -> None:
        if not self._manual_generation_is_current(error.generation):
            return
        self._translator = self._ensure_manual_translation_controller().translator
        friendly = error.friendly_error
        self._show_tgt(friendly.short_message, is_error=True)
        self._pulse_avatar_error()
        self._finish_manual_translation(success=False)

    def _finish_manual_translate_worker(self, generation: int) -> None:
        if not self._manual_generation_is_current(generation):
            return
        self._translating = False
        self._refresh_translate_button()

    def _schedule_manual_translation_watchdog(self, generation: int) -> None:
        timeout_s = self._manual_translation_watchdog_s()
        QTimer.singleShot(
            int(timeout_s * 1000),
            lambda g=generation, timeout=timeout_s: self._on_manual_translation_timeout(g, timeout),
        )

    def _manual_translation_watchdog_s(self) -> float:
        return self._ensure_manual_translation_controller().timeout_seconds()

    def _on_manual_translation_timeout(self, generation: int, timeout_s: float) -> None:
        if generation != getattr(self, "_manual_translation_generation", 0) or not self._translating:
            return
        self._manual_translation_generation = self._ensure_manual_translation_controller().invalidate()
        self._translating = False
        friendly = self._format_translation_error(
            TimeoutError(f"Translation request timed out after {timeout_s:.0f}s")
        )
        self._show_tgt(friendly.short_message, is_error=True)
        self._pulse_avatar_error()
        self._finish_manual_translation(success=False)
        self._refresh_translate_button()

    def _finish_manual_translation(self, *, success: bool = True, output_message: OutputMessage | None = None) -> None:
        send_after = self._manual_send_after_translate
        self._manual_send_after_translate = False
        callback = self._manual_done_callback
        self._manual_done_callback = None
        sent = False
        if success and send_after:
            sent = self._send_to_vrc()
        if success:
            if output_message is not None:
                self._dispatch_output_message(output_message, sinks=("tts",))
            else:
                self._auto_read_manual_translation()
            key = "status_running" if self._running else "status_ready"
            self._set_status(
                self._t(key),
                "accent" if self._running else "success",
                key=key,
            )
        else:
            self._set_status(self._t("status_error"), "danger", key="status_error")
        if callable(callback):
            callback(bool(success and (sent or not send_after)))

    def _refresh_translate_button(self) -> None:
        translate_btn = getattr(self, "_translate_btn", None)
        if translate_btn:
            translate_btn.setText(self._t("translate"))
            translate_btn.setEnabled(True)

    # ----------------------------------------------------------------
    # VRC sending
    # ----------------------------------------------------------------
    def _send_to_vrc(self) -> bool:
        tgt_text = self._last_tgt_text
        tgt2_text = self._last_tgt2_text
        tgt3_text = getattr(self, "_last_tgt3_text", "")
        src_text = self._src_text
        if not tgt_text and not src_text:
            return False
        try:
            sent = self._ensure_output_dispatcher().send_chatbox(
                self._ensure_sender(),
                original_text=src_text,
                translated_text=tgt_text,
                translated_text_2=tgt2_text,
                translated_text_3=tgt3_text,
            )
            if sent:
                return True
        except Exception as e:
            QMessageBox.critical(self, self._t("send_failed_title"), str(e))
        return False

    def _ensure_sender(self):
        if self._sender is None:
            self._sender = self._create_sender()
        return self._sender

    def _ensure_osc_service(self):
        service = getattr(self, "_osc_service", None)
        if service is not None:
            return service
        from src.core.osc_service import OscService

        service = OscService()
        service.setParent(self)
        service.mute_self_changed.connect(self._handle_vrchat_mute_self)
        service.avatar_parameter_received.connect(self._handle_osc_avatar_parameter)
        service.error.connect(lambda message: self._set_bottom(str(message), "warning"))
        self._osc_service = service
        return service

    def _create_sender(self) -> VRCOSCSender:
        osc_cfg = self._config.get("osc", {})
        min_interval = float(osc_cfg.get("min_send_interval_s", 0.8))
        service = self._ensure_osc_service()
        sender = service.connect_sender(
            host=osc_cfg.get("send_host", "127.0.0.1"),
            port=int(osc_cfg.get("send_port", 9000)),
            min_send_interval_s=min_interval,
        )
        return sender

    def _close_osc_sender(self) -> None:
        service = getattr(self, "_osc_service", None)
        if service is not None:
            try:
                service.disconnect_sender()
            except Exception:
                logger.debug("Failed to disconnect OSC sender", exc_info=True)
            self._sender = None
            return
        if self._sender:
            try:
                self._sender.close()
            except Exception:
                pass
            self._sender = None

    def _apply_osc_listener_config(self) -> None:
        if self._destroying:
            return
        osc_cfg = self._config.setdefault("osc", {})
        service = self._ensure_osc_service()
        if not bool(osc_cfg.get("listener_enabled", False)):
            service.stop_listener()
            return
        try:
            service.start_listener(
                host=str(osc_cfg.get("receive_host", "127.0.0.1") or "127.0.0.1"),
                port=int(osc_cfg.get("receive_port", 9001)),
                sync_mute_self=bool(osc_cfg.get("sync_mute_self", True)),
            )
        except Exception as exc:
            self._set_bottom(str(exc), "warning")

    def _osc_control_params(self) -> dict[str, str]:
        osc_cfg = self._config.setdefault("osc", {})
        prefix = str(osc_cfg.get("control_prefix", "Mio") or "Mio").strip() or "Mio"
        params = osc_cfg.setdefault("control_params", {})
        if not isinstance(params, dict):
            params = {}
            osc_cfg["control_params"] = params
        defaults = {
            "mic": f"{prefix}ToggleMic",
            "listen": f"{prefix}ToggleListen",
            "tts": f"{prefix}ToggleTts",
            "overlay": f"{prefix}ToggleOverlay",
        }
        return {key: str(params.get(key, value) or value).strip() for key, value in defaults.items()}

    def _handle_vrchat_mute_self(self, muted: bool) -> None:
        if not bool(self._config.get("osc", {}).get("sync_mute_self", True)):
            return
        self._set_mic_muted(bool(muted), bottom_key="mic_mute_on" if muted else "mic_mute_off")

    def _handle_osc_avatar_parameter(self, name: str, value: object) -> None:
        osc_cfg = self._config.get("osc", {})
        if not bool(osc_cfg.get("allow_avatar_control", False)):
            return
        desired = _coerce_osc_bool(value)
        if desired is None:
            return
        param_name = str(name or "").strip()
        controls = self._osc_control_params()
        if param_name == controls.get("mic"):
            if desired and not self._running:
                self._do_start()
            elif not desired and self._running:
                self._do_stop()
        elif param_name == controls.get("listen"):
            if desired != bool(self._desktop_capture_enabled):
                self._set_desktop_capture_enabled(desired, persist=True)
        elif param_name == controls.get("tts"):
            self._set_tts_enabled_from_avatar(desired)
        elif param_name == controls.get("overlay"):
            if desired != bool(self._listen_overlay_enabled):
                self._set_listen_overlay_enabled(desired, persist=True)

    def _set_tts_enabled_from_avatar(self, enabled: bool) -> None:
        tts_cfg = self._tts_config()
        if bool(tts_cfg.get("enabled", False)) == bool(enabled):
            return
        tts_cfg["enabled"] = bool(enabled)
        self._sync_tts_enabled_from_config()
        if not enabled:
            self._reset_tts_manager()
        self._schedule_config_save()
        self._set_bottom("TTS enabled" if enabled else "TTS disabled")

    # ----------------------------------------------------------------
    # TTS helpers
    # ----------------------------------------------------------------
    def _current_tts_engine(self) -> str:
        return str(self._tts_config().get("engine", "edge") or "edge").strip() or "edge"

    def _current_tts_engine_config(self) -> dict:
        tts_cfg = self._tts_config()
        engine_cfg = tts_cfg.get(self._current_tts_engine(), {})
        resolved = dict(engine_cfg) if isinstance(engine_cfg, dict) else {}
        if self._current_tts_engine() == "qwen_tts" and self._get_output_format() != "original_only":
            trans_cfg = self._config.get("translation", {})
            fallback_target = trans_cfg.get("target_language", "ja") if isinstance(trans_cfg, dict) else "ja"
            language_type = self._qwen_tts_language_type_from_target(
                getattr(self, "_current_tgt_lang", fallback_target)
            )
            if language_type:
                resolved["language_type"] = language_type
            self._apply_qwen_tts_persona_instructions(resolved)
        return resolved

    def _apply_qwen_tts_persona_instructions(self, engine_cfg: dict) -> None:
        from src.tts.persona_instructions import (
            build_qwen_tts_persona_instructions,
            qwen_tts_model_supports_instructions,
        )

        if engine_cfg.get("instructions"):
            return
        if not qwen_tts_model_supports_instructions(engine_cfg.get("model", "")):
            return
        instructions = build_qwen_tts_persona_instructions(self._config)
        if not instructions:
            return
        engine_cfg["instructions"] = instructions
        engine_cfg.setdefault("optimize_instructions", True)

    @staticmethod
    def _qwen_tts_language_type_from_target(target_language: object) -> str:
        target = str(target_language or "").strip().lower().replace("_", "-")
        if target in {"ja", "jp", "japanese", "日本語", "日文", "日语"}:
            return "Japanese"
        if target in {"zh", "zh-cn", "cn", "chinese", "中文", "简体中文", "中国語"}:
            return "Chinese"
        if target in {"ko", "kr", "korean", "한국어", "韩语", "韓国語"}:
            return "Korean"
        if target in {"en", "en-us", "en-gb", "english", "英文", "英语"}:
            return "English"
        return ""

    def _current_tts_strategy(self) -> str:
        strategy = str(self._config.get("simul_mode", {}).get("tts_strategy", "queue") or "queue")
        return "latest" if strategy == "latest" else "queue"

    @staticmethod
    def _safe_tts_rate(value: object) -> float:
        try:
            return max(0.5, min(float(value), 2.0))
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _safe_tts_volume(value: object) -> float:
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return 0.8

    def _ensure_tts_manager(self):
        if self._tts_manager is not None:
            return self._tts_manager
        from src.tts.manager import TTSManager

        tts_cfg = self._tts_config()
        perf_cfg = self._performance_config()
        manager = TTSManager(
            engine_name=self._current_tts_engine(),
            cache_enabled=True,
            allow_fallback=bool(tts_cfg.get("allow_fallback", True)),
            output_device=tts_cfg.get("output_device"),
            output_device_name=str(tts_cfg.get("output_device_name") or ""),
            prefer_virtual_output=bool(tts_cfg.get("output_to_vrchat", False)),
            monitor_output=bool(tts_cfg.get("monitor_enabled", False)),
            sbv2_device=str(tts_cfg.get("style_bert_vits2", {}).get("device", "cpu")),
            sbv2_bert_language=str(tts_cfg.get("style_bert_vits2", {}).get("bert_language", "jp")),
            engine_config=self._current_tts_engine_config(),
            max_cache_size_mb=int(perf_cfg.get("tts_cache_max_mb", 24)),
            max_cache_items=int(perf_cfg.get("tts_cache_max_items", 60)),
        )
        if not manager.is_available():
            return None
        manager.start()
        self._tts_manager = manager
        return manager

    def _reset_tts_manager(self) -> None:
        manager = getattr(self, "_tts_manager", None)
        if manager is None:
            return
        self._tts_manager = None
        try:
            stop_playback = getattr(manager, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()
        except Exception:
            logger.debug("Failed to stop TTS manager", exc_info=True)

    def _tts_voice_for_engine(self, manager) -> str:
        engine_cfg = self._current_tts_engine_config()
        voice = str(engine_cfg.get("voice") or "").strip()
        if voice:
            return voice
        try:
            voices = manager.get_available_voices()
        except Exception:
            voices = []
        if voices:
            first = voices[0]
            if isinstance(first, dict):
                return str(first.get("id") or "").strip()
            return str(getattr(first, "id", "") or "").strip()
        return ""

    def _should_suppress_tts_echo_from_listen(self) -> bool:
        tts_cfg = self._tts_config()
        if not (
            bool(getattr(self, "_desktop_capture_enabled", False))
            and bool(tts_cfg.get("enabled", False))
            and bool(tts_cfg.get("output_to_vrchat", False))
        ):
            return False
        if bool(tts_cfg.get("monitor_enabled", False)):
            return True
        listen_device = self._desktop_output_device_name()
        tts_device = self._match_desktop_device_name(str(tts_cfg.get("output_device_name") or ""))
        return tts_device is not None and self._desktop_device_names_match(listen_device, tts_device)

    def _tts_echo_lock(self) -> threading.Lock:
        lock = self.__dict__.get("_listen_tts_echo_lock")
        if lock is None:
            lock = threading.Lock()
            self._listen_tts_echo_lock = lock
        return lock

    def _begin_listen_tts_echo_suppression(self) -> None:
        now = time.monotonic()
        with self._tts_echo_lock():
            self._listen_tts_echo_pending_count = int(
                self.__dict__.get("_listen_tts_echo_pending_count", 0) or 0
            ) + 1
            self._listen_tts_echo_suppress_until = max(
                float(self.__dict__.get("_listen_tts_echo_suppress_until", 0.0) or 0.0),
                now + LISTEN_TTS_ECHO_SUPPRESS_PENDING_S,
            )

    def _finish_listen_tts_echo_suppression(self, tail_seconds: float) -> None:
        now = time.monotonic()
        with self._tts_echo_lock():
            pending = max(
                0,
                int(self.__dict__.get("_listen_tts_echo_pending_count", 0) or 0) - 1,
            )
            self._listen_tts_echo_pending_count = pending
            if pending <= 0:
                self._listen_tts_echo_suppress_until = now + max(0.0, float(tail_seconds))

    def _listen_tts_echo_suppress_active(self) -> bool:
        return time.monotonic() <= float(
            self.__dict__.get("_listen_tts_echo_suppress_until", 0.0) or 0.0
        )

    def _queue_tts_playback(self, text: str) -> bool:
        if not self._sync_tts_enabled_from_config():
            return False
        clean = str(text or "").strip()
        if not clean:
            return False
        now = time.monotonic()
        last_tts_text = getattr(self, "_last_tts_text", "")
        last_tts_at = float(getattr(self, "_last_tts_at", 0.0) or 0.0)
        tts_dedup_s = float(getattr(self, "_tts_dedup_s", 0.5) or 0.5)
        if (
            clean == last_tts_text
            and now - last_tts_at < tts_dedup_s
        ):
            logger.debug("TTS deduplicated (same text within %.1fs): %s", tts_dedup_s, clean)
            return False
        self._last_tts_text = clean
        self._last_tts_at = now
        manager = self._ensure_tts_manager()
        if manager is None:
            return False
        if self._current_tts_strategy() == "latest":
            manager.clear_queue()
        voice = self._tts_voice_for_engine(manager)
        if not voice:
            return False
        suppress_echo = self._should_suppress_tts_echo_from_listen()
        if suppress_echo:
            self._begin_listen_tts_echo_suppression()

        def _done(success: bool, _message: str) -> None:
            if suppress_echo:
                self._finish_listen_tts_echo_suppression(
                    LISTEN_TTS_ECHO_SUPPRESS_TAIL_S if success else 0.0
                )

        engine_cfg = self._current_tts_engine_config()
        accepted = manager.speak(
            clean,
            voice,
            self._safe_tts_rate(engine_cfg.get("rate")),
            self._safe_tts_volume(engine_cfg.get("volume")),
            callback=_done,
        )
        if not accepted and suppress_echo:
            self._finish_listen_tts_echo_suppression(0.0)
        return bool(accepted)

    def _manual_translation_tts_text(self, *, original_text: str, translated_text: str) -> str:
        if self._get_output_format() == "original_only":
            return original_text
        return translated_text or original_text

    def _auto_read_translation_result(self, *, original_text: str, translated_text: str) -> bool:
        tts_cfg = self._tts_config()
        if not self._sync_tts_enabled_from_config():
            return False
        if not bool(tts_cfg.get("auto_read", True)):
            return False
        text = self._manual_translation_tts_text(
            original_text=original_text,
            translated_text=translated_text,
        )
        return self._queue_tts_playback(text)

    def _auto_read_mic_translation(self, *, original_text: str, translated_text: str) -> bool:
        return self._auto_read_translation_result(
            original_text=original_text,
            translated_text=translated_text,
        )

    def _auto_read_manual_translation(self) -> bool:
        return self._auto_read_translation_result(
            original_text=self._src_text,
            translated_text=self._last_tgt_text,
        )

    def _listen_suppress_reason(self, text: str) -> str | None:
        del text
        return "own_tts_playback" if self._listen_tts_echo_suppress_active() else None

    # ----------------------------------------------------------------
    # Callbacks / status
    # ----------------------------------------------------------------
    def _set_runtime_status(self, key: str, color: str = "accent") -> None:
        if getattr(self, "_status_label", None) is None:
            return
        self._set_status(self._t(key), color, key=key)

    def _restore_runtime_status(self, *keys: str) -> None:
        if getattr(self, "_status_label", None) is None:
            return
        current_key = getattr(self, "_status_key", None)
        if keys and current_key not in keys:
            return
        if current_key == "status_translating" and getattr(self, "_translating", False):
            return
        key = "status_running" if getattr(self, "_running", False) else "status_ready"
        self._set_status(
            self._t(key),
            "accent" if key == "status_running" else "success",
            key=key,
        )

    def _handle_mic_vad_state(self, in_speech: bool) -> None:
        active = bool(in_speech) and not getattr(self, "_mic_muted", False)
        self._mic_in_speech = active
        self._sync_avatar_speaking_state()
        if not getattr(self, "_running", False):
            return
        if active:
            self._set_runtime_status("status_speaking", "accent")
        else:
            self._restore_runtime_status("status_speaking")

    def _handle_listen_vad_state(self, in_speech: bool) -> None:
        active = bool(in_speech)
        self._desktop_in_speech = active
        self._listen_in_speech = active
        self._sync_avatar_speaking_state()
        self._refresh_floating_window_status(active)

    def _on_started(self) -> None:
        self._refresh_start_button()
        self._set_status(self._t("status_running"), "accent", key="status_running")

    def _on_start_error(self, msg: str) -> None:
        self._set_status(self._t("status_error"), "danger", key="status_error")
        self._refresh_start_button()
        QMessageBox.critical(self, self._t("listen_start_failed_title"), msg)

    def _handle_model_progress(self, event) -> None:
        if isinstance(event, dict):
            stage = str(event.get("stage", "")).strip()
            progress = event.get("progress")
            if stage == "download_complete":
                self._set_bottom(self._t("model_ready"), "success", key="model_ready")
                self._show_bottom_progress(1.0, indeterminate=False)
                return
            if stage in {"download_prepare", "download", "loading"}:
                text = self._t("model_loading")
                self._set_status(self._t("status_model_loading"), "accent", key="status_model_loading")
                if progress is not None:
                    text = f"{text} {float(progress) * 100:.0f}%"
                self._set_bottom(text, "accent")
                self._show_bottom_progress(float(progress) if progress is not None else None, indeterminate=progress is None)
                return
            if stage == "ready":
                self._set_bottom(self._t("model_ready"), "success", key="model_ready")
                self._hide_bottom_progress()
                return
            msg = str(event.get("message", "")).strip()
            if msg:
                self._set_bottom(msg, "danger")
        else:
            msg = str(event).strip()
            if msg:
                self._set_bottom(msg)

    def _pulse_avatar_error(self) -> None:
        if not self._avatar_sync_enabled():
            return
        if hasattr(self, "_avatar_error_timer") and self._avatar_error_timer is not None:
            try:
                self._avatar_error_timer.stop()
            except Exception:
                pass
        self._sync_avatar_bool("error", True, force=True)
        if not hasattr(self, "_avatar_error_timer"):
            self._avatar_error_timer = QTimer(self)
            self._avatar_error_timer.setSingleShot(True)
            self._avatar_error_timer.timeout.connect(self._clear_avatar_error)
        self._avatar_error_timer.start(1400)

    def _clear_avatar_error(self) -> None:
        self._sync_avatar_bool("error", False, force=True)

    def _sync_avatar_bool(self, key: str, value: bool, *, force: bool = False) -> bool:
        if not self._avatar_sync_enabled():
            return False
        param_name = self._avatar_param_name(key)
        if not param_name:
            return False
        try:
            return self._ensure_sender().send_avatar_bool(param_name, value, force=force)
        except Exception:
            return False

    def _sync_avatar_int(self, key: str, value: int, *, force: bool = False) -> bool:
        if not self._avatar_sync_enabled():
            return False
        param_name = self._avatar_param_name(key)
        if not param_name:
            return False
        try:
            return self._ensure_sender().send_avatar_int(param_name, value, force=force)
        except Exception:
            return False

    def _current_translating_state(self) -> bool:
        with self._translation_state_lock:
            return self._active_translation_jobs > 0

    def _sync_avatar_target_language(self, *, force: bool = False) -> None:
        self._sync_avatar_int(
            "target_language",
            target_language_osc_value(self._current_tgt_lang),
            force=force,
        )

    def _sync_avatar_speaking_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "speaking",
            (
                bool(getattr(self, "_mic_in_speech", False))
                and not bool(getattr(self, "_mic_muted", False))
            )
            or bool(getattr(self, "_desktop_in_speech", False)),
            force=force,
        )

    def _sync_avatar_muted_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "muted",
            bool(getattr(self, "_mic_muted", False)),
            force=force,
        )

    def _sync_avatar_translating_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "translating",
            self._current_translating_state(),
            force=force,
        )

    def _sync_avatar_overlay_state(self, *, force: bool = False) -> None:
        self._sync_avatar_bool(
            "overlay",
            bool(getattr(self, "_listen_overlay_enabled", False)),
            force=force,
        )

    def _sync_all_avatar_params(self, *, force: bool = False) -> None:
        self._sync_avatar_target_language(force=force)
        self._sync_avatar_speaking_state(force=force)
        self._sync_avatar_muted_state(force=force)
        self._sync_avatar_translating_state(force=force)
        self._sync_avatar_overlay_state(force=force)
        self._sync_avatar_bool("error", False, force=force)

    def _reset_avatar_params(self) -> None:
        if not self._avatar_sync_enabled():
            return
        try:
            sender = self._ensure_sender()
        except Exception:
            return
        sender.clear_avatar_state(
            [
                (self._avatar_param_name("translating"), False),
                (self._avatar_param_name("speaking"), False),
                (self._avatar_param_name("muted"), False),
                (self._avatar_param_name("error"), False),
                (self._avatar_param_name("target_language"), 0),
                (self._avatar_param_name("overlay"), False),
            ]
        )

    def _refresh_runtime_status(self) -> None:
        self._refresh_desktop_capture_button()
        if self._current_translating_state():
            self._set_status(self._t("translating"), "accent")
        else:
            self._set_status(self._t("status_running"), "accent", key="status_running")
        self._refresh_mic_mute_button()

    # ----------------------------------------------------------------
    # UI helpers
    # ----------------------------------------------------------------
    def _t(self, key: str, **kwargs) -> str:  # type: ignore[assignment]
        if kwargs is None:
            kwargs = {}
        ui_lang = getattr(self, "_ui_lang", None) or get_ui_language(getattr(self, "_config", {}))
        return tr(ui_lang, key, **kwargs)

    def _set_status(self, text: str, color: str = "default", *, key: str | None = None) -> None:
        if key is not None:
            self._status_key = key
        self._status_color = color
        status_label = getattr(self, "_status_label", None)
        if status_label is None:
            return
        clean_text = self._clean_status_text(text)
        status_label.setText(f"● {clean_text}" if clean_text else "")
        status_label.setMinimumWidth(
            min(220, max(132, status_label.fontMetrics().horizontalAdvance(status_label.text()) + 26))
        )
        palette = _main_theme_palette(getattr(self, "_main_theme", "dark"))
        color_map = {
            "accent": str(palette["ACCENT"]),
            "success": str(palette["SUCCESS"]),
            "danger": str(palette["DANGER"]),
            "warning": str(palette["WARNING"]),
            "default": str(palette["TEXT_SECONDARY"]),
        }
        text_color = color_map.get(color, color_map["default"])
        status_label.setStyleSheet(
            "#statusPill {"
            f" color: {text_color};"
            " background: transparent;"
            " border: 0;"
            " border-radius: 0;"
            " padding: 0 4px;"
            " font-size: 12px;"
            " font-weight: 800;"
            "}"
        )

    def _set_bottom(self, text: str, color: str = "default", *, key: str | None = None) -> None:
        self._bottom_key = key
        self._bottom_color = color
        reported_text = self._bottom_report_text(text, color=color, key=key)
        ready_text = self._clean_status_text(self._t("status_ready"))
        if key == "status_ready" or reported_text == ready_text:
            reported_text = ""
        self._bottom_text = reported_text
        bottom_bar = getattr(self, "_bottom_bar", None)
        if bottom_bar:
            bottom_bar.setText(self._bottom_text)
            bottom_bar.setVisible(bool(self._bottom_text))
            palette = _main_theme_palette(getattr(self, "_main_theme", "dark"))
            color_map = {
                "accent": str(palette["ACCENT"]),
                "success": str(palette["SUCCESS"]),
                "danger": str(palette["DANGER"]),
                "warning": str(palette["WARNING"]),
                "default": str(palette["TEXT_SECONDARY"]),
            }
            text_color = color_map.get(color, color_map["default"])
            bottom_bar.setStyleSheet(f"#bottomLabel {{ color: {text_color}; }}")

    @staticmethod
    def _clean_status_text(text: object) -> str:
        value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        return value.lstrip("▶■●○• ").strip()

    def _bottom_report_text(self, text: object, *, color: str = "default", key: str | None = None) -> str:
        value = self._clean_status_text(text)
        if not value and key:
            value = self._clean_status_text(self._t(key))
        lowered = value.lower()
        if any(token in lowered for token in ("network", "connection", "timeout", "timed out", "dns", "socket", "网络")):
            return self._copy("report_network_error")
        if any(token in lowered for token in ("quota", "rate limit", "429", "too many requests", "限流", "配额")):
            return self._copy("report_request_limited")
        if any(token in lowered for token in ("api key", "apikey", "unauthorized", "401", "403", "配置", "密钥")):
            return self._copy("report_config_error")
        if color == "danger" and (len(value) > 52 or ":" in value or "：" in value):
            return self._copy("report_runtime_error")
        for separator in ("。", ".", "，", ",", "；", ";", "\n"):
            if separator in value and len(value) > 42:
                value = value.split(separator, 1)[0].strip()
                break
        if len(value) > 52:
            value = value[:49].rstrip() + "..."
        return value

    def _show_tgt(self, text: str, *, is_error: bool = False) -> None:
        self._last_tgt_text = text
        self._tgt_rendered_text = text
        self._tgt_rendered_is_error = is_error
        if self._tgt_text_widget:
            palette = _main_theme_palette(self._main_theme)
            text_color = palette["DANGER"] if is_error else palette["TEXT_PRIMARY"]
            self._tgt_text_widget.setPlainText(text)
            self._tgt_text_widget.setStyleSheet(
                f"QPlainTextEdit#textPane {{ color: {text_color}; }}"
            )

    def _refresh_start_button(self) -> None:
        if self._start_btn:
            self._start_btn.setEnabled(True)
            key = "stop_listening" if self._running else "start_listening"
            self._start_btn.setText(self._button_text(key))
            start_icon = ui_icon("square.svg" if self._running else "play.svg", 15, "#ffffff")
            if not start_icon.isNull():
                self._start_btn.setIcon(start_icon)
                self._start_btn.setIconSize(QSize(15, 15))
            self._start_btn.setObjectName("dangerButton" if self._running else "primaryButton")
            self._start_btn.style().unpolish(self._start_btn)
            self._start_btn.style().polish(self._start_btn)
        if getattr(self, "_quick_controls_hint", None):
            self._quick_controls_hint.setText(self._t("status_running") if self._running else self._t("status_ready"))

    def _refresh_mic_mute_button(self) -> None:
        if self._mute_btn:
            self._mute_btn.setText(self._copy("mic_mute_on" if self._mic_muted else "mic_mute_off"))
            self._mute_btn.setProperty("active", self._mic_muted)
            self._mute_btn.style().unpolish(self._mute_btn)
            self._mute_btn.style().polish(self._mute_btn)

    def _refresh_tweaks_button(self) -> None:
        if not self._tweaks_btn:
            return
        self._tweaks_btn.setText("实时")
        self._tweaks_btn.setToolTip("实时调整参数（麦克风、VAD、TTS等）")
        self._tweaks_btn.setFixedSize(HEADER_ACTION_WIDTH, 40)
        icon = ui_icon("activity.svg", 18, icon_tint(self._main_theme, strong=True))
        self._tweaks_btn.setIcon(icon)
        self._tweaks_btn.setIconSize(QSize(18, 18))
        self._tweaks_btn.style().unpolish(self._tweaks_btn)
        self._tweaks_btn.style().polish(self._tweaks_btn)

    def _refresh_theme_button(self) -> None:
        palette = _main_theme_palette(self._main_theme)
        strong_icon = icon_tint(self._main_theme, strong=True)
        muted_icon = icon_tint(self._main_theme)
        if self._theme_btn:
            theme_icon = ui_icon("sun.svg" if self._main_theme == "dark" else "moon.svg", 17, strong_icon)
            self._theme_btn.setIcon(theme_icon)
            self._theme_btn.setText("" if not theme_icon.isNull() else self._main_theme[:1].upper())
            tooltip_key = "theme_to_light" if self._main_theme == "dark" else "theme_to_dark"
            if getattr(self, "_main_theme_preference", "system") == "system":
                self._theme_btn.setToolTip(f"{self._copy('theme_follow_system')} · {self._copy(tooltip_key)}")
            else:
                self._theme_btn.setToolTip(self._copy(tooltip_key))
        if self._swap_lang_btn:
            swap_icon = ui_icon("repeat-2.svg", 16, muted_icon)
            self._swap_lang_btn.setIcon(swap_icon)
            self._swap_lang_btn.setText("" if not swap_icon.isNull() else "Swap")
            self._swap_lang_btn.setToolTip(self._copy("swap_languages"))
        if self._device_dropdown_btn:
            down_icon = ui_icon("chevron-down.svg", 15, muted_icon)
            self._device_dropdown_btn.setIcon(down_icon)
            self._device_dropdown_btn.setText("" if not down_icon.isNull() else "v")
            self._device_dropdown_btn.setToolTip(self._t("microphone"))
        if self._send_to_vrc_btn:
            send_icon = ui_icon("send.svg", 15, "#ffffff")
            if not send_icon.isNull():
                self._send_to_vrc_btn.setIcon(send_icon)
                self._send_to_vrc_btn.setIconSize(QSize(15, 15))
        self._refresh_tweaks_button()
        self._refresh_social_buttons()
        if self._status_label:
            self._status_label.setText(self._status_label.text())

    def _button_text(self, key: str) -> str:
        return self._t(key).lstrip("▶■●○• ").strip()

    def _reload_theme_style(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_app_stylesheet(self._main_theme))
        self.setStyleSheet(build_main_window_styles(self._main_theme))
        apply_window_chrome_theme(self, self._main_theme)
        self._refresh_theme_button()
        if self._status_label is not None:
            self._set_status(self._status_label.text(), getattr(self, "_status_color", "default"), key=getattr(self, "_status_key", None))
        if self._bottom_bar is not None:
            self._set_bottom(self._bottom_text, getattr(self, "_bottom_color", "default"), key=getattr(self, "_bottom_key", None))
        palette = _main_theme_palette(self._main_theme)
        if self._src_text_widget is not None:
            text_color = str(palette["TEXT_PRIMARY"] if self._src_text else palette["EDITOR_MUTED"])
            self._src_text_widget.setStyleSheet(f"QPlainTextEdit#textPane {{ color: {text_color}; }}")
        if self._tgt_text_widget is not None:
            text_color = str(palette["DANGER"] if self._tgt_rendered_is_error else palette["TEXT_PRIMARY"])
            self._tgt_text_widget.setStyleSheet(f"QPlainTextEdit#textPane {{ color: {text_color}; }}")

    def _animate_theme_refresh(self) -> None:
        self._animate_widget_theme_refresh(self.centralWidget(), duration=140, start_opacity=0.88)

    def _animate_widget_theme_refresh(
        self,
        widget: QWidget | None,
        *,
        duration: int = 120,
        start_opacity: float = 0.9,
    ) -> None:
        if widget is None or not widget.isVisible():
            return
        try:
            previous_animation = getattr(widget, "_mio_theme_fade_animation", None)
            if previous_animation is not None:
                previous_animation.stop()
            previous_widget = getattr(widget, "_mio_theme_effect_widget", None)
            if previous_widget is not None:
                previous_widget.setGraphicsEffect(None)
        except RuntimeError:
            return

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(start_opacity)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(start_opacity)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        setattr(widget, "_mio_theme_fade_animation", animation)
        setattr(widget, "_mio_theme_effect_widget", widget)

        def finish() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if getattr(widget, "_mio_theme_fade_animation", None) is animation:
                setattr(widget, "_mio_theme_fade_animation", None)
            if getattr(widget, "_mio_theme_effect_widget", None) is widget:
                setattr(widget, "_mio_theme_effect_widget", None)

        animation.finished.connect(finish)
        animation.start()

    def _refresh_child_windows(self, *, animate: bool = False) -> None:
        base_stylesheet = self._base_stylesheet()
        for window in (
            getattr(self, "_mode_wizard_dialog", None),
            getattr(self, "_guide_win", None),
            getattr(self, "_update_win", None),
            getattr(self, "_sponsor_window", None),
        ):
            self._sync_child_window_theme(window, base_stylesheet, animate=animate)
        for windows_attr in ("_audio_diagnostics_windows", "_vad_calibration_windows"):
            windows = getattr(self, windows_attr, None)
            if isinstance(windows, dict):
                for window in list(windows.values()):
                    self._sync_child_window_theme(window, base_stylesheet, animate=animate)
        if self._text_input_window is not None:
            try:
                self._text_input_window.refresh_theme()
            except Exception:
                logger.debug("Failed to update text input window theme", exc_info=True)
        if self._floating_window is not None:
            try:
                self._floating_window.refresh_theme(self._main_theme)
            except Exception:
                logger.debug("Failed to update floating window theme", exc_info=True)
        if self._tweaks_panel is not None:
            try:
                self._tweaks_panel.refresh_theme(self._main_theme)
            except Exception:
                logger.debug("Failed to update realtime tweaks panel theme", exc_info=True)
        if self._settings_window is not None:
            try:
                self._settings_window.sync_theme(self._main_theme_preference, smooth=animate)
            except Exception:
                logger.debug("Failed to update settings window theme", exc_info=True)

    def _sync_child_window_theme(self, window, stylesheet: str | None = None, *, animate: bool = False) -> None:
        if window is None:
            return
        try:
            window.setStyleSheet(stylesheet if stylesheet is not None else self._base_stylesheet())
            apply_window_chrome_theme(window, self._main_theme)
            if animate:
                self._animate_widget_theme_refresh(window, duration=120, start_opacity=0.9)
        except Exception:
            logger.debug("Failed to update child window theme", exc_info=True)

    # ----------------------------------------------------------------
    # Config save
    # ----------------------------------------------------------------
    def _schedule_config_save(self) -> None:
        if self._destroying:
            return
        timer = self._config_save_timer
        if timer.isActive():
            timer.stop()
        timer.start(CONFIG_SAVE_DEBOUNCE_MS)

    def _flush_config_save(self) -> None:
        try:
            config_manager.save_config(self._config)
        except Exception as exc:
            logger.warning("Config save failed: %s", exc)

    # ----------------------------------------------------------------
    # UI thread dispatch
    # ----------------------------------------------------------------
    def _call_in_ui(self, callback, delay_ms: int = 0) -> bool:
        if self._destroying:
            return False
        if threading.get_ident() != self._ui_thread_id:
            try:
                self._ui_callback_queue.put_nowait((delay_ms, callback))
                self.sig_ui_callback.emit()
                return True
            except Exception:
                return False
        QTimer.singleShot(delay_ms, lambda cb=callback: self._run_ui_callback(cb))
        return True

    def _run_ui_callback(self, callback) -> None:
        if self._destroying:
            return
        try:
            callback()
        except Exception:
            logger.exception("UI callback failed")

    def _start_ui_callback_drain(self) -> None:
        self._callback_drain_timer = QTimer(self)
        self._callback_drain_timer.setSingleShot(True)
        self._callback_drain_timer.timeout.connect(self._drain_ui_callback_queue)
        self.sig_ui_callback.connect(self._drain_ui_callback_queue)
        self._callback_drain_timer.start(UI_CALLBACK_DRAIN_MS)

    def _drain_ui_callback_queue(self) -> None:
        if self._destroying:
            return
        processed = 0
        while processed < UI_CALLBACK_DRAIN_LIMIT:
            try:
                delay_ms, callback = self._ui_callback_queue.get_nowait()
            except queue.Empty:
                break
            QTimer.singleShot(delay_ms, lambda cb=callback: self._run_ui_callback(cb))
            processed += 1
        # Re-arm only if queue still has items (event-driven, no constant ticking)
        if processed > 0 and not self._destroying and self._callback_drain_timer is not None:
            self._callback_drain_timer.start(UI_CALLBACK_DRAIN_MS)

    # ----------------------------------------------------------------
    # Config helpers
    # ----------------------------------------------------------------
    def _get_output_format(self) -> str:
        return self._ensure_output_dispatcher().output_format()

    def _get_output_format_2(self) -> str:
        return "disabled"

    def _output_format_uses_second_target(self) -> bool:
        return self._ensure_output_dispatcher().output_format_uses_second_target()

    def _chatbox_template_uses_second_target(self) -> bool:
        return self._ensure_output_dispatcher().chatbox_template_uses_second_target()

    def _format_chatbox_output_legacy_corrupt(self, src_text: str, tgt_text: str, tgt2_text: str = "") -> str:
        return self._format_chatbox_output(src_text, tgt_text, tgt2_text)

    def _chatbox_template(self) -> str:
        return self._ensure_output_dispatcher().chatbox_template()

    def _chatbox_template_uses_third_target(self) -> bool:
        return self._ensure_output_dispatcher().chatbox_template_uses_third_target()

    def _format_chatbox_template(
        self,
        src_text: str,
        tgt_text: str,
        tgt2_text: str = "",
        tgt3_text: str = "",
    ) -> str:
        return self._ensure_output_dispatcher().format_chatbox_template(
            src_text,
            tgt_text,
            tgt2_text,
            tgt3_text,
        )

    def _format_chatbox_output(
        self,
        src_text: str,
        tgt_text: str,
        tgt2_text: str = "",
        tgt3_text: str = "",
    ) -> str:
        return self._ensure_output_dispatcher().format_chatbox_output(
            src_text,
            tgt_text,
            tgt2_text,
            tgt3_text,
        )

    def _detect_source_lang(self, text: str) -> str:
        return detect_language(text)

    def _performance_config(self) -> dict:
        cfg = self._config.get("performance", {})
        return cfg if isinstance(cfg, dict) else {}

    def _performance_profile(self) -> str:
        profile = str(self._performance_config().get("profile", "balanced")).strip().lower()
        return profile if profile in {"balanced", "low_power"} else "balanced"

    def _startup_update_check_enabled(self) -> bool:
        return bool(self._performance_config().get("check_updates_on_start", True))

    def _startup_update_check_delay_ms(self) -> int:
        cfg = self._performance_config()
        try:
            delay = int(cfg.get("update_check_delay_ms", 3500))
        except (TypeError, ValueError):
            delay = 3500
        return max(1200, min(delay, 30000))

    def _resolve_mic_input_device_name(self, *, refresh: bool = False) -> str | None:
        audio_cfg = self._config.get("audio", {})
        mode = str(audio_cfg.get("input_device_mode", "")).strip()
        if not mode:
            mode = "fixed" if str(audio_cfg.get("input_device") or "").strip() else "auto"
        if mode == "auto":
            devices = []
            if refresh or not self._devices:
                try:
                    devices = _list_microphone_devices()
                except Exception:
                    logger.debug("Failed to enumerate microphone devices", exc_info=True)
                    devices = []
                self._devices = {
                    str(d.get("name", "")).strip(): int(d.get("index", -1))
                    for d in devices
                    if str(d.get("name", "")).strip()
                }
            if not devices and self._devices:
                devices = [
                    {"name": name, "index": index}
                    for name, index in self._devices.items()
                ]
            default_name = self._current_default_input_device_name(devices)
            if default_name:
                return default_name
            for name in self._devices:
                if name:
                    return name
            return None
        return str(audio_cfg.get("input_device") or "").strip() or None

    def _load_devices(self) -> None:
        try:
            devices = _list_microphone_devices()
        except Exception:
            logger.debug("Failed to enumerate microphone devices", exc_info=True)
            devices = []
        self._apply_loaded_devices(devices)

    def _load_devices_async(self) -> None:
        if self._devices_loading or self._destroying:
            return
        self._devices_loading = True

        def run() -> None:
            try:
                devices = _list_microphone_devices()
            except Exception:
                logger.debug("Failed to enumerate microphone devices", exc_info=True)
                devices = []
            self._call_in_ui(lambda d=devices: self._apply_loaded_devices(d))

        threading.Thread(target=run, daemon=True, name="qt-device-scan").start()

    def _load_desktop_devices(self) -> None:
        devices: dict[str, int] = {}
        for device in _list_desktop_output_devices():
            name = str(device.get("name", "")).strip()
            if name and name not in devices:
                devices[name] = int(device.get("index", -1))
        if not devices and self._desktop_devices:
            logger.warning("Desktop loopback device refresh returned empty; keeping cached devices")
            return
        self._desktop_devices = devices

    def _apply_loaded_devices(self, devices: list[dict]) -> None:
        self._devices_loading = False
        self._devices = {
            str(d.get("name", "")).strip(): int(d.get("index", -1))
            for d in devices
            if str(d.get("name", "")).strip()
        }
        self._refresh_device_combo()

    def _avatar_sync_config(self) -> dict:
        if not isinstance(getattr(self, "_config", None), dict):
            return {"enabled": False, "params": {}}
        osc_cfg = self._config.setdefault("osc", {})
        avatar_cfg = osc_cfg.setdefault("avatar_sync", {})
        avatar_cfg.setdefault("enabled", False)
        params = avatar_cfg.setdefault("params", {})
        params.setdefault("translating", "MioTranslating")
        params.setdefault("speaking", "MioSpeaking")
        params.setdefault("muted", "MioMuted")
        params.setdefault("error", "MioError")
        params.setdefault("target_language", "MioTargetLanguage")
        params.setdefault("overlay", "MioOverlayActive")
        return avatar_cfg

    def _avatar_sync_enabled(self) -> bool:
        return bool(self._avatar_sync_config().get("enabled", False))

    def _avatar_param_name(self, key: str) -> str:
        avatar_cfg = self._avatar_sync_config()
        params = avatar_cfg.get("params", {})
        if not isinstance(params, dict):
            return ""
        return str(params.get(key, "")).strip()

    def _reset_streaming_state(self, source: str | None = None) -> None:
        if source is None or source == MIC_SOURCE:
            self._mic_in_speech = False
            self._partial_generation += 1
        if source is None or source == DESKTOP_SOURCE:
            self._desktop_in_speech = False
            self._listen_in_speech = False

    def _stop_hotkeys(self) -> None:
        for hk in (self._text_input_hotkey, self._mic_mute_hotkey):
            if hk:
                try:
                    hk.stop()
                except Exception:
                    pass

    def _register_hotkeys(self) -> None:
        hotkey_cfg = self._config.setdefault("hotkeys", {})
        hotkey_cfg.setdefault("mic_mute", DEFAULT_MIC_MUTE_HOTKEY)
        text_input_cfg = self._config.setdefault("text_input_window", {})
        text_input_cfg.setdefault("hotkey", DEFAULT_TEXT_INPUT_HOTKEY)

        def on_mic_mute() -> None:
            self._call_in_ui(self._toggle_mic_mute)

        try:
            self._mic_mute_hotkey = GlobalHotkey(
                hotkey_cfg.get("mic_mute", DEFAULT_MIC_MUTE_HOTKEY),
                on_mic_mute,
                name="mic-mute",
                hotkey_id=1,
            )
            self._mic_mute_hotkey.start()
        except Exception as e:
            logger.warning("Failed to register mic mute hotkey: %s", e)

        try:
            self._text_input_hotkey = GlobalHotkey(
                text_input_cfg.get("hotkey", DEFAULT_TEXT_INPUT_HOTKEY),
                lambda: self._call_in_ui(self._open_text_input_popup),
                name="text-input",
                hotkey_id=2,
            )
            self._text_input_hotkey.start()
        except Exception as e:
            logger.warning("Failed to register text input hotkey: %s", e)

    def _on_config_saved(self) -> None:
        was_running = self._running
        if was_running:
            self._set_bottom(self._t("settings_saved_reloading"))
            self._do_stop()

        previous_lang = self._ui_lang
        self._ui_lang = get_ui_language(self._config)
        trans_cfg = self._config.get("translation", {})
        self._current_tgt_lang = str(trans_cfg.get("target_language", self._current_tgt_lang) or "ja")
        self._current_tgt_lang_2 = str(trans_cfg.get("target_language_2", self._current_tgt_lang_2) or "en")
        self._current_tgt_lang_3 = str(trans_cfg.get("target_language_3", self._current_tgt_lang_3) or "")
        src_lang = str(trans_cfg.get("source_language", "auto") or "auto")
        self._current_src_lang = None if src_lang == "auto" else src_lang
        self._current_asr_lang = self._current_src_lang if self._current_src_lang in {"zh", "yue", "ja", "en", "ko"} else None
        self._main_theme_preference = _main_theme_preference_from_config(self._config)
        self._main_theme = _resolve_main_theme(self._main_theme_preference)
        self._desktop_capture_enabled = bool(
            self._config.get("vrc_listen", {}).get("enabled", False)
        )
        self._listen_overlay_enabled = bool(
            self._config.get("vrc_listen", {}).get("show_overlay", False)
        )
        overlay_service = getattr(self, "_overlay_service", None)
        if overlay_service is not None:
            overlay_service.set_enabled(self._listen_overlay_enabled, reveal=False)
        self._mode_manager = ModeManager(
            self._config,
            virtual_device_resolver=find_best_virtual_output_device,
        )
        mode_change = self._mode_manager.apply_current_mode()
        self._sync_tts_enabled_from_config()
        central = self.centralWidget()
        if isinstance(central, BackgroundWidget):
            central.set_theme(self._main_theme)
            central.set_background_path(self._background_image_path())
        self._translator = None
        manual_controller = getattr(self, "_manual_translation_controller", None)
        if manual_controller is not None:
            manual_controller.translator = None
        self._asr = None
        self._listen_asr = None
        self._refresh_asr_transcribe_locks()
        self._reset_translation_failure_backoff()
        self._reset_tts_manager()
        self._close_osc_sender()
        self._apply_osc_listener_config()
        self._reload_theme_style()
        if self._text_input_window is not None:
            try:
                self._text_input_window.refresh_theme()
            except Exception:
                logger.debug("Failed to update text input window theme", exc_info=True)
        if self._floating_window is not None:
            try:
                self._floating_window.refresh_theme(self._main_theme)
            except Exception:
                logger.debug("Failed to update floating window theme", exc_info=True)
        self._refresh_static_texts()
        self._load_devices_async()
        self._sync_settings_window_vrc_listen_state()
        self._schedule_settings_preload(500)
        if mode_change.changed:
            self._set_bottom(
                self._copy(
                    "mode_switched_simultaneous"
                    if self._mode_manager.mode is AppMode.SIMULTANEOUS
                    else "mode_switched_translation"
                )
            )
        if previous_lang != self._ui_lang and self._floating_window is not None:
            try:
                self._floating_window.update_language(self._ui_lang)
            except Exception:
                logger.debug("Failed to update floating window language", exc_info=True)
        if previous_lang != self._ui_lang and self._text_input_window is not None:
            try:
                self._text_input_window.update_language(self._ui_lang)
            except Exception:
                logger.debug("Failed to update text input window language", exc_info=True)
        self._stop_hotkeys()
        self._register_hotkeys()
        if was_running:
            QTimer.singleShot(100, self._do_start)

    def _background_image_path(self) -> str:
        ui_cfg = self._config.get("ui")
        if not isinstance(ui_cfg, dict):
            return ""
        value = ui_cfg.get("background_image_path")
        return value if isinstance(value, str) else ""

    def _format_translation_error(self, error: object):
        config = getattr(self, "_config", {}) or {}
        trans_cfg = config.get("translation", {}) if isinstance(config, dict) else {}
        backend = trans_cfg.get("backend")
        ui_language = getattr(self, "_ui_lang", "zh_CN")
        return format_translation_error(error, backend=backend, ui_language=ui_language)

    def _reset_translation_failure_backoff(self) -> None:
        with self._translation_state_lock:
            self._translation_failure_streak = 0
            self._translation_cooldown_until = 0.0
            self._translation_cooldown_category = None

    def _translation_cooldown_remaining(self) -> float:
        with self._translation_state_lock:
            return max(0.0, self._translation_cooldown_until - time.monotonic())

    def _translation_cooldown_active(self, source: str) -> bool:
        remaining = self._translation_cooldown_remaining()
        if remaining <= 0:
            return False
        logger.debug(
            "Skipping translation while cooldown is active (source=%s remaining_s=%.1f)",
            source,
            remaining,
        )
        return True

    def _record_translation_success(self) -> None:
        self._reset_translation_failure_backoff()

    def _record_translation_failure(self, friendly) -> float:
        base_cooldown = TRANSLATION_FAILURE_COOLDOWN_S.get(friendly.category, 0.0)
        if base_cooldown <= 0:
            return 0.0
        now = time.monotonic()
        with self._translation_state_lock:
            if now > self._translation_cooldown_until + TRANSLATION_FAILURE_MAX_COOLDOWN_S:
                self._translation_failure_streak = 0
            self._translation_failure_streak += 1
            multiplier = 2 ** min(self._translation_failure_streak - 1, 3)
            cooldown_s = min(base_cooldown * multiplier, TRANSLATION_FAILURE_MAX_COOLDOWN_S)
            self._translation_cooldown_until = now + cooldown_s
            self._translation_cooldown_category = friendly.category
            return cooldown_s

    # ----------------------------------------------------------------
    # Card / shadow utilities
    # ----------------------------------------------------------------
    def _base_stylesheet(self) -> str:
        return build_main_window_styles(self._main_theme)

    def _show_bottom_progress(self, progress: float | None, *, indeterminate: bool) -> None:
        self._bottom_progress_visible = True
        if progress is not None:
            self._bottom_progress_value = max(0.0, min(1.0, float(progress)))
        if self._bottom_progress:
            if indeterminate:
                self._bottom_progress.setRange(0, 0)
            else:
                self._bottom_progress.setRange(0, 100)
                self._bottom_progress.setValue(int(self._bottom_progress_value * 100))
            self._bottom_progress.show()

    def _hide_bottom_progress(self) -> None:
        self._bottom_progress_visible = False
        if self._bottom_progress:
            self._bottom_progress.hide()

    def _card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("contentCard")
        card.setFrameShape(QFrame.Shape.NoFrame)
        return card

    def _apply_shadow(self, widget: QWidget, *, blur: int, alpha: int, y_offset: int) -> None:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, y_offset)
        shadow.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(shadow)

class _StartupCancelled(Exception):
    pass


class _StartupConfigurationError(Exception):
    pass


class _LangVar:
    def __init__(self) -> None:
        self._value = ""

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
