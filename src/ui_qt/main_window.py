from __future__ import annotations

import copy
import hashlib
import json
import logging
import queue
import re
import threading
import time
import unicodedata
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPixmap
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
    QVBoxLayout,
    QWidget,
)

from src.asr.errors import ASRMissingAPIKeyError, ASRTemporaryUnavailableError
from src.asr.model_registry import ASR_ENGINE_FOLLOW_MAIN, LISTEN_SELECTABLE_ASR_ENGINES, get_asr_runtime_spec, normalize_asr_engine
from src.core.manual_translation_controller import ManualTranslationController, ManualTranslationRequest
from src.core.mode_manager import AppMode, ModeManager
from src.core.output_dispatcher import OutputDispatcher, OutputMessage
from src.core.overlay_service import OverlayService
from src.core.realtime_pipelines import ListenPipeline, MicPipeline, RealtimeTranslationResult
from src.core.realtime_scheduler import (
    AdmissionStatus,
    RealtimeCompletion,
    RealtimeScheduler,
    RealtimeTask,
    RealtimeTranslationQueueExpiredError,
)
from src.core.rewrite_coordinator import (
    REWRITE_PRIORITY_REALTIME,
    RewriteCallCoordinator,
    provider_runtime_config_signature,
)
from src.translators.base import (
    TranslationContextStore,
    provider_background_work_in_progress,
    translation_context_scope,
)
from src.translators.asr_rewriter import (
    ASR_REWRITE_DISABLED,
    normalize_asr_rewrite_style,
)
from src.ui_qt.font_config import apply_application_font
from src.ui_qt.credential_prompt import show_missing_credential_prompt
from src.ui_qt.qt_localization import install_qt_translations
from src.ui_qt.icon_utils import ui_icon
from src.ui_qt.styles import build_app_stylesheet, build_main_window_styles
from src.ui_qt.theme import MAIN_THEME_CONFIG_KEY, icon_tint, normalize_theme, normalize_theme_preference, resolve_theme, theme_preference_from_config, theme_tokens
from src.ui_qt.window_utils import apply_window_chrome_theme, play_theme_fade
from src.ui_qt.widgets import NoWheelComboBox
from src.ui_qt.realtime_tweaks_panel import RealtimeTweaksPanel
from src.ui_qt.state_manager import AppState
from src.tts.error_utils import tts_error_code
from src.utils import config_manager
from src.utils.app_paths import resource_base_dirs
from src.utils.credential_validation import first_missing_required_credential
from src.utils.global_hotkey import GlobalHotkey, DEFAULT_MIC_MUTE_HOTKEY, DEFAULT_TEXT_INPUT_HOTKEY
from src.utils.i18n import tr
from src.utils.lang_detect import detect_language
from src.utils.latency_metrics import translation_metrics_snapshot
from src.utils.localization import (
    format_locale_number,
    format_locale_percent,
    normalize_ui_language,
    translate_key_catalog,
)
from src.utils.provider_diagnostics import safe_exception_summary
from src.utils.startup_timing import record_startup_stage
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import (
    UI_LANGUAGE_OPTIONS,
    get_backend_value,
    get_manual_source_language_options,
    get_target_language_options,
    get_ui_language,
    normalize_backend,
    normalize_output_format,
    ORIGINAL_ONLY_READ_TRANSLATION_OSC_DELAY_MS,
    ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
    OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION,
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
FINAL_TASK_QUEUE_MAXSIZE = 8
DESKTOP_FINAL_TASK_QUEUE_MAXSIZE = 8
TRANSLATION_TASK_QUEUE_MAXSIZE = 12
ASR_REWRITE_TASK_QUEUE_MAXSIZE = 4
ASR_WORKER_CONCURRENCY = 2
ASR_REWRITE_WORKER_CONCURRENCY = 2
TRANSLATION_WORKER_CONCURRENCY = 2
MAX_REALTIME_ASR_QUEUE_AGE_S = 5.0
MAX_REVERSE_ASR_QUEUE_AGE_S = 2.5
MAX_REVERSE_TRANSLATION_QUEUE_AGE_S = 2.5
MIC_PRIORITY_BURST = 3
DEFAULT_REALTIME_TRANSLATION_TIMEOUT_S = 6.0
DEFAULT_REVERSE_TRANSLATION_TIMEOUT_S = 4.0
DEFAULT_REVERSE_ASR_TIMEOUT_S = 5.0
WORKER_STOP_TIMEOUT_S = 5.0
# An update must not hand a visible installer a process that still owns models,
# audio devices, or application files.  Unlike normal Stop, Install Now waits
# for the bounded cleanup path and leaves Mio open if it cannot finish.
UPDATE_INSTALL_QUIESCE_TIMEOUT_S = 15.0
CONFIG_SAVE_DEBOUNCE_MS = 280
MAIN_WINDOW_DEFAULT_SIZE = (940, 440)
MAIN_WINDOW_MIN_SIZE = (900, 430)
HEADER_ACTION_WIDTH = 104
CHATBOX_CHAR_LIMIT = 144
HEADER_HEIGHT = 74
LANG_COMBO_SOURCE_WIDTH = 128
LANG_COMBO_TARGET_WIDTH = 128
LANG_ROW_BUTTON_SIZE = 28
LANG_FLOW_MAX_HEIGHT = 50
TEXT_PANE_MIN_HEIGHT = 88
TEXT_PANEL_MAX_HEIGHT = 144
ACTION_STRIP_MAX_HEIGHT = 80
ACTION_BUTTON_HEIGHT = 32
SIDE_PRIMARY_BUTTON_HEIGHT = 32
SIDE_CONTROL_BUTTON_HEIGHT = 32
FOOTER_HEIGHT = 48
FOOTER_BUTTON_SIZE = 36
FOOTER_SPONSOR_BUTTON_WIDTH = 140
FOOTER_ICON_SIZE = 20
FOOTER_SPONSOR_ICON_SIZE = 15
BASE_DPI = 96.0
MIN_MAIN_UI_SCALE = 0.9
MAX_MAIN_UI_SCALE = 1.35
UI_CALLBACK_DRAIN_MS = 25
UI_CALLBACK_DRAIN_LIMIT = 128
UI_CALLBACK_QUEUE_MAXSIZE = 256
UI_PRIORITY_CALLBACK_QUEUE_MAXSIZE = 4
UI_DELIVERY_ACK_POLL_S = 0.05
_TTS_REQUEST_SCOPED_CONFIG_KEYS = frozenset({"voice", "rate", "volume"})
_ONLINE_TTS_PREWARM_ENGINES = frozenset(
    {
        "edge",
        "gtts",
        "google",
        "voicevox",
        "aivis",
        "aivis_speech",
        "mimo",
        "mimo_tts",
        "xiaomi_tts",
        "qwen_tts",
        "qwen3_tts",
        "qwen-tts",
    }
)
_ONLINE_ASR_PREWARM_ENGINES = frozenset(
    {
        "gemini-live",
        "qwen3-asr",
        "webspeech",
    }
)
_TTS_BACKGROUND_PREWARM_DELAY_MS = 750
_TTS_VIRTUAL_OUTPUT_WAIT_RETRIES = 12
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
MIC_DIAGNOSTIC_LOG_INTERVAL_S = 60.0
MIC_DIGITAL_SILENCE_RECOVERY_S = 60.0
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

MAIN_COPY = {
    "creator_banner_compact": {
        "zh-CN": "天川 澪 | 免费版 | GPL",
        "en": "天川 澪 | Free build | GPL",
        "ja": "天川 澪 | 無料版 | GPL",
        "ru": "天川 澪 | Бесплатная сборка | GPL",
        "ko": "天川 澪 | 무료 빌드 | GPL",
    },
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
    "mic_device_auto_option": {
        "zh-CN": "自动：活动设备 / 系统默认",
        "en": "Auto: Active / System Default",
        "ja": "自動: 使用中 / システム既定",
        "ru": "Авто: активный / системный",
        "ko": "자동: 사용 중 / 시스템 기본",
    },
    "input_device_missing": {
        "zh-CN": "未检测到麦克风",
        "en": "No microphone found",
        "ja": "マイクが見つかりません",
        "ru": "Микрофон не найден",
        "ko": "마이크를 찾을 수 없음",
    },
    "qwen_tts_auth_error": {
        "zh-CN": "Qwen TTS 凭据被拒绝，请检查 API Key 和服务区域",
        "en": "Qwen TTS rejected the credential; check the API key and service region",
        "ja": "Qwen TTS の認証が拒否されました。API Key とサービス地域を確認してください",
        "ru": "Qwen TTS отклонил учетные данные; проверьте API-ключ и регион сервиса",
        "ko": "Qwen TTS 인증이 거부되었습니다. API Key와 서비스 지역을 확인하세요",
    },
    "qwen_tts_network_error": {
        "zh-CN": "Qwen TTS 网络连接中断，请检查网络或代理设置后重试",
        "en": "Qwen TTS network connection was interrupted; check the network or proxy settings and try again",
        "ja": "Qwen TTS のネットワーク接続が中断されました。ネットワークまたはプロキシ設定を確認して再試行してください",
        "ru": "Сетевое соединение Qwen TTS прервано; проверьте сеть или настройки прокси и повторите попытку",
        "ko": "Qwen TTS 네트워크 연결이 중단되었습니다. 네트워크 또는 프록시 설정을 확인한 뒤 다시 시도하세요",
    },
    "qwen_tts_timeout_error": {
        "zh-CN": "Qwen TTS 请求超时，已释放任务，请稍后重试",
        "en": "Qwen TTS timed out and released the task; try again shortly",
        "ja": "Qwen TTS がタイムアウトしたためタスクを解放しました。しばらくしてから再試行してください",
        "ru": "Истекло время ожидания Qwen TTS, задача освобождена; повторите попытку позже",
        "ko": "Qwen TTS 요청 시간이 초과되어 작업을 해제했습니다. 잠시 후 다시 시도하세요",
    },
    "qwen_tts_rate_limit_error": {
        "zh-CN": "Qwen TTS 请求过于频繁，请稍候再试",
        "en": "Qwen TTS is rate-limited; wait briefly and try again",
        "ja": "Qwen TTS の利用制限に達しました。しばらく待ってから再試行してください",
        "ru": "Достигнут лимит запросов Qwen TTS; немного подождите и повторите попытку",
        "ko": "Qwen TTS 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요",
    },
    "qwen_tts_model_error": {
        "zh-CN": "Qwen TTS 不支持当前模型，请检查模型与服务区域",
        "en": "Qwen TTS does not support the selected model; check the model and service region",
        "ja": "Qwen TTS は選択したモデルに対応していません。モデルとサービス地域を確認してください",
        "ru": "Qwen TTS не поддерживает выбранную модель; проверьте модель и регион сервиса",
        "ko": "Qwen TTS가 선택한 모델을 지원하지 않습니다. 모델과 서비스 지역을 확인하세요",
    },
    "qwen_tts_endpoint_error": {
        "zh-CN": "Qwen TTS 服务地址无效，请检查区域与 API 地址",
        "en": "The Qwen TTS endpoint is invalid; check the region and API URL",
        "ja": "Qwen TTS の接続先が無効です。地域と API URL を確認してください",
        "ru": "Недопустимый адрес Qwen TTS; проверьте регион и URL API",
        "ko": "Qwen TTS 엔드포인트가 올바르지 않습니다. 지역과 API URL을 확인하세요",
    },
    "qwen_tts_busy_error": {
        "zh-CN": "Qwen TTS 队列繁忙或已暂时恢复保护，请稍候再试",
        "en": "Qwen TTS is busy or temporarily recovering; wait briefly and try again",
        "ja": "Qwen TTS が混雑中または一時復旧中です。しばらく待ってから再試行してください",
        "ru": "Qwen TTS занят или временно восстанавливается; немного подождите и повторите попытку",
        "ko": "Qwen TTS가 혼잡하거나 일시적으로 복구 중입니다. 잠시 후 다시 시도하세요",
    },
    "qwen_tts_playback_error": {
        "zh-CN": "Qwen TTS 音频播放失败，请检查输出设备后重试",
        "en": "Qwen TTS audio playback failed; check the output device and try again",
        "ja": "Qwen TTS の音声再生に失敗しました。出力デバイスを確認して再試行してください",
        "ru": "Не удалось воспроизвести звук Qwen TTS; проверьте устройство вывода и повторите попытку",
        "ko": "Qwen TTS 오디오 재생에 실패했습니다. 출력 장치를 확인한 뒤 다시 시도하세요",
    },
    "qwen_tts_provider_error": {
        "zh-CN": "Qwen TTS 服务暂时失败，任务已释放，请稍后重试",
        "en": "Qwen TTS temporarily failed and released the task; try again shortly",
        "ja": "Qwen TTS サービスで一時的な障害が発生し、タスクを解放しました。しばらくしてから再試行してください",
        "ru": "Временный сбой Qwen TTS, задача освобождена; повторите попытку позже",
        "ko": "Qwen TTS 서비스에 일시적인 오류가 발생해 작업을 해제했습니다. 잠시 후 다시 시도하세요",
    },
    "qwen_tts_input_error": {
        "zh-CN": "Qwen TTS 无法处理当前文本，请修改内容后重试",
        "en": "Qwen TTS could not accept the current text; edit it and try again",
        "ja": "Qwen TTS が現在のテキストを受け付けませんでした。内容を修正して再試行してください",
        "ru": "Qwen TTS не принял текущий текст; измените его и повторите попытку",
        "ko": "Qwen TTS가 현재 텍스트를 처리할 수 없습니다. 내용을 수정한 뒤 다시 시도하세요",
    },
    "qwen_tts_configuration_error": {
        "zh-CN": "Qwen TTS 凭据配置不可用，请重新保存 API Key 并检查服务区域",
        "en": "The Qwen TTS credential configuration is unusable; save the API key again and check the service region",
        "ja": "Qwen TTS の認証設定を使用できません。API Key を再保存し、サービス地域を確認してください",
        "ru": "Настройки учетных данных Qwen TTS непригодны; снова сохраните API-ключ и проверьте регион сервиса",
        "ko": "Qwen TTS 인증 설정을 사용할 수 없습니다. API Key를 다시 저장하고 서비스 지역을 확인하세요",
    },
    "qwen_tts_safety_error": {
        "zh-CN": "Qwen TTS 因内容安全检查未合成此文本，请修改内容后重试",
        "en": "Qwen TTS did not synthesize this text because of a content-safety check; edit it and try again",
        "ja": "Qwen TTS はコンテンツ安全性チェックによりこのテキストを合成しませんでした。内容を修正して再試行してください",
        "ru": "Qwen TTS не синтезировал этот текст из-за проверки безопасности содержимого; измените текст и повторите попытку",
        "ko": "Qwen TTS가 콘텐츠 안전성 검사로 이 텍스트를 합성하지 않았습니다. 내용을 수정한 뒤 다시 시도하세요",
    },
    "qwen_tts_unavailable_error": {
        "zh-CN": "Qwen TTS 当前不可用，请检查凭据、模型和服务区域",
        "en": "Qwen TTS is unavailable; check the credential, model, and service region",
        "ja": "Qwen TTS を利用できません。認証情報、モデル、サービス地域を確認してください",
        "ru": "Qwen TTS недоступен; проверьте учетные данные, модель и регион сервиса",
        "ko": "Qwen TTS를 사용할 수 없습니다. 인증 정보, 모델, 서비스 지역을 확인하세요",
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
    "realtime_queue_full": {
        "zh-CN": "语音处理任务已满，请稍等片刻",
        "en": "Speech processing is at capacity; please pause briefly",
        "ja": "音声処理が混み合っています。少し待ってから話してください",
        "ru": "Обработка речи перегружена; сделайте короткую паузу",
        "ko": "음성 처리 대기열이 가득 찼습니다. 잠시 후 다시 말해 주세요",
    },
    "asr_temporary_failure": {
        "zh-CN": "语音识别暂时失败，连接正在恢复；请再说一次",
        "en": "Speech recognition temporarily failed and is recovering; please try that sentence again",
        "ja": "音声認識が一時的に失敗しました。接続を復旧中です。もう一度話してください",
        "ru": "Распознавание речи временно недоступно и восстанавливается; повторите фразу",
        "ko": "음성 인식이 일시적으로 실패해 연결을 복구 중입니다. 문장을 다시 말해 주세요",
    },
    "asr_queue_expired": {
        "zh-CN": "语音在识别队列中等待过久，请再说一次",
        "en": "Speech waited too long in the recognition queue; please repeat that sentence",
        "ja": "音声認識の待ち時間が長すぎました。もう一度話してください",
        "ru": "Фраза слишком долго ожидала распознавания; повторите её",
        "ko": "음성이 인식 대기열에서 너무 오래 기다렸습니다. 다시 말해 주세요",
    },
    "translation_queue_expired": {
        "zh-CN": "逆向翻译等待过久，已丢弃该句以恢复实时处理",
        "en": "Reverse translation waited too long and was discarded so realtime processing can recover",
        "ja": "逆翻訳の待機時間が長すぎたため、リアルタイム処理を復旧するためにこの文を破棄しました",
        "ru": "Обратный перевод ждал слишком долго; фраза отброшена для восстановления обработки в реальном времени",
        "ko": "역번역 대기 시간이 너무 길어 실시간 처리를 복구하기 위해 해당 문장을 폐기했습니다",
    },
    "asr_credential_failure": {
        "zh-CN": "语音识别凭据不可用，请在设置中检查对应的 API Key",
        "en": "The speech-recognition credential is unavailable; check its API key in Settings",
        "ja": "音声認識の認証情報を使用できません。設定で該当する API Key を確認してください",
        "ru": "Учётные данные распознавания речи недоступны; проверьте API-ключ в настройках",
        "ko": "음성 인식 인증 정보를 사용할 수 없습니다. 설정에서 해당 API 키를 확인해 주세요",
    },
    "update_badge": {
        "zh-CN": "新版本",
        "en": "Update",
        "ja": "更新",
        "ru": "Обновление",
        "ko": "업데이트",
    },
    "mode_translation": {
        "zh-CN": "文本",
        "en": "Text",
        "ja": "テキスト",
        "ru": "Текст",
        "ko": "텍스트",
    },
    "source_lang_short": {
        "zh-CN": "源语言",
        "en": "Src",
        "ja": "元言語",
        "ru": "Исх.",
        "ko": "원문 언어",
    },
    "translation_lang_1_short": {
        "zh-CN": "译文 1",
        "en": "TL 1",
        "ja": "翻訳 1",
        "ru": "Перевод 1",
        "ko": "번역 1",
    },
    "translation_lang_2_short": {
        "zh-CN": "译文 2",
        "en": "TL 2",
        "ja": "翻訳 2",
        "ru": "Перевод 2",
        "ko": "번역 2",
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


def create_translator(
    config: dict,
    *,
    context_store: TranslationContextStore | None = None,
):
    from src.translators.factory import create_translator as _create_translator

    return _create_translator(config, context_store=context_store)


def default_output_device_name() -> str | None:
    from src.audio.desktop_recorder import default_output_device_name as _default_output_device_name

    return _default_output_device_name()


def _list_desktop_output_devices(*, force_refresh: bool = False) -> list[dict]:
    from src.audio.desktop_recorder import list_output_devices as _list_out
    return _list_out(force_refresh=force_refresh)


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
    from src.audio.device_inventory import list_input_devices

    return list_input_devices(force_refresh=True)


def inventory_default_input_device_name(*, force_refresh: bool = False) -> str | None:
    from src.audio.device_inventory import default_input_device_name

    return default_input_device_name(force_refresh=force_refresh)


def audio_device_names_match(left: object, right: object) -> bool:
    from src.audio.device_inventory import device_names_match

    return device_names_match(left, right)


def unique_device_name_match(target: object, candidates: object) -> str | None:
    from src.audio.device_inventory import unique_device_name_match as _unique_device_name_match

    return _unique_device_name_match(target, candidates)


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


def _asr_pair_config_signature(config: Mapping[str, Any] | object) -> str:
    """Return a credential-safe digest for retained ASR provider ownership."""

    asr_cfg: object = {}
    listen_cfg: object = {}
    ui_language = ""
    if isinstance(config, Mapping):
        candidate = config.get("asr", {})
        asr_cfg = candidate if isinstance(candidate, Mapping) else {}
        candidate = config.get("vrc_listen", {})
        listen_cfg = candidate if isinstance(candidate, Mapping) else {}
        try:
            ui_language = get_ui_language(dict(config))
        except Exception:
            ui_language = ""
    payload = {
        "asr": asr_cfg,
        "vrc_listen": listen_cfg,
        "ui_language": ui_language,
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        ).encode("utf-8", errors="backslashreplace")
    except Exception:
        serialized = repr(payload).encode("utf-8", errors="backslashreplace")
    return hashlib.sha256(serialized).hexdigest()


def _listen_asr_provider_config(config: dict, listen_engine: str) -> dict:
    if listen_engine != "qwen3-asr":
        return config
    listen_config = copy.deepcopy(config)
    listen_cfg = listen_config.get("vrc_listen", {})
    if not isinstance(listen_cfg, Mapping):
        listen_cfg = {}
    try:
        reverse_timeout = float(
            listen_cfg.get(
                "asr_timeout_s",
                DEFAULT_REVERSE_ASR_TIMEOUT_S,
            )
        )
    except (TypeError, ValueError):
        reverse_timeout = DEFAULT_REVERSE_ASR_TIMEOUT_S
    reverse_timeout = max(2.0, min(reverse_timeout, 12.0))
    qwen_cfg = listen_config.setdefault("asr", {}).setdefault(
        "qwen3_asr", {}
    )
    if isinstance(qwen_cfg, dict):
        qwen_cfg["hard_timeout_seconds"] = reverse_timeout
        qwen_cfg["max_retries"] = 0
    return listen_config


def _close_provider_quietly(provider: Any, *, label: str) -> None:
    close = getattr(provider, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        logger.debug(
            "Failed to close %s: %s",
            label,
            safe_exception_summary(exc),
        )


def _create_asr_pair(
    config: dict,
    *,
    prewarmed_main: Any = None,
    prewarmed_listen: Any = None,
):
    main_engine = _main_asr_engine(config)
    listen_engine = _listen_asr_engine(config)
    try:
        main_asr = (
            prewarmed_main
            if prewarmed_main is not None
            else create_asr(config, engine=main_engine)
        )
    except Exception:
        if prewarmed_listen is not prewarmed_main:
            _close_provider_quietly(
                prewarmed_listen,
                label="orphaned prewarmed listen ASR provider",
            )
        raise
    vrc_cfg = config.get("vrc_listen", {}) if isinstance(config, dict) else {}
    if not isinstance(vrc_cfg, dict) or not bool(vrc_cfg.get("enabled", False)):
        # Do not load a second heavyweight model merely to keep a disabled
        # desktop-listen feature ready. Enabling a distinct listen engine while
        # running performs a controlled pipeline restart below.
        if prewarmed_listen is not None and prewarmed_listen is not main_asr:
            _close_provider_quietly(
                prewarmed_listen,
                label="unused prewarmed listen ASR provider",
            )
        return main_asr, main_asr
    matching_runtime = (
        _listen_asr_reuses_main(config)
        and _asr_runtime_signature(config, listen_engine)
        == _asr_runtime_signature(config, main_engine)
    )
    # Qwen reverse recognition gets its own persistent runtime. Sharing the
    # same semaphore/HTTP client with microphone recognition serialized both
    # directions and let a slow mic request block every reverse sentence.
    isolate_qwen_listen = bool(matching_runtime and listen_engine == "qwen3-asr")
    if matching_runtime and not isolate_qwen_listen:
        if prewarmed_listen is not None and prewarmed_listen is not main_asr:
            _close_provider_quietly(
                prewarmed_listen,
                label="redundant prewarmed listen ASR provider",
            )
        return main_asr, main_asr
    listen_config = _listen_asr_provider_config(config, listen_engine)
    try:
        listen_asr = (
            prewarmed_listen
            if prewarmed_listen is not None
            else create_asr(
                listen_config,
                engine=listen_engine,
            )
        )
    except Exception:
        _close_provider_quietly(
            main_asr,
            label="partially constructed main ASR provider",
        )
        raise
    return main_asr, listen_asr


# ----------------------------------------------------------------
# BackgroundWidget
# ----------------------------------------------------------------
def _decode_background_image(path: Path) -> QImage:
    """Read and decode a configured background away from the Qt UI thread."""

    try:
        if not path.is_file():
            return QImage()
        return QImage(str(path))
    except (OSError, RuntimeError):
        return QImage()


class BackgroundWidget(QWidget):
    _background_image_decoded = Signal(int, str, object)

    def __init__(self, background_path: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._resize_timer: QTimer | None = None
        self._theme = "dark"
        self._background_decode_generation = 0
        self._requested_background_path = ""
        self._background_image_decoded.connect(self._apply_decoded_background_image)
        self.setAutoFillBackground(False)
        self.set_background_path(background_path)

    def set_theme(self, theme: str) -> None:
        self._theme = _normalize_main_theme(theme)
        self.update()

    def set_background_path(self, background_path: str) -> None:
        path = Path(background_path).expanduser() if background_path else None
        self._background_decode_generation += 1
        generation = self._background_decode_generation
        requested_path = str(path) if path is not None else ""
        self._requested_background_path = requested_path

        if path is None:
            self._pixmap = QPixmap()
            self.update()
            return

        widget_ref = weakref.ref(self)

        def decode() -> None:
            image = _decode_background_image(path)
            widget = widget_ref()
            if widget is None:
                return
            try:
                widget._background_image_decoded.emit(
                    generation,
                    requested_path,
                    image,
                )
            except RuntimeError:
                # The QWidget may have been destroyed while file I/O was in flight.
                return

        threading.Thread(
            target=decode,
            daemon=True,
            name=f"qt-background-decode-{generation}",
        ).start()

    def _apply_decoded_background_image(
        self,
        generation: int,
        requested_path: str,
        image: object,
    ) -> None:
        if (
            generation != self._background_decode_generation
            or requested_path != self._requested_background_path
        ):
            return
        if isinstance(image, QImage) and not image.isNull():
            self._pixmap = QPixmap.fromImage(image)
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


@dataclass(frozen=True, slots=True)
class _RealtimeAudioPayload:
    audio: Any
    asr_provider: Any
    asr_language: str | None
    source_language: str | None
    target_language: str
    second_target_language: str
    third_target_language: str
    listen_target_language: str
    listen_prefix: str
    send_to_chatbox: bool
    config_snapshot: Mapping[str, Any]
    source_generation: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _RealtimeTranslationWorkerState:
    """Worker-confined translator state; never shared across translation threads."""

    translator: Any = None
    listen_translator: Any = None
    config_snapshot: Mapping[str, Any] | None = None
    runtime_signature: str | None = None
    config: dict[str, Any] | None = None
    dispatcher: OutputDispatcher | None = None
    mic_pipeline: MicPipeline | None = None
    listen_pipeline: ListenPipeline | None = None

    def close_translator(
        self,
        source: str | None = None,
        *,
        close_client: bool = True,
    ) -> None:
        if source == DESKTOP_SOURCE:
            translators = (self.listen_translator,)
            self.listen_translator = None
        elif source == MIC_SOURCE:
            translators = (self.translator,)
            self.translator = None
        else:
            translators = (self.translator, self.listen_translator)
            self.translator = None
            self.listen_translator = None
        closed: list[Any] = []
        for translator in translators:
            if translator is None or any(translator is item for item in closed):
                continue
            closed.append(translator)
            if not close_client:
                continue
            close = getattr(translator, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:
                logger.debug(
                    "Failed to close realtime translation worker client: %s",
                    safe_exception_summary(exc),
                )

    def close(self) -> None:
        self.close_translator()
        self.config_snapshot = None
        self.runtime_signature = None
        self.config = None
        self.dispatcher = None
        self.mic_pipeline = None
        self.listen_pipeline = None


@dataclass(slots=True)
class _RealtimeRewriteWorkerState:
    """Worker-confined generative client for the optional rewrite stage."""

    translator: Any = None
    config_snapshot: Mapping[str, Any] | None = None
    runtime_signature: str | None = None

    def close(self) -> None:
        translator = self.translator
        self.translator = None
        self.config_snapshot = None
        self.runtime_signature = None
        close = getattr(translator, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                logger.debug(
                    "Failed to close realtime ASR rewrite worker client: %s",
                    safe_exception_summary(exc),
                )


def _translator_cleanup_is_supervised(
    translator: Any,
    metrics: Mapping[str, Any] | None = None,
) -> bool:
    """Return true when timeout cleanup already owns destructive close."""

    if translator is None:
        return False
    retired = getattr(translator, "_pending_requests_retired", None)
    if callable(retired):
        try:
            if bool(retired()):
                return True
        except Exception:
            pass
    return bool(isinstance(metrics, Mapping) and metrics.get("wall_timeout_triggered"))


def _freeze_snapshot_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_snapshot_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_snapshot_value(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _thaw_snapshot_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_snapshot_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_snapshot_value(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw_snapshot_value(item) for item in value]
    return value


def _immutable_audio_snapshot(audio: Any) -> Any:
    """Copy recorder-owned buffers so capture can immediately reuse its storage."""

    try:
        import numpy as np

        if isinstance(audio, np.ndarray):
            snapshot = np.array(audio, copy=True, order="C")
            snapshot.setflags(write=False)
            return snapshot
    except Exception:
        logger.debug("Unable to create NumPy audio snapshot", exc_info=True)
    if isinstance(audio, memoryview):
        return audio.tobytes()
    if isinstance(audio, bytearray):
        return bytes(audio)
    if isinstance(audio, (bytes, str, int, float, type(None))):
        return audio
    try:
        return copy.deepcopy(audio)
    except Exception:
        logger.debug("Unable to deep-copy audio payload; using original object", exc_info=True)
        return audio


# ----------------------------------------------------------------
# Qt MainWindow with real backend wiring
# ----------------------------------------------------------------
class MainWindow(QMainWindow):
    sig_status = Signal(str)
    sig_bottom = Signal(str)
    sig_ui_callback = Signal()

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._startup_constructed_at = time.perf_counter()
        self._config = config
        self._destroying = False
        self._post_show_initialization_scheduled = False
        self._post_show_initialization_started = False
        self._first_paint_logged = False
        self._virtual_output_resolution_in_progress = False
        self._background_initialization_started = False
        self._asr_prewarm_lock = threading.RLock()
        self._asr_prewarm_cancel_event = threading.Event()
        self._asr_prewarm_thread: threading.Thread | None = None
        self._prewarmed_asr_signature: str | None = None
        self._prewarmed_main_asr = None
        self._prewarmed_listen_asr = None
        # Translation clients are prepared before a realtime session starts.
        # Requests-based providers keep their warmed session associated with the
        # preparation thread; the finished thread can safely hand that client to
        # its eventual realtime worker through the provider's session pool.
        self._translation_prewarm_lock = threading.RLock()
        self._translation_prewarm_cancel_event = threading.Event()
        self._translation_prewarm_thread: threading.Thread | None = None
        self._translation_prewarm_threads: set[threading.Thread] = set()
        self._translation_prewarm_cancel_events: set[threading.Event] = set()
        self._translation_prewarm_signature: str | None = None
        self._prewarmed_realtime_translators: dict[str, list[Any]] = {
            MIC_SOURCE: [],
            DESKTOP_SOURCE: [],
        }
        self._tts_prewarm_lock = threading.RLock()
        self._tts_prewarm_cancel_event = threading.Event()
        self._tts_prewarm_thread: threading.Thread | None = None
        self._tts_prewarm_signature: tuple | None = None
        self._tts_manager_lock = threading.RLock()
        self._ui_thread_id = threading.get_ident()
        self._ui_callback_queue: queue.Queue[tuple[int, object]] = queue.Queue(
            maxsize=UI_CALLBACK_QUEUE_MAXSIZE
        )
        self._ui_priority_callback_queue: queue.Queue[tuple[int, object]] = (
            queue.Queue(maxsize=UI_PRIORITY_CALLBACK_QUEUE_MAXSIZE)
        )
        self._ui_callback_drop_count = 0
        self._realtime_delivery_cancel_event = threading.Event()

        # Shared state for the realtime tweaks panel.
        self._state = AppState()

        # --- Runtime state ---
        self._running = False
        self._listen_session = 0
        self._startup_cancel_event = threading.Event()
        self._startup_thread: threading.Thread | None = None
        self._recorder: AudioRecorder | None = None
        self._listen_recorder: DesktopAudioRecorder | None = None
        self._asr = None
        self._listen_asr = None
        self._asr_close_lock = threading.RLock()
        self._closing_asr_providers: list[Any] = []
        self._closed_asr_provider_refs: list[weakref.ReferenceType[Any]] = []
        self._asr_cleanup_threads: list[threading.Thread] = []
        self._translator = None
        self._output_dispatcher = OutputDispatcher(lambda: getattr(self, "_config", {}))
        self._mic_pipeline: MicPipeline | None = None
        self._listen_pipeline: ListenPipeline | None = None
        self._translation_context_store = TranslationContextStore()
        self._manual_translation_controller: ManualTranslationController | None = None
        self._rewrite_coordinator = self._create_rewrite_coordinator()
        self._update_install_preparing = False
        self._update_install_prepared = False
        self._update_install_was_running = False
        self._sender: VRCOSCSender | None = None
        self._osc_service = None
        self._overlay_service: OverlayService | None = None
        self._tts_manager: TTSManager | None = None
        self._deferred_cleanup_lock = threading.RLock()
        self._deferred_tts_managers: list[object] = []
        self._deferred_manual_translation_controllers: list[object] = []
        self._tts_enabled = bool(config.get("tts", {}).get("enabled", False))
        self._mic_muted = False
        self._mic_capture_paused_for_mute = False
        self._mic_in_speech = False
        self._listen_in_speech = False
        self._translating = False
        self._translation_state_lock = threading.Lock()
        self._active_translation_jobs = 0
        self._translation_failure_streak = 0
        self._translation_cooldown_until = 0.0
        self._translation_cooldown_category: str | None = None
        self._translation_backoff_by_source: dict[str, dict[str, Any]] = {}
        self._listen_tts_echo_suppress_until = 0.0
        self._listen_tts_echo_pending_count = 0
        self._listen_tts_echo_lock = threading.Lock()
        self._last_tts_text = ""
        self._last_tts_at = 0.0
        self._tts_dedup_s = 0.5  # Skip TTS if same text within this window
        self._devices: dict[str, int] = {}
        self._default_mic_device_name: str | None = None
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
        self._desktop_recovery_attempt = 0
        self._desktop_recovery_timer = QTimer(self)
        self._desktop_recovery_timer.setSingleShot(True)
        self._desktop_recovery_timer.timeout.connect(
            self._restart_desktop_capture
        )

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
        self._mode_manager = ModeManager(config)
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
        self._realtime_scheduler: RealtimeScheduler | None = None
        self._partial_generation = 0
        self._partial_result_lock = threading.Lock()
        self._partial_result_candidate = ""
        self._partial_result_hits = 0

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
        self._tgt_lang2_combo: QComboBox | None = None
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
        self._header_frame = None
        self._header_layout = None
        self._brand_layout = None
        self._brand_text_layout = None
        self._app_icon_label = None
        self._content_layout = None
        self._translation_card_layout = None
        self._flow_panel = None
        self._flow_layout = None
        self._flow_source_row = None
        self._flow_target_row = None
        self._panes_layout = None
        self._left_panel_layout = None
        self._right_panel_layout = None
        self._action_strip = None
        self._action_layout = None
        self._action_top_layout = None
        self._action_bottom_layout = None
        self._side_panel = None
        self._side_layout = None
        self._side_title_layout = None
        self._mode_layout = None
        self._mic_layout = None
        self._mic_actions_layout = None
        self._assist_layout = None

        self.setWindowTitle(self._t("window_title"))
        self.resize(*MAIN_WINDOW_DEFAULT_SIZE)
        self.setMinimumSize(*MAIN_WINDOW_MIN_SIZE)
        self._build_ui()
        self._disable_native_status_bar()
        self._apply_adaptive_layout(force=True)
        self._start_ui_callback_drain()
        self._refresh_static_texts()
        self._refresh_start_button()
        self._refresh_mic_mute_button()
        self._refresh_mode_buttons()
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        self._set_status(self._t("status_ready"), "success", key="status_ready")
        self._set_bottom(self._t("status_ready"), "success", key="status_ready")

        self._subscribe_realtime_tweaks_state()

        record_startup_stage(
            "ui.main_window_construct",
            started_at=self._startup_constructed_at,
        )
        logger.info("Qt MainWindow initialized")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._post_show_initialization_scheduled:
            return
        self._post_show_initialization_scheduled = True
        record_startup_stage("ui.window_visible")
        QTimer.singleShot(0, self._queue_post_show_initialization)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._first_paint_logged:
            return
        self._first_paint_logged = True
        record_startup_stage("ui.first_paint")

    def _queue_post_show_initialization(self) -> None:
        if self._destroying or self._post_show_initialization_started:
            return
        QTimer.singleShot(0, self._run_post_show_initialization)

    def _run_post_show_initialization(self) -> None:
        if self._destroying or self._post_show_initialization_started:
            return
        self._post_show_initialization_started = True
        started_at = time.perf_counter()
        record_startup_stage("ui.post_show_initialization_started")

        if self._initial_mode_change.changed:
            QTimer.singleShot(500, self._schedule_config_save)
        self._load_devices_async()
        self._resolve_virtual_output_async()
        self._register_hotkeys()
        self._schedule_desktop_audio_watch(2500)
        self._schedule_mic_audio_watch(2500)
        QTimer.singleShot(100, self._apply_deferred_background_image)
        QTimer.singleShot(1200, self._maybe_show_mode_wizard)
        QTimer.singleShot(2000, self._maybe_show_osc_guide)
        if self._startup_update_check_enabled():
            QTimer.singleShot(self._startup_update_check_delay_ms(), self._check_for_update)
        self._schedule_settings_preload(2500)
        QTimer.singleShot(0, self._apply_osc_listener_config)

        record_startup_stage(
            "ui.post_show_initialization_schedule",
            started_at=started_at,
        )

    def _apply_deferred_background_image(self) -> None:
        if self._destroying:
            return
        central = self.centralWidget()
        if isinstance(central, BackgroundWidget):
            central.set_background_path(self._background_image_path())

    def start_background_initialization(self) -> bool:
        """Schedule optional provider preparation after the first UI render."""

        if self._destroying or self._background_initialization_started:
            return False
        self._background_initialization_started = True
        started_at = time.perf_counter()

        if (
            self._translation_background_warmup_enabled()
            and first_missing_required_credential(
                self._config,
                scopes=("translation",),
                ui_language=getattr(self, "_ui_lang", None),
                active_only=True,
            )
            is None
        ):
            try:
                self._ensure_manual_translation_controller().prewarm_async()
            except Exception:
                logger.exception(
                    "Could not schedule manual translation provider prewarm"
                )
            try:
                self._start_translation_background_prewarm()
            except Exception:
                logger.exception(
                    "Could not schedule realtime translation provider prewarm"
                )

        self._start_asr_background_prewarm()
        QTimer.singleShot(
            _TTS_BACKGROUND_PREWARM_DELAY_MS,
            lambda: self._start_tts_background_prewarm(0),
        )
        record_startup_stage(
            "background.provider_initialization_schedule",
            started_at=started_at,
        )
        logger.info("Provider background initialization scheduled")
        return True

    def _translation_background_warmup_enabled(
        self,
        config: Mapping[str, Any] | None = None,
    ) -> bool:
        active_config = config if isinstance(config, Mapping) else self._config
        translation_cfg = active_config.get("translation", {})
        if not isinstance(translation_cfg, Mapping):
            return False
        output_needs_translation = (
            normalize_output_format(translation_cfg.get("output_format"))
            != "original_only"
        )
        rewrite_needs_translation = (
            normalize_asr_rewrite_style(
                translation_cfg.get("asr_rewrite_style", ASR_REWRITE_DISABLED)
            )
            != ASR_REWRITE_DISABLED
        )
        listen_cfg = active_config.get("vrc_listen", {})
        listen_needs_translation = bool(
            isinstance(listen_cfg, Mapping)
            and listen_cfg.get("enabled", False)
        )
        return bool(
            output_needs_translation
            or rewrite_needs_translation
            or listen_needs_translation
        )

    def _translation_prewarm_lifecycle_lock(self) -> threading.RLock:
        lock = self.__dict__.get("_translation_prewarm_lock")
        if lock is None:
            lock = threading.RLock()
            self._translation_prewarm_lock = lock
        return lock

    @staticmethod
    def _close_translation_providers(*providers: Any, label: str) -> None:
        closed: list[Any] = []
        for provider in providers:
            if provider is None or any(provider is item for item in closed):
                continue
            closed.append(provider)
            _close_provider_quietly(provider, label=label)

    def _translation_prewarm_sources(self, config: Mapping[str, Any]) -> tuple[str, ...]:
        listen_cfg = config.get("vrc_listen", {})
        listen_enabled = bool(
            isinstance(listen_cfg, Mapping) and listen_cfg.get("enabled", False)
        )
        return (MIC_SOURCE, DESKTOP_SOURCE) if listen_enabled else (MIC_SOURCE,)

    def _start_translation_background_prewarm(self) -> bool:
        """Prepare selected realtime translation clients before listening starts.

        The preparation is deliberately detached from ``RealtimeScheduler.start``.
        A slow public endpoint therefore cannot leave the start button in a
        network-dependent state; workers consume a completed client when one is
        available and otherwise start with a cold client on their first request.
        """

        if self._destroying:
            return False
        config_snapshot = copy.deepcopy(self._config)
        if (
            not self._translation_background_warmup_enabled(config_snapshot)
            or not self._realtime_translation_credentials_available(config_snapshot)
        ):
            return False
        signature = provider_runtime_config_signature(config_snapshot)
        try:
            worker_count = max(1, min(self._realtime_translation_worker_concurrency(), 4))
        except Exception:
            worker_count = 2
        sources = self._translation_prewarm_sources(config_snapshot)
        lock = self._translation_prewarm_lifecycle_lock()
        with lock:
            existing = getattr(self, "_translation_prewarm_thread", None)
            existing_signature = getattr(self, "_translation_prewarm_signature", None)
            if (
                existing_signature == signature
                and existing is not None
                and (existing.ident is None or existing.is_alive())
            ):
                return True
            cancel_event = threading.Event()
            previous_cancel = getattr(
                self, "_translation_prewarm_cancel_event", None
            )
            if previous_cancel is not None:
                previous_cancel.set()
            for event in getattr(self, "_translation_prewarm_cancel_events", set()):
                event.set()
            existing_pools = getattr(
                self,
                "_prewarmed_realtime_translators",
                {},
            )
            if not isinstance(existing_pools, Mapping):
                existing_pools = {}
            stale = [
                provider
                for values in existing_pools.values()
                for provider in values
            ]
            self._prewarmed_realtime_translators = {
                MIC_SOURCE: [],
                DESKTOP_SOURCE: [],
            }
            self._translation_prewarm_cancel_event = cancel_event
            self._translation_prewarm_signature = signature
        self._close_translation_providers(
            *stale,
            label="replaced realtime translation prewarm provider",
        )

        results: dict[str, list[Any]] = {source: [] for source in sources}
        results_lock = threading.Lock()

        def prepare(source: str) -> None:
            provider = None
            try:
                provider = self._create_realtime_translator(
                    config_snapshot,
                    source=source,
                )
                self._prewarm_realtime_translator(provider)
                if cancel_event.is_set() or self._destroying:
                    return
                with results_lock:
                    results[source].append(provider)
                    provider = None
            except Exception as exc:
                logger.warning(
                    "Realtime translation background provider prewarm failed "
                    "source=%s error=%s",
                    source,
                    safe_exception_summary(exc),
                )
            finally:
                if provider is not None:
                    self._close_translation_providers(
                        provider,
                        label="unretained realtime translation prewarm provider",
                    )

        def run() -> None:
            started_at = time.perf_counter()
            retained: list[Any] = []
            # Each provider gets its own short-lived owner thread. This keeps
            # thread-local requests sessions transferable to the eventual worker
            # without sharing a mutable Session concurrently.
            jobs: list[threading.Thread] = []
            try:
                for source in sources:
                    for index in range(worker_count):
                        job = threading.Thread(
                            target=prepare,
                            args=(source,),
                            daemon=True,
                            name=f"translation-provider-prewarm-{source}-{index + 1}",
                        )
                        jobs.append(job)
                        job.start()
                for job in jobs:
                    job.join()
                with lock:
                    if (
                        not cancel_event.is_set()
                        and not self._destroying
                        and getattr(self, "_translation_prewarm_cancel_event", None)
                        is cancel_event
                    ):
                        self._prewarmed_realtime_translators = {
                            MIC_SOURCE: list(results.get(MIC_SOURCE, [])),
                            DESKTOP_SOURCE: list(
                                results.get(DESKTOP_SOURCE, [])
                            ),
                        }
                        retained = [
                            provider
                            for values in self._prewarmed_realtime_translators.values()
                            for provider in values
                        ]
                if not retained:
                    self._close_translation_providers(
                        *[
                            provider
                            for values in results.values()
                            for provider in values
                        ],
                        label="unretained realtime translation prewarm provider",
                    )
                logger.info(
                    "Realtime translation background prewarm finished "
                    "sources=%s workers=%d retained=%d",
                    ",".join(sources),
                    worker_count,
                    len(retained),
                )
            finally:
                current = threading.current_thread()
                with lock:
                    if getattr(self, "_translation_prewarm_thread", None) is current:
                        self._translation_prewarm_thread = None
                    self.__dict__.setdefault(
                        "_translation_prewarm_threads",
                        set(),
                    ).discard(current)
                    self.__dict__.setdefault(
                        "_translation_prewarm_cancel_events",
                        set(),
                    ).discard(cancel_event)
                record_startup_stage(
                    "background.translation_provider_prewarm",
                    started_at=started_at,
                    outcome="ok" if retained else "skipped",
                )

        thread = threading.Thread(
            target=run,
            daemon=True,
            name="translation-provider-prewarm",
        )
        with lock:
            if self._destroying:
                cancel_event.set()
                return False
            self._translation_prewarm_thread = thread
            self.__dict__.setdefault("_translation_prewarm_threads", set()).add(thread)
            self.__dict__.setdefault(
                "_translation_prewarm_cancel_events",
                set(),
            ).add(cancel_event)
        try:
            thread.start()
        except BaseException:
            with lock:
                if self._translation_prewarm_thread is thread:
                    self._translation_prewarm_thread = None
                self.__dict__.setdefault("_translation_prewarm_threads", set()).discard(
                    thread
                )
                self.__dict__.setdefault(
                    "_translation_prewarm_cancel_events",
                    set(),
                ).discard(cancel_event)
            cancel_event.set()
            raise
        return True

    def _take_realtime_prewarmed_translator(
        self,
        config: Mapping[str, Any],
        *,
        source: str,
    ) -> Any:
        signature = provider_runtime_config_signature(config)
        lock = self._translation_prewarm_lifecycle_lock()
        stale: list[Any] = []
        provider = None
        with lock:
            if getattr(self, "_translation_prewarm_signature", None) != signature:
                existing_pools = getattr(
                    self,
                    "_prewarmed_realtime_translators",
                    {},
                )
                if not isinstance(existing_pools, Mapping):
                    existing_pools = {}
                stale = [
                    item
                    for values in existing_pools.values()
                    for item in values
                ]
                self._prewarmed_realtime_translators = {
                    MIC_SOURCE: [],
                    DESKTOP_SOURCE: [],
                }
            else:
                pools = getattr(self, "_prewarmed_realtime_translators", None)
                if not isinstance(pools, dict):
                    pools = {
                        MIC_SOURCE: [],
                        DESKTOP_SOURCE: [],
                    }
                    self._prewarmed_realtime_translators = pools
                values = pools.setdefault(source, [])
                if values:
                    provider = values.pop(0)
        self._close_translation_providers(
            *stale,
            label="stale realtime translation prewarm provider",
        )
        if provider is not None:
            self._bind_realtime_translation_context_store(provider)
            logger.info(
                "Retained realtime translation prewarm consumed source=%s",
                source,
            )
        return provider

    def _bind_realtime_translation_context_store(self, translator: Any) -> None:
        """Attach a retained client to the session store created for this run."""

        store = getattr(self, "_translation_context_store", None)
        if not isinstance(store, TranslationContextStore):
            return
        pending = [translator]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            if hasattr(current, "_context_store"):
                current._context_store = store
                current._owns_context_store = False
            primary = getattr(current, "_primary", None)
            if primary is not None:
                pending.append(primary)
            fallbacks = getattr(current, "_fallbacks", None)
            if isinstance(fallbacks, Mapping):
                pending.extend(fallbacks.values())

    def _cancel_translation_background_prewarm(self) -> None:
        lock = self._translation_prewarm_lifecycle_lock()
        with lock:
            cancel_event = getattr(self, "_translation_prewarm_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
            for event in getattr(self, "_translation_prewarm_cancel_events", set()):
                event.set()
            existing_pools = getattr(
                self,
                "_prewarmed_realtime_translators",
                {},
            )
            if not isinstance(existing_pools, Mapping):
                existing_pools = {}
            providers = [
                provider
                for values in existing_pools.values()
                for provider in values
            ]
            self._prewarmed_realtime_translators = {
                MIC_SOURCE: [],
                DESKTOP_SOURCE: [],
            }
            self._translation_prewarm_signature = None
        self._close_translation_providers(
            *providers,
            label="cancelled realtime translation prewarm provider",
        )

    @staticmethod
    def _asr_engine_credentials_available(config: dict, engine: str) -> bool:
        probe = copy.deepcopy(config)
        asr_cfg = probe.setdefault("asr", {})
        if not isinstance(asr_cfg, dict):
            asr_cfg = {}
            probe["asr"] = asr_cfg
        asr_cfg["engine"] = engine
        listen_cfg = probe.setdefault("vrc_listen", {})
        if not isinstance(listen_cfg, dict):
            listen_cfg = {}
            probe["vrc_listen"] = listen_cfg
        listen_cfg["enabled"] = False
        return (
            first_missing_required_credential(
                probe,
                scopes=("asr",),
                active_only=True,
            )
            is None
        )

    @staticmethod
    def _warm_asr_provider(provider: Any, engine: str) -> bool:
        del engine
        prewarm = getattr(provider, "prewarm", None)
        if not callable(prewarm):
            return False
        return bool(prewarm())

    def _asr_prewarm_lifecycle_lock(self) -> threading.RLock:
        lock = self.__dict__.get("_asr_prewarm_lock")
        if lock is None:
            lock = threading.RLock()
            self._asr_prewarm_lock = lock
        return lock

    @staticmethod
    def _close_unique_asr_providers(*providers: Any, label: str) -> None:
        closed: list[Any] = []
        for provider in providers:
            if provider is None or any(provider is item for item in closed):
                continue
            closed.append(provider)
            _close_provider_quietly(provider, label=label)

    def _start_asr_background_prewarm(self) -> bool:
        if self._destroying:
            return False
        config_snapshot = copy.deepcopy(self._config)
        main_engine = _main_asr_engine(config_snapshot)
        listen_engine = _listen_asr_engine(config_snapshot)
        listen_cfg = config_snapshot.get("vrc_listen", {})
        listen_enabled = bool(
            isinstance(listen_cfg, Mapping)
            and listen_cfg.get("enabled", False)
        )
        candidates = {main_engine}
        if listen_enabled:
            candidates.add(listen_engine)
        if not candidates.intersection(_ONLINE_ASR_PREWARM_ENGINES):
            return False

        signature = _asr_pair_config_signature(config_snapshot)
        lock = self._asr_prewarm_lifecycle_lock()
        with lock:
            existing = getattr(self, "_asr_prewarm_thread", None)
            if existing is not None and (
                existing.ident is None or existing.is_alive()
            ):
                return True
            cancel_event = threading.Event()
            self._asr_prewarm_cancel_event = cancel_event

        def run() -> None:
            started_at = time.perf_counter()
            main_provider = None
            listen_provider = None
            outcome = "skipped"
            try:
                if (
                    main_engine in _ONLINE_ASR_PREWARM_ENGINES
                    and self._asr_engine_credentials_available(
                        config_snapshot,
                        main_engine,
                    )
                ):
                    try:
                        main_provider = create_asr(
                            config_snapshot,
                            engine=main_engine,
                        )
                    except Exception as exc:
                        logger.warning(
                            "ASR background provider construction failed "
                            "role=main engine=%s error=%s",
                            main_engine,
                            safe_exception_summary(exc),
                        )

                matching_runtime = bool(
                    listen_enabled
                    and _listen_asr_reuses_main(config_snapshot)
                    and _asr_runtime_signature(config_snapshot, listen_engine)
                    == _asr_runtime_signature(config_snapshot, main_engine)
                )
                isolate_qwen_listen = bool(
                    matching_runtime and listen_engine == "qwen3-asr"
                )
                if (
                    listen_enabled
                    and matching_runtime
                    and not isolate_qwen_listen
                ):
                    listen_provider = main_provider
                elif (
                    listen_enabled
                    and listen_engine in _ONLINE_ASR_PREWARM_ENGINES
                    and self._asr_engine_credentials_available(
                        config_snapshot,
                        listen_engine,
                    )
                ):
                    try:
                        listen_provider = create_asr(
                            _listen_asr_provider_config(
                                config_snapshot,
                                listen_engine,
                            ),
                            engine=listen_engine,
                        )
                    except Exception as exc:
                        logger.warning(
                            "ASR background provider construction failed "
                            "role=listen engine=%s error=%s",
                            listen_engine,
                            safe_exception_summary(exc),
                        )

                if cancel_event.is_set():
                    return

                warmed: list[Any] = []
                for role, provider, engine in (
                    ("main", main_provider, main_engine),
                    ("listen", listen_provider, listen_engine),
                ):
                    if provider is None or any(provider is item for item in warmed):
                        continue
                    warmed.append(provider)
                    try:
                        succeeded = self._warm_asr_provider(provider, engine)
                        logger.info(
                            "ASR provider background prewarm finished "
                            "role=%s engine=%s succeeded=%s",
                            role,
                            engine,
                            succeeded,
                        )
                    except Exception as exc:
                        logger.warning(
                            "ASR provider background prewarm failed "
                            "role=%s engine=%s error=%s",
                            role,
                            engine,
                            safe_exception_summary(exc),
                        )

                if not warmed or cancel_event.is_set():
                    return
                stale_main = None
                stale_listen = None
                with lock:
                    if (
                        cancel_event.is_set()
                        or self._destroying
                        or getattr(self, "_asr_prewarm_cancel_event", None)
                        is not cancel_event
                    ):
                        return
                    stale_main = getattr(self, "_prewarmed_main_asr", None)
                    stale_listen = getattr(self, "_prewarmed_listen_asr", None)
                    self._prewarmed_asr_signature = signature
                    self._prewarmed_main_asr = main_provider
                    self._prewarmed_listen_asr = listen_provider
                    main_provider = None
                    listen_provider = None
                    outcome = "ok"
                self._close_unique_asr_providers(
                    stale_main,
                    stale_listen,
                    label="replaced prewarmed ASR provider",
                )
            finally:
                self._close_unique_asr_providers(
                    main_provider,
                    listen_provider,
                    label="unretained ASR prewarm provider",
                )
                current = threading.current_thread()
                with lock:
                    if getattr(self, "_asr_prewarm_thread", None) is current:
                        self._asr_prewarm_thread = None
                record_startup_stage(
                    "background.asr_provider_prewarm",
                    started_at=started_at,
                    outcome=outcome,
                )

        thread = threading.Thread(
            target=run,
            daemon=True,
            name="asr-provider-prewarm",
        )
        with lock:
            if self._destroying:
                cancel_event.set()
                return False
            self._asr_prewarm_thread = thread
        try:
            thread.start()
        except BaseException:
            with lock:
                if self._asr_prewarm_thread is thread:
                    self._asr_prewarm_thread = None
            cancel_event.set()
            raise
        return True

    def _take_prewarmed_asr_pair(self, config: dict) -> tuple[Any, Any]:
        expected_signature = _asr_pair_config_signature(config)
        lock = self._asr_prewarm_lifecycle_lock()
        stale_main = None
        stale_listen = None
        with lock:
            cancel_event = getattr(self, "_asr_prewarm_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
            cached_signature = getattr(self, "_prewarmed_asr_signature", None)
            main_provider = getattr(self, "_prewarmed_main_asr", None)
            listen_provider = getattr(self, "_prewarmed_listen_asr", None)
            self._prewarmed_asr_signature = None
            self._prewarmed_main_asr = None
            self._prewarmed_listen_asr = None
            if cached_signature != expected_signature:
                stale_main = main_provider
                stale_listen = listen_provider
                main_provider = None
                listen_provider = None
        self._close_unique_asr_providers(
            stale_main,
            stale_listen,
            label="stale prewarmed ASR provider",
        )
        if main_provider is not None or listen_provider is not None:
            logger.info(
                "Retained ASR provider prewarm consumed main=%s listen=%s",
                main_provider is not None,
                listen_provider is not None,
            )
        return main_provider, listen_provider

    def _cancel_asr_background_prewarm(self) -> None:
        lock = self._asr_prewarm_lifecycle_lock()
        with lock:
            cancel_event = getattr(self, "_asr_prewarm_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
            main_provider = getattr(self, "_prewarmed_main_asr", None)
            listen_provider = getattr(self, "_prewarmed_listen_asr", None)
            self._prewarmed_asr_signature = None
            self._prewarmed_main_asr = None
            self._prewarmed_listen_asr = None
        self._close_unique_asr_providers(
            main_provider,
            listen_provider,
            label="cancelled prewarmed ASR provider",
        )

    def _tts_prewarm_lifecycle_lock(self) -> threading.RLock:
        lock = self.__dict__.get("_tts_prewarm_lock")
        if lock is None:
            lock = threading.RLock()
            self._tts_prewarm_lock = lock
        return lock

    def _tts_manager_lifecycle_lock(self) -> threading.RLock:
        lock = self.__dict__.get("_tts_manager_lock")
        if lock is None:
            lock = threading.RLock()
            self._tts_manager_lock = lock
        return lock

    def _start_tts_background_prewarm(self, retry: int = 0) -> bool:
        if self._destroying:
            return False
        if bool(getattr(self, "_virtual_output_resolution_in_progress", False)):
            if retry < _TTS_VIRTUAL_OUTPUT_WAIT_RETRIES:
                QTimer.singleShot(
                    250,
                    lambda attempt=retry + 1: self._start_tts_background_prewarm(
                        attempt
                    ),
                )
            return False

        tts_cfg = self._config.get("tts", {})
        if not isinstance(tts_cfg, Mapping) or not bool(
            tts_cfg.get("enabled", False)
        ):
            return False
        engine = self._current_tts_engine().strip().lower()
        if engine not in _ONLINE_TTS_PREWARM_ENGINES:
            return False
        if (
            first_missing_required_credential(
                self._config,
                scopes=("tts",),
                ui_language=getattr(self, "_ui_lang", None),
                active_only=True,
            )
            is not None
        ):
            return False

        signature = self._tts_runtime_signature()
        engine_config = self._current_tts_engine_config()
        perf_cfg = self._performance_config()
        build_kwargs = {
            "engine_name": self._current_tts_engine(),
            "cache_enabled": True,
            # Do not initialize a local fallback during background startup.
            # If the selected online engine is unavailable, first real use can
            # still construct the normal fallback-enabled manager.
            "allow_fallback": False,
            "output_device": tts_cfg.get("output_device"),
            "output_device_name": str(tts_cfg.get("output_device_name") or ""),
            "prefer_virtual_output": bool(tts_cfg.get("output_to_vrchat", False)),
            "monitor_output": bool(tts_cfg.get("monitor_enabled", False)),
            "sbv2_device": "cpu",
            "sbv2_bert_language": "jp",
            "engine_config": engine_config,
            "max_cache_size_mb": int(perf_cfg.get("tts_cache_max_mb", 24)),
            "max_cache_items": int(perf_cfg.get("tts_cache_max_items", 60)),
        }
        voice = str(engine_config.get("voice") or "").strip()
        prewarm_lock = self._tts_prewarm_lifecycle_lock()
        with prewarm_lock:
            existing = getattr(self, "_tts_prewarm_thread", None)
            if existing is not None and (
                existing.ident is None or existing.is_alive()
            ):
                return True
            cancel_event = threading.Event()
            self._tts_prewarm_cancel_event = cancel_event
            self._tts_prewarm_signature = signature

        def run() -> None:
            started_at = time.perf_counter()
            manager = None
            outcome = "skipped"
            try:
                from src.tts.manager import TTSManager

                manager = TTSManager(**build_kwargs)
                if not manager.is_available() or cancel_event.is_set():
                    return
                manager.start()
                if cancel_event.is_set():
                    return
                prewarm = getattr(manager, "prewarm", None)
                if callable(prewarm):
                    prewarm(voice)

                manager_lock = self._tts_manager_lifecycle_lock()
                with manager_lock:
                    current_signature = self._tts_runtime_signature()
                    existing_manager = getattr(self, "_tts_manager", None)
                    if (
                        cancel_event.is_set()
                        or self._destroying
                        or current_signature != signature
                        or existing_manager is not None
                    ):
                        return
                    self._tts_manager = manager
                    self._tts_manager_signature = signature
                    manager = None
                    outcome = "ok"
                logger.info(
                    "Retained online TTS manager prewarm ready engine=%s",
                    engine,
                )
            except Exception as exc:
                outcome = "error"
                logger.warning(
                    "Online TTS background prewarm failed engine=%s error=%s",
                    engine,
                    safe_exception_summary(exc),
                )
            finally:
                if manager is not None:
                    self._close_tts_manager_instance(manager)
                current = threading.current_thread()
                with prewarm_lock:
                    if getattr(self, "_tts_prewarm_thread", None) is current:
                        self._tts_prewarm_thread = None
                        self._tts_prewarm_signature = None
                record_startup_stage(
                    "background.tts_provider_prewarm",
                    started_at=started_at,
                    outcome=outcome,
                )

        thread = threading.Thread(
            target=run,
            daemon=True,
            name="tts-provider-prewarm",
        )
        with prewarm_lock:
            if self._destroying:
                cancel_event.set()
                return False
            self._tts_prewarm_thread = thread
        try:
            thread.start()
        except BaseException:
            with prewarm_lock:
                if self._tts_prewarm_thread is thread:
                    self._tts_prewarm_thread = None
                    self._tts_prewarm_signature = None
            cancel_event.set()
            raise
        return True

    @staticmethod
    def _close_tts_manager_instance(manager: Any) -> None:
        try:
            close = getattr(manager, "close", None)
            if callable(close):
                close()
                return
            stop = getattr(manager, "stop", None)
            if callable(stop):
                stop()
        except Exception as exc:
            logger.debug(
                "Failed to close unretained TTS manager: %s",
                safe_exception_summary(exc),
            )

    def _cancel_tts_background_prewarm(self) -> None:
        lock = self._tts_prewarm_lifecycle_lock()
        with lock:
            cancel_event = getattr(self, "_tts_prewarm_cancel_event", None)
            if cancel_event is not None:
                cancel_event.set()
            self._tts_prewarm_signature = None

    def _restart_background_provider_initialization(self) -> None:
        self._cancel_translation_background_prewarm()
        self._cancel_asr_background_prewarm()
        self._cancel_tts_background_prewarm()
        self._background_initialization_started = False
        if not self._destroying:
            QTimer.singleShot(0, self.start_background_initialization)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_adaptive_layout()
        if getattr(self, "_device_combo", None) is not None:
            QTimer.singleShot(0, self._refresh_device_combo)

    # ----------------------------------------------------------------
    # Public
    # ----------------------------------------------------------------
    def _create_settings_window(self, *, preload: bool = False, defer_initial_page: bool = False):
        # Feature flag: Use new tabbed settings window (currently disabled)
        use_new_settings = False  # Set to True to enable new settings UI

        if use_new_settings:
            from src.ui_qt.settings import SettingsWindowTabbed

            win = SettingsWindowTabbed(
                self._config,
                ui_language=self._ui_lang,
                parent=self,
            )
            win.config_changed.connect(lambda cfg: self._on_config_saved())
            self._settings_window = win
            return win
        else:
            # Original settings window
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
                on_deferred_tts_manager=self._remember_deferred_tts_manager,
                preload=preload,
                defer_initial_page=defer_initial_page,
            )
            # Connect language change signal for immediate UI refresh
            win.language_changed.connect(self._on_language_changed)
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


    def _schedule_settings_preload(self, delay_ms: int) -> None:
        if not self._settings_preload_enabled():
            logger.debug("Skipping settings preload while Style-Bert-VITS2 is selected")
            return
        QTimer.singleShot(delay_ms, self._preload_settings_window)

    @staticmethod
    def _select_settings_page(
        window: object,
        page_id: str | None,
        focus_target: str | None,
    ) -> None:
        if not page_id or not hasattr(window, "select_page"):
            return
        select_page = getattr(window, "select_page")
        try:
            select_page(page_id, focus_target=focus_target)
        except TypeError:
            # Keep compatibility with the feature-flagged tabbed settings UI
            # and third-party embedding shims that expose the historical
            # one-argument method.
            select_page(page_id)
            focus_credential = getattr(window, "focus_credential_target", None)
            if focus_target and callable(focus_credential):
                QTimer.singleShot(
                    0,
                    lambda target=focus_target: focus_credential(target),
                )

    def show_settings(
        self,
        page_id: str | None = None,
        focus_target: str | None = None,
    ) -> None:
        if self._settings_window is not None and getattr(self._settings_window, "_closing", False):
            self._settings_window = None
        if self._settings_window is not None:
            self._sync_settings_window_vrc_listen_state()
            self._select_settings_page(
                self._settings_window,
                page_id,
                focus_target,
            )
            self._settings_window.show()
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        win = self._create_settings_window(defer_initial_page=True)
        self._select_settings_page(win, page_id, focus_target)
        win.show()
        win.raise_()
        win.activateWindow()

    def _prompt_for_missing_credential(
        self,
        scopes: tuple[str, ...],
    ) -> bool:
        ui_language = getattr(self, "_ui_lang", None) or get_ui_language(
            getattr(self, "_config", {})
        )
        missing = first_missing_required_credential(
            self._config,
            scopes=scopes,
            ui_language=ui_language,
            active_only=True,
        )
        if missing is None:
            return False

        show_missing_credential_prompt(
            self,
            missing,
            ui_language=ui_language,
            open_settings=lambda item=missing: self.show_settings(
                page_id=item.settings_page,
                focus_target=item.focus_target,
            ),
            trigger="runtime_" + "_".join(scopes),
            active_only=True,
        )
        return True

    def open_mode_wizard(self) -> None:
        self._open_mode_wizard(mark_seen=True)

    def destroy(self) -> None:
        self._shutdown()
        super().close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._shutdown()
        super().closeEvent(event)

    @staticmethod
    def _create_rewrite_coordinator() -> RewriteCallCoordinator:
        return RewriteCallCoordinator(
            max_active_calls=ASR_REWRITE_WORKER_CONCURRENCY,
            max_pending_leaders=ASR_REWRITE_TASK_QUEUE_MAXSIZE + 2,
            cache_size=256,
            realtime_burst=MIC_PRIORITY_BURST,
        )

    def _prepare_for_update_install(self) -> bool:
        """Quiesce runtime-owned resources before launching a visible update.

        This deliberately does not call ``_shutdown``: the update dialog must
        stay alive if the installer cannot be spawned.  It performs the same
        runtime teardown while preserving the window and can be rolled back by
        ``_abort_update_install_preparation``.
        """

        if getattr(self, "_destroying", False):
            return False
        if getattr(self, "_update_install_prepared", False):
            return True
        if getattr(self, "_update_install_preparing", False):
            return False

        self._update_install_preparing = True
        self._update_install_was_running = bool(getattr(self, "_running", False))
        deadline = time.monotonic() + UPDATE_INSTALL_QUIESCE_TIMEOUT_S
        try:
            # Startup prewarm owns provider clients outside the realtime and
            # manual-translation lifecycles. Cancel it before the first
            # quiescence wait so the updater cannot race a retained ASR client
            # or an in-flight ASR/TTS connection setup.
            self._cancel_translation_background_prewarm()
            self._cancel_asr_background_prewarm()
            self._cancel_tts_background_prewarm()

            # _do_stop invalidates the session before capture can admit more
            # work, cancels provider calls, drains TTS/OSC queues, and closes
            # ASR providers only after scheduler workers have stopped.
            shutdown_barrier = self._do_stop()
            if shutdown_barrier is not None:
                remaining = min(
                    UPDATE_INSTALL_QUIESCE_TIMEOUT_S,
                    deadline - time.monotonic(),
                )
                if remaining <= 0 or not shutdown_barrier.wait(remaining):
                    logger.error("Timed out waiting for realtime workers before update")
                    return False
            if not self._wait_for_update_runtime_quiescence(deadline):
                logger.error("Timed out closing realtime providers before update")
                return False

            # Manual text/rewrite work does not belong to the realtime
            # scheduler.  Close its clients and wait for any leased provider
            # calls so no background request remains when the installer opens.
            self._clear_cached_translator()
            remaining = max(0.0, deadline - time.monotonic())
            if not self._close_manual_translation_controller(
                wait_timeout_s=remaining,
            ):
                logger.error("Timed out closing manual translation workers before update")
                return False
            remaining = max(0.0, deadline - time.monotonic())
            self._reset_tts_manager(timeout_seconds=remaining)
            self._close_update_osc_service()

            coordinator = getattr(self, "_rewrite_coordinator", None)
            if coordinator is not None:
                coordinator.close()
            self._discard_ui_callbacks()
            self._flush_config_save()

            if not self._wait_for_update_runtime_quiescence(deadline):
                logger.error("Realtime cleanup did not finish before update launch")
                return False
            self._update_install_prepared = True
            return True
        except Exception:
            logger.exception("Failed to prepare Mio for visible update installation")
            return False
        finally:
            self._update_install_preparing = False

    def _wait_for_update_runtime_quiescence(self, deadline: float) -> bool:
        """Wait for all deferred runtime cleanup before installer handoff."""

        while self._runtime_cleanup_in_progress():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def _close_update_osc_service(self) -> None:
        """Release both OSC sender and listener for the installer handoff."""

        service = getattr(self, "_osc_service", None)
        if service is not None:
            try:
                service.close()
            except Exception:
                logger.debug("Failed to close OSC service for update", exc_info=True)
            self._osc_service = None
            self._sender = None
            return
        sender = getattr(self, "_sender", None)
        if sender is not None:
            try:
                sender.close()
            except Exception:
                logger.debug("Failed to close OSC sender for update", exc_info=True)
            self._sender = None

    def _abort_update_install_preparation(self) -> None:
        """Make a failed installer launch recoverable without exiting Mio."""

        was_running = bool(getattr(self, "_update_install_was_running", False))
        self._update_install_preparing = False
        self._update_install_prepared = False
        self._update_install_was_running = False

        coordinator = getattr(self, "_rewrite_coordinator", None)
        snapshot = getattr(coordinator, "snapshot", None)
        needs_replacement = coordinator is None
        if callable(snapshot):
            try:
                needs_replacement = bool(snapshot().closed)
            except Exception:
                needs_replacement = True
        if needs_replacement:
            self._rewrite_coordinator = self._create_rewrite_coordinator()

        self._flush_config_save()
        if was_running and not getattr(self, "_destroying", False):
            # _do_start already waits for deferred ASR cleanup, so a provider
            # that needed longer than the update handoff can still recover.
            QTimer.singleShot(0, self._do_start)
            return
        self._refresh_start_button()
        self._set_status(self._t("status_ready"), "success", key="status_ready")

    def _shutdown(self) -> None:
        if self._destroying:
            return
        shutdown_started_at = time.monotonic()
        self._destroying = True
        self._cancel_translation_background_prewarm()
        self._cancel_asr_background_prewarm()
        self._cancel_tts_background_prewarm()
        with self._asr_lifecycle_lock():
            startup_cancel_event = getattr(self, "_startup_cancel_event", None)
            if startup_cancel_event is not None:
                startup_cancel_event.set()
        logger.info("Qt MainWindow shutdown requested")
        self._stop_hotkeys()
        self._stop_owned_timers()
        self._release_overlay_service()
        self._close_independent_tool_windows()
        self._flush_config_save()
        if self._running:
            self._do_stop()
        else:
            with self._asr_lifecycle_lock():
                self._listen_session = getattr(self, "_listen_session", 0) + 1
                self._running = False
            self._reset_streaming_state()
            self._stop_listen()
            self._stop_microphone_capture()
            shutdown_barrier = self._stop_workers()
            self._close_asr_providers(wait_for=shutdown_barrier)
            self._close_osc_sender()
        self._clear_cached_translator()
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
        self._close_manual_translation_controller()
        coordinator = getattr(self, "_rewrite_coordinator", None)
        if coordinator is not None:
            coordinator.close()
        self._discard_ui_callbacks()
        deferred_cleanup = self._runtime_cleanup_in_progress()
        foreground_elapsed_s = max(0.0, time.monotonic() - shutdown_started_at)
        logger.info(
            "Qt MainWindow foreground shutdown complete elapsed_ms=%.1f "
            "deferred_cleanup=%s",
            foreground_elapsed_s * 1000.0,
            deferred_cleanup,
        )
        if deferred_cleanup:
            self._start_shutdown_completion_monitor(shutdown_started_at)
        else:
            logger.info(
                "Qt MainWindow shutdown cleanup complete elapsed_ms=%.1f "
                "deferred_cleanup=false",
                foreground_elapsed_s * 1000.0,
            )

    def _start_shutdown_completion_monitor(self, shutdown_started_at: float) -> None:
        """Log the terminal lifecycle event after deferred provider cleanup."""

        existing = getattr(self, "_shutdown_completion_thread", None)
        if existing is not None:
            try:
                if existing.is_alive():
                    return
            except Exception:
                return

        def monitor() -> None:
            while True:
                lock = self._asr_lifecycle_lock()
                with lock:
                    tracked = tuple(
                        thread
                        for thread in self.__dict__.setdefault(
                            "_asr_cleanup_threads",
                            [],
                        )
                        if thread is not threading.current_thread()
                    )
                    startup_thread = getattr(self, "_startup_thread", None)
                    closing = bool(
                        self.__dict__.setdefault("_closing_asr_providers", [])
                    )
                alive: list[threading.Thread] = []
                for thread in tracked:
                    try:
                        if thread.is_alive():
                            alive.append(thread)
                    except Exception:
                        alive.append(thread)
                if startup_thread is not None:
                    try:
                        if startup_thread.is_alive():
                            alive.append(startup_thread)
                    except Exception:
                        alive.append(startup_thread)
                runtime_cleanup = self._runtime_cleanup_in_progress()
                if not alive and not closing and not runtime_cleanup:
                    break
                if alive:
                    for thread in alive:
                        if thread is not threading.current_thread():
                            try:
                                thread.join(timeout=0.25)
                            except Exception:
                                logger.debug(
                                    "Failed to wait for deferred shutdown cleanup",
                                    exc_info=True,
                                )
                else:
                    time.sleep(0.05)
            logger.info(
                "Qt MainWindow shutdown cleanup complete elapsed_ms=%.1f "
                "deferred_cleanup=true",
                max(0.0, time.monotonic() - shutdown_started_at) * 1000.0,
            )

        thread = threading.Thread(
            target=monitor,
            daemon=True,
            name="shutdown-completion-monitor",
        )
        self._shutdown_completion_thread = thread
        thread.start()

    def _stop_owned_timers(self) -> None:
        """Stop child timers so a closed window cannot keep doing background work."""

        try:
            timers = tuple(self.findChildren(QTimer))
        except Exception:
            timers = tuple(
                timer
                for timer in self.__dict__.values()
                if isinstance(timer, QTimer)
            )
        for timer in timers:
            try:
                timer.stop()
            except Exception:
                logger.debug("Failed to stop MainWindow timer", exc_info=True)

    def _release_overlay_service(self) -> None:
        dispatcher = getattr(self, "_output_dispatcher", None)
        if dispatcher is not None:
            for sink_name in ("overlay", "ui", "tts"):
                try:
                    dispatcher.unregister_sink(sink_name)
                except Exception:
                    logger.debug(
                        "Failed to unregister output sink %s",
                        sink_name,
                        exc_info=True,
                    )

        service = getattr(self, "_overlay_service", None)
        self._overlay_service = None
        if service is None:
            return
        try:
            service.set_enabled(False, reveal=False)
        except Exception:
            logger.debug("Failed to disable overlay service", exc_info=True)
        try:
            service.set_backend(None)
        except Exception:
            logger.debug("Failed to detach overlay backend", exc_info=True)
        try:
            service.deleteLater()
        except Exception:
            logger.debug("Failed to dispose overlay service", exc_info=True)

    def _close_manual_translation_controller(
        self,
        *,
        wait_timeout_s: float | None = None,
    ) -> bool:
        controller = getattr(self, "_manual_translation_controller", None)
        self._manual_translation_controller = None
        if controller is None:
            return True
        try:
            controller.invalidate()
        except Exception as exc:
            logger.debug(
                "Failed to invalidate manual translation: %s",
                safe_exception_summary(exc),
            )
        for signal_name, callback_name in (
            ("started", "_on_manual_translate_started"),
            ("succeeded", "_on_manual_translate_success"),
            ("failed", "_on_manual_translate_error"),
            ("worker_finished", "_finish_manual_translate_worker"),
        ):
            signal = getattr(controller, signal_name, None)
            callback = getattr(self, callback_name, None)
            disconnect = getattr(signal, "disconnect", None)
            if callable(disconnect) and callback is not None:
                try:
                    disconnect(callback)
                except Exception:
                    pass
        try:
            if wait_timeout_s is None:
                stopped = controller.close()
            else:
                try:
                    stopped = controller.close(wait_timeout_s=wait_timeout_s)
                except TypeError:
                    # Keep compatibility with an older controller supplied by a
                    # plugin while still allowing the normal app controller to
                    # synchronously drain its network workers during an update.
                    stopped = controller.close()
            complete = True if stopped is None else bool(stopped)
            if not complete:
                self._remember_deferred_manual_controller(controller)
            return complete
        except Exception as exc:
            self._remember_deferred_manual_controller(controller)
            logger.debug(
                "Failed to close manual translation controller: %s",
                safe_exception_summary(exc),
            )
            return False

    def _remember_deferred_manual_controller(self, controller: object) -> None:
        lock = self.__dict__.setdefault("_deferred_cleanup_lock", threading.RLock())
        with lock:
            deferred = self.__dict__.setdefault(
                "_deferred_manual_translation_controllers",
                [],
            )
            if all(existing is not controller for existing in deferred):
                deferred.append(controller)

    def _discard_ui_callbacks(self) -> None:
        """Release queued closures and their payloads after UI delivery is disabled."""

        for queue_name in ("_ui_callback_queue", "_ui_priority_callback_queue"):
            work_queue = getattr(self, queue_name, None)
            if work_queue is None:
                continue
            while True:
                try:
                    work_queue.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break

    def _close_independent_tool_windows(self) -> None:
        text_window = self._text_input_window
        self._text_input_window = None
        if text_window is not None:
            try:
                text_window.close()
                text_window.deleteLater()
            except Exception:
                logger.debug("Failed to close text input window during shutdown", exc_info=True)

        floating_window = self._floating_window
        self._floating_window = None
        if floating_window is not None:
            try:
                floating_window._on_close = None
                floating_window.close()
                floating_window.deleteLater()
            except Exception:
                logger.debug("Failed to close floating window during shutdown", exc_info=True)

        for windows_attr in ("_audio_diagnostics_windows", "_vad_calibration_windows"):
            windows = getattr(self, windows_attr, {})
            if isinstance(windows, dict):
                for tool_window in list(windows.values()):
                    try:
                        tool_window.close()
                        tool_window.deleteLater()
                    except Exception:
                        logger.debug("Failed to close tool window during shutdown", exc_info=True)
                windows.clear()
        mode_wizard = getattr(self, "_mode_wizard_dialog", None)
        self._mode_wizard_dialog = None
        if mode_wizard is not None:
            try:
                mode_wizard.close()
                mode_wizard.deleteLater()
            except Exception:
                logger.debug("Failed to close mode wizard during shutdown", exc_info=True)

        settings_window = self._settings_window
        self._settings_window = None
        if settings_window is not None:
            try:
                settings_window.close()
            except Exception:
                logger.debug("Failed to close settings window during shutdown", exc_info=True)

        for window_attr, label in (
            ("_guide_win", "OSC guide"),
            ("_sponsor_window", "sponsor window"),
            ("_tweaks_panel", "quick-switch panel"),
            ("_update_win", "update window"),
        ):
            tool_window = getattr(self, window_attr, None)
            setattr(self, window_attr, None)
            if tool_window is None:
                continue
            try:
                shutdown = getattr(tool_window, "shutdown", None)
                if window_attr == "_update_win" and callable(shutdown):
                    shutdown()
                else:
                    tool_window.close()
                if not bool(getattr(tool_window, "_downloading", False)):
                    tool_window.deleteLater()
            except Exception:
                logger.debug("Failed to close %s during shutdown", label, exc_info=True)

    # ----------------------------------------------------------------
    # UI Construction
    # ----------------------------------------------------------------

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("footerPanel")
        footer.setFixedHeight(FOOTER_HEIGHT)
        layout = QHBoxLayout(footer)
        vertical_margin = max(0, (FOOTER_HEIGHT - FOOTER_BUTTON_SIZE) // 2)
        layout.setContentsMargins(14, vertical_margin, 14, vertical_margin)
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
        self._sponsors_btn.setFixedSize(FOOTER_SPONSOR_BUTTON_WIDTH, FOOTER_BUTTON_SIZE)
        self._sponsors_btn.setIconSize(QSize(FOOTER_SPONSOR_ICON_SIZE, FOOTER_SPONSOR_ICON_SIZE))
        self._sponsors_btn.setIcon(ui_icon(ICON_SPONSOR_FILE, FOOTER_SPONSOR_ICON_SIZE, "#ffffff"))
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


    def _copy(self, key: str, **kwargs) -> str:
        if key in MAIN_COPY:
            ui_lang = getattr(self, "_ui_lang", None) or get_ui_language(
                getattr(self, "_config", {})
            )
            return translate_key_catalog(MAIN_COPY, ui_lang, key, **kwargs)
        return self._t(key, **kwargs)


    @staticmethod
    def _set_combo_text(combo: QComboBox, text: str) -> None:
        idx = combo.findText(text)
        if idx >= 0:
            blocked = combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(blocked)


    def _on_ui_lang_selected(self, selected_label: str) -> None:
        code = self._ui_lang_codes.get(selected_label)
        if not code or code == self._ui_lang:
            return
        self._apply_ui_language(code)

    def _apply_ui_language(self, language_code: str, *, persist: bool = True) -> None:
        code = normalize_ui_language(language_code)
        if code not in self._ui_lang_reverse:
            return
        install_qt_translations(QApplication.instance(), code)
        self._ui_lang = code
        self._config.setdefault("ui", {})["language"] = code
        self._refresh_static_texts()
        self._refresh_open_window_languages()
        if persist:
            self._schedule_config_save()

    def _refresh_open_window_languages(self) -> None:
        windows = [
            getattr(self, "_settings_window", None),
            getattr(self, "_floating_window", None),
            getattr(self, "_text_input_window", None),
            getattr(self, "_tweaks_panel", None),
            getattr(self, "_mode_wizard_dialog", None),
            getattr(self, "_sponsor_window", None),
            getattr(self, "_update_win", None),
        ]
        for collection_name in ("_audio_diagnostics_windows", "_vad_calibration_windows"):
            collection = getattr(self, collection_name, None)
            if isinstance(collection, dict):
                windows.extend(collection.values())
        for provider in (getattr(self, "_asr", None), getattr(self, "_listen_asr", None)):
            if provider is None:
                continue
            if callable(getattr(provider, "update_language", None)):
                windows.append(provider)
                continue
            browser_handle = getattr(provider, "_browser_handle", None)
            if browser_handle is not None:
                windows.append(browser_handle)
        for window in windows:
            update_language = getattr(window, "update_language", None)
            if not callable(update_language):
                continue
            try:
                update_language(self._ui_lang)
            except Exception:
                logger.debug(
                    "Failed to update %s language",
                    type(window).__name__,
                    exc_info=True,
                )
        self._refresh_osc_guide_language()

    def _on_tgt_lang_change(self, selected_label: str | None = None) -> None:
        if selected_label is None and self._tgt_lang_combo:
            selected_label = self._tgt_lang_combo.currentText()
        code = self._target_lang_codes.get(str(selected_label or ""), self._current_tgt_lang or "ja")
        self._current_tgt_lang = code
        translation = self._config.setdefault("translation", {})
        translation["target_language"] = code
        # A main-window language choice is an explicit player preference. If
        # this remains "auto", configuration normalization can replace it with
        # the UI-language default on the next load.
        translation["language_pair_source"] = "manual"
        self._refresh_language_combos()
        self._refresh_realtime_config_snapshot()
        self._schedule_config_save()

    def _on_src_lang_change(self, selected_label: str | None = None) -> None:
        if selected_label is None and self._src_lang_combo:
            selected_label = self._src_lang_combo.currentText()
        code = self._src_lang_codes.get(str(selected_label or ""), "auto")
        self._current_src_lang = None if code == "auto" else code
        self._current_asr_lang = code
        translation = self._config.setdefault("translation", {})
        translation["source_language"] = code
        translation["language_pair_source"] = "manual"
        if not getattr(self, "_refreshing_language_combos", False):
            self._refresh_language_combos()
            self._refresh_realtime_config_snapshot()
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
            translation = self._config.setdefault("translation", {})
            translation["source_language"] = tgt_code
            translation["target_language"] = src_code
            translation["language_pair_source"] = "manual"
            self._current_src_lang = tgt_code
            self._current_asr_lang = tgt_code
            self._current_tgt_lang = src_code
            self._refresh_language_combos()
            self._refresh_realtime_config_snapshot()
            self._set_source_text(self._last_tgt_text or self._src_text)
            self._show_tgt("")
            self._schedule_config_save()


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
        self._text_input_window.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
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
        desired = bool(muted)
        capture_error: Exception | None = None
        with self._asr_lifecycle_lock():
            changed = desired != bool(getattr(self, "_mic_muted", False))
            self._mic_muted = desired
            try:
                if changed and desired and getattr(self, "_running", False):
                    self._reset_streaming_state(MIC_SOURCE)
                    self._invalidate_realtime_source(MIC_SOURCE)
                self._apply_microphone_capture_mute_state()
            except Exception as exc:
                capture_error = exc
                logger.warning(
                    "Failed to %s microphone capture after mute state changed: %s",
                    "pause" if desired else "resume",
                    exc,
                )
        if changed:
            self._refresh_mic_mute_button()
            key = bottom_key or ("mic_mute_on" if desired else "mic_mute_off")
            self._set_bottom(self._copy(key))
            self._sync_avatar_muted_state(force=True)
            self._sync_avatar_speaking_state(force=True)
        if capture_error is not None:
            self._set_bottom(
                self._t(
                    "main_capture_pause_failed" if desired else "main_capture_resume_failed"
                ),
                "warning",
            )

    def _apply_microphone_capture_mute_state(self) -> None:
        """Keep the physical microphone stream aligned with Mio's mute state."""

        muted = bool(getattr(self, "_mic_muted", False))
        self._set_microphone_asr_capture_enabled(not muted)
        recorder = getattr(self, "_recorder", None)
        if muted:
            self._mic_capture_paused_for_mute = True
            if recorder is not None:
                logger.info("Pausing microphone capture because Mio is muted")
                self._stop_microphone_capture()
            return

        was_paused = bool(getattr(self, "_mic_capture_paused_for_mute", False))
        self._mic_capture_paused_for_mute = False
        if getattr(self, "_running", False) and recorder is None and was_paused:
            logger.info("Resuming microphone capture because Mio is unmuted")
            self._start_microphone_capture()

    def _set_microphone_asr_capture_enabled(self, enabled: bool) -> None:
        """Control providers such as WebSpeech that own a separate mic stream."""

        self._set_asr_provider_capture_enabled(getattr(self, "_asr", None), enabled)

    @staticmethod
    def _set_asr_provider_capture_enabled(provider: Any, enabled: bool) -> None:
        setter = getattr(provider, "set_capture_enabled", None)
        if not callable(setter):
            return
        try:
            setter(bool(enabled))
        except Exception as exc:
            logger.warning(
                "Failed to %s ASR-owned microphone capture: %s",
                "resume" if enabled else "pause",
                safe_exception_summary(exc),
            )

    def _cancel_pending_asr_requests(self, *, reason: str) -> None:
        """Interrupt cancellable provider calls before joining scheduler workers."""

        providers: list[Any] = []
        for provider in (
            getattr(self, "_asr", None),
            getattr(self, "_listen_asr", None),
        ):
            if provider is None or any(existing is provider for existing in providers):
                continue
            providers.append(provider)
        for provider in providers:
            cancel = getattr(provider, "cancel_pending_requests", None)
            if not callable(cancel):
                continue
            try:
                cancel()
                logger.info(
                    "Cancelled pending ASR provider requests provider=%s reason=%s",
                    getattr(provider, "provider_id", type(provider).__name__),
                    reason,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to cancel pending ASR provider requests "
                    "provider=%s reason=%s error=%s",
                    getattr(provider, "provider_id", type(provider).__name__),
                    reason,
                    safe_exception_summary(exc),
                )

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
        if mode is AppMode.SIMULTANEOUS:
            self._resolve_virtual_output_async()

    def _resolve_virtual_output_async(self) -> None:
        if self._destroying or bool(
            getattr(self, "_virtual_output_resolution_in_progress", False)
        ):
            return
        mode = getattr(self._mode_manager, "mode", AppMode.TRANSLATION)
        tts_cfg = self._config.get("tts", {})
        if (
            mode is not AppMode.SIMULTANEOUS
            or not isinstance(tts_cfg, dict)
            or not bool(tts_cfg.get("output_to_vrchat", False))
        ):
            return

        self._virtual_output_resolution_in_progress = True

        def run() -> None:
            try:
                resolved = find_best_virtual_output_device()
            except Exception:
                logger.debug("Failed to resolve virtual output device", exc_info=True)
                resolved = None
            self._call_in_ui(
                lambda value=resolved: self._apply_resolved_virtual_output(value)
            )

        threading.Thread(
            target=run,
            daemon=True,
            name="qt-virtual-output-resolver",
        ).start()

    def _apply_resolved_virtual_output(self, resolved: object) -> None:
        self._virtual_output_resolution_in_progress = False
        if self._destroying or resolved is None:
            return
        if getattr(self._mode_manager, "mode", AppMode.TRANSLATION) is not AppMode.SIMULTANEOUS:
            return
        tts_cfg = self._config.get("tts", {})
        if not isinstance(tts_cfg, dict) or not bool(tts_cfg.get("output_to_vrchat", False)):
            return
        try:
            device_id, device_name = resolved
        except (TypeError, ValueError):
            return
        changed = False
        if tts_cfg.get("output_device") != device_id:
            tts_cfg["output_device"] = device_id
            changed = True
        if tts_cfg.get("output_device_name") != device_name:
            tts_cfg["output_device_name"] = device_name
            changed = True
        if changed:
            self._reset_tts_manager()
            self._schedule_config_save()
            logger.info("Resolved virtual TTS output device after first render: %s", device_name)

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

    def _listen_asr_requires_restart_on_enable(self) -> bool:
        if not getattr(self, "_running", False):
            return False
        main_asr = getattr(self, "_asr", None)
        listen_asr = getattr(self, "_listen_asr", None)
        if main_asr is None or listen_asr is not main_asr:
            return False
        return bool(
            not _listen_asr_reuses_main(self._config)
            or _listen_asr_engine(self._config) == "qwen3-asr"
        )

    def _set_desktop_capture_enabled(self, enabled: bool, *, persist: bool) -> None:
        new_value = bool(enabled)
        if new_value and self._running:
            if self._listen_asr_requires_restart_on_enable():
                self._desktop_capture_enabled = True
                self._config.setdefault("vrc_listen", {})["enabled"] = True
                self._refresh_desktop_capture_button()
                self._refresh_floating_window_status(False)
                self._sync_settings_window_vrc_listen_state()
                if persist:
                    self._schedule_config_save()
                self._set_bottom(self._copy("desktop_audio_saved"))
                self._do_stop()
                self._schedule_pipeline_start_retry(100)
                return
            try:
                self._desktop_force_device_refresh = True
                self._start_listen()
            except Exception as exc:
                logger.warning("Desktop listen failed to start: %s", exc)
                self._desktop_capture_enabled = True
                self._listen_in_speech = False
                self._config.setdefault("vrc_listen", {})["enabled"] = True
                self._refresh_desktop_capture_button()
                self._refresh_floating_window_status(False)
                self._sync_settings_window_vrc_listen_state()
                self._set_bottom(self._t("main_desktop_listen_failed"), "warning")
                self._schedule_desktop_capture_recovery(str(exc))
                if persist:
                    self._schedule_config_save()
                return
        elif not new_value:
            recovery_timer = getattr(self, "_desktop_recovery_timer", None)
            if recovery_timer is not None:
                recovery_timer.stop()
            self._desktop_recovery_attempt = 0
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
        self._desktop_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
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
        self._listen_overlay_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
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
            service.error.connect(self._on_overlay_service_error)
            self._overlay_service = service
            self._ensure_output_dispatcher().register_sink("overlay", service.show_message)
        elif create_backend and getattr(self, "_floating_window", None) is None:
            service.set_backend(self._ensure_floating_window(), backend_name="desktop")
        service.set_enabled(bool(self._listen_overlay_enabled), reveal=False)
        return service

    def _on_overlay_service_error(self, message: str) -> None:
        logger.warning("Caption overlay failed: %s", message)
        self._set_bottom(self._t("main_overlay_error"), "warning")

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
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
            ui_language=self._ui_lang,
        )
        self._sponsor_window.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )
        self._sponsor_window.show()

    def _open_update_window(self) -> None:
        pending = getattr(self, "_pending_update", None)
        if not pending:
            return
        self._show_update_window(pending)

    def _show_update_window(self, update_info: UpdateInfo):
        """Show one application-owned update/repair dialog at a time."""

        existing = getattr(self, "_update_win", None)
        if existing is not None and not bool(getattr(existing, "_destroying", False)):
            try:
                existing._minimized = False
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return existing
            except RuntimeError:
                self._update_win = None
        from src.ui_qt.update_window import UpdateWindow

        dialog = UpdateWindow(self, update_info, self._ui_lang)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog_ref = weakref.ref(dialog)
        window_ref = weakref.ref(self)

        def clear_update_window(_result: int) -> None:
            window = window_ref()
            if window is not None and getattr(window, "_update_win", None) is dialog_ref():
                window._update_win = None

        dialog.finished.connect(clear_update_window)
        self._update_win = dialog
        dialog.show()
        return dialog

    def _check_for_update(self) -> None:
        if self._destroying:
            return
        if not self.isVisible():
            return

        window_ref = weakref.ref(self)

        def deliver_update(update_info: UpdateInfo) -> None:
            window = window_ref()
            if window is not None and not window._destroying:
                window._handle_update_available(update_info)

        def deliver_error() -> None:
            window = window_ref()
            if window is not None and not window._destroying:
                window._set_bottom(
                    window._t("main_update_check_failed"),
                    "warning",
                    key="main_update_check_failed",
                )

        def on_update(update_info: UpdateInfo | None) -> None:
            window = window_ref()
            if update_info is not None and window is not None and not window._destroying:
                window._call_in_ui(lambda info=update_info: deliver_update(info))

        def on_error(message: str) -> None:
            logger.warning("Qt update check failed: %s", message)
            window = window_ref()
            if window is None or window._destroying:
                return
            window._call_in_ui(deliver_error)

        try:
            check_for_update(on_update, on_error=on_error)
        except Exception:
            logger.debug("Qt update check failed", exc_info=True)
            self._set_bottom(
                self._t("main_update_check_failed"),
                "warning",
                key="main_update_check_failed",
            )

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
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._guide_win = dialog
        dialog.setObjectName("oscGuideDialog")
        dialog.setWindowTitle(self._t("guide_title"))
        dialog.setStyleSheet(self._base_stylesheet())
        dialog.setMinimumSize(520, 430)
        dialog.resize(560, 600)
        dialog.finished.connect(lambda _result: setattr(self, "_guide_win", None))
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("oscGuideContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        title = QLabel(self._t("guide_title"))
        title.setObjectName("sectionTitle")
        content_layout.addWidget(title)

        subtitle = QLabel(self._t("guide_subtitle"))
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        content_layout.addWidget(subtitle)

        step_widgets: list[tuple[QLabel, QLabel, QLabel]] = []
        for step_title, step_body, path in self._guide_pages():
            card = QFrame()
            card.setObjectName("guideStepCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)
            step_label = QLabel(step_title)
            step_label.setObjectName("sectionTitle")
            body_label = QLabel(step_body)
            body_label.setObjectName("mutedLabel")
            body_label.setWordWrap(True)
            path_label = QLabel("  >  ".join(path))
            path_label.setObjectName("statusPill")
            card_layout.addWidget(step_label)
            card_layout.addWidget(body_label)
            card_layout.addWidget(path_label)
            content_layout.addWidget(card)
            step_widgets.append((step_label, body_label, path_label))

        footer = QLabel(self._t("guide_footer"))
        footer.setObjectName("mutedLabel")
        footer.setWordWrap(True)
        content_layout.addWidget(footer)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        btn = QPushButton(self._t("guide_done"))
        btn.setObjectName("primaryButton")
        btn.clicked.connect(dialog.accept)
        layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
        dialog._mio_guide_widgets = (title, subtitle, step_widgets, footer, btn)
        dialog._mio_guide_scroll = scroll
        dialog.show()
        dialog.activateWindow()

    @staticmethod
    def _guide_page_specs() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        return (
            (
                "guide_step_1_title",
                "guide_step_1_body",
                ("guide_path_action_menu", "guide_path_options"),
            ),
            (
                "guide_step_2_title",
                "guide_step_2_body",
                ("guide_path_options", "OSC"),
            ),
            (
                "guide_step_3_title",
                "guide_step_3_body",
                ("OSC", "guide_path_enabled"),
            ),
        )

    def _guide_pages(self) -> list[tuple[str, str, list[str]]]:
        pages: list[tuple[str, str, list[str]]] = []
        for title_key, body_key, path_keys in self._guide_page_specs():
            path = [part if part == "OSC" else self._t(part) for part in path_keys]
            pages.append((self._t(title_key), self._t(body_key), path))
        return pages

    def _refresh_osc_guide_language(self) -> None:
        dialog = getattr(self, "_guide_win", None)
        widgets = getattr(dialog, "_mio_guide_widgets", None)
        if dialog is None or not widgets:
            return
        title, subtitle, step_widgets, footer, button = widgets
        dialog.setWindowTitle(self._t("guide_title"))
        title.setText(self._t("guide_title"))
        subtitle.setText(self._t("guide_subtitle"))
        for labels, page in zip(step_widgets, self._guide_pages()):
            step_label, body_label, path_label = labels
            step_title, step_body, path = page
            step_label.setText(step_title)
            body_label.setText(step_body)
            path_label.setText("  >  ".join(path))
        footer.setText(self._t("guide_footer"))
        button.setText(self._t("guide_done"))
        scroll = getattr(dialog, "_mio_guide_scroll", None)
        content = scroll.widget() if scroll is not None else None
        if content is not None:
            content.updateGeometry()
            if content.layout() is not None:
                content.layout().activate()

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
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        btn.setFixedSize(FOOTER_BUTTON_SIZE, FOOTER_BUTTON_SIZE)
        btn.setIconSize(QSize(FOOTER_ICON_SIZE, FOOTER_ICON_SIZE))
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
        icon = ui_icon(icon_name, FOOTER_ICON_SIZE, color)
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
        if (
            self._start_btn is None
            or self._destroying
            or self._running
            or getattr(self, "_update_install_preparing", False)
            or getattr(self, "_update_install_prepared", False)
        ):
            return
        if self._pipeline_cleanup_in_progress():
            self._start_btn.setEnabled(False)
            self._start_btn.setText(self._t("starting"))
            self._set_status(self._t("starting"), "accent", key="starting")
            self._schedule_pipeline_start_retry(100)
            return
        retry_timer = getattr(self, "_pipeline_start_retry_timer", None)
        if retry_timer is not None and retry_timer.isActive():
            retry_timer.stop()
        if self._prompt_for_missing_credential(("translation", "asr", "tts")):
            self._set_status(self._t("status_error"), "danger", key="status_error")
            return
        self._start_btn.setEnabled(False)
        self._start_btn.setText(self._t("starting"))
        self._set_status(self._t("starting"), "accent", key="starting")
        self._listen_session += 1
        self._reset_streaming_state()
        self._reset_translation_failure_backoff()
        cancel_event = threading.Event()
        self._startup_cancel_event = cancel_event
        session = self._listen_session
        try:
            # OscService is a QObject parented to MainWindow. Keep its
            # construction/signal wiring on the Qt thread; the socket setup is
            # lightweight and must not be moved into the pipeline worker.
            self._ensure_sender()
        except Exception as exc:
            self._cleanup_startup_failure(
                str(exc),
                show_error=True,
                session_id=session,
                cancel_event=cancel_event,
            )
            return

        def run() -> None:
            try:
                self._init_pipeline(session, cancel_event)
            except _StartupCancelled:
                self._call_in_ui(
                    lambda sid=session, event=cancel_event: self._cleanup_startup_failure(
                        show_error=False,
                        session_id=sid,
                        cancel_event=event,
                    )
                )
            except Exception as exc:
                self._call_in_ui(
                    lambda msg=str(exc), sid=session, event=cancel_event: self._cleanup_startup_failure(
                        msg,
                        show_error=True,
                        session_id=sid,
                        cancel_event=event,
                    )
                )
            finally:
                current = threading.current_thread()
                self._call_in_ui(lambda thread=current: self._clear_startup_thread(thread))

        startup_thread = threading.Thread(
            target=run,
            daemon=True,
            name="pipeline-startup",
        )
        self._startup_thread = startup_thread
        startup_thread.start()

    def _pipeline_cleanup_in_progress(self) -> bool:
        """Prevent a replacement model load while the previous runtime still owns memory."""

        asr_cleanup = False
        lock = self._asr_lifecycle_lock()
        with lock:
            startup_thread = getattr(self, "_startup_thread", None)
            if startup_thread is not None:
                try:
                    startup_alive = startup_thread.is_alive()
                except Exception:
                    startup_alive = True
                if not startup_alive:
                    if self._startup_thread is startup_thread:
                        self._startup_thread = None
                else:
                    asr_cleanup = True

            cleanup_threads = self.__dict__.setdefault("_asr_cleanup_threads", [])
            alive_cleanup: list[threading.Thread] = []
            for thread in cleanup_threads:
                try:
                    if thread.is_alive():
                        alive_cleanup.append(thread)
                except Exception:
                    alive_cleanup.append(thread)
            cleanup_threads[:] = alive_cleanup
            asr_cleanup = asr_cleanup or bool(
                alive_cleanup
                or self.__dict__.setdefault("_closing_asr_providers", [])
            )
        return bool(asr_cleanup)

    def _runtime_cleanup_in_progress(self) -> bool:
        """Report all deferred resources that must finish before process handoff."""

        return bool(
            self._pipeline_cleanup_in_progress()
            or self._provider_prewarm_cleanup_in_progress()
            or self._deferred_tts_cleanup_in_progress()
            or self._deferred_manual_cleanup_in_progress()
            or provider_background_work_in_progress()
        )

    def _provider_prewarm_cleanup_in_progress(self) -> bool:
        """Track startup prewarm threads and retained provider ownership."""

        asr_pending = False
        asr_lock = self._asr_prewarm_lifecycle_lock()
        with asr_lock:
            asr_thread = getattr(self, "_asr_prewarm_thread", None)
            if asr_thread is not None:
                try:
                    asr_pending = asr_thread.ident is None or asr_thread.is_alive()
                except Exception:
                    asr_pending = True
                if not asr_pending and self._asr_prewarm_thread is asr_thread:
                    self._asr_prewarm_thread = None
            retained_asr = bool(
                getattr(self, "_prewarmed_main_asr", None) is not None
                or getattr(self, "_prewarmed_listen_asr", None) is not None
            )

        tts_pending = False
        tts_lock = self._tts_prewarm_lifecycle_lock()
        with tts_lock:
            tts_thread = getattr(self, "_tts_prewarm_thread", None)
            if tts_thread is not None:
                try:
                    tts_pending = tts_thread.ident is None or tts_thread.is_alive()
                except Exception:
                    tts_pending = True
                if not tts_pending and self._tts_prewarm_thread is tts_thread:
                    self._tts_prewarm_thread = None

        translation_pending = False
        translation_lock = self._translation_prewarm_lifecycle_lock()
        with translation_lock:
            tracked_translation_threads = getattr(
                self,
                "_translation_prewarm_threads",
                set(),
            )
            alive_translation_threads: set[threading.Thread] = set()
            for tracked_thread in tracked_translation_threads:
                try:
                    if tracked_thread.ident is None or tracked_thread.is_alive():
                        alive_translation_threads.add(tracked_thread)
                except Exception:
                    alive_translation_threads.add(tracked_thread)
            self._translation_prewarm_threads = alive_translation_threads
            translation_thread = getattr(self, "_translation_prewarm_thread", None)
            if translation_thread is not None:
                try:
                    translation_pending = (
                        translation_thread.ident is None
                        or translation_thread.is_alive()
                    )
                except Exception:
                    translation_pending = True
                if (
                    not translation_pending
                    and self._translation_prewarm_thread is translation_thread
                ):
                    self._translation_prewarm_thread = None
            translation_pending = bool(
                translation_pending or alive_translation_threads
            )
            translation_pools = getattr(
                self,
                "_prewarmed_realtime_translators",
                {},
            )
            retained_translation = bool(
                isinstance(translation_pools, Mapping)
                and any(values for values in translation_pools.values())
            )

        return bool(
            asr_pending
            or retained_asr
            or tts_pending
            or translation_pending
            or retained_translation
        )

    def _deferred_tts_cleanup_in_progress(self) -> bool:
        lock = self.__dict__.setdefault("_deferred_cleanup_lock", threading.RLock())
        with lock:
            managers = tuple(self.__dict__.setdefault("_deferred_tts_managers", []))
        remaining: list[object] = []
        for manager in managers:
            complete = False
            try:
                getter = getattr(manager, "get_quiescence_state", None)
                if callable(getter):
                    state = getter()
                else:
                    close = getattr(manager, "close", None)
                    if not callable(close):
                        complete = True
                        state = None
                    else:
                        try:
                            state = close(timeout_seconds=0.0)
                        except TypeError:
                            state = close()
                if state is not None:
                    complete = bool(
                        getattr(state, "quiescent", state)
                    ) and not bool(
                        getattr(state, "engine_close_deferred", False)
                    )
            except Exception as exc:
                logger.debug(
                    "Failed to poll deferred TTS cleanup: %s",
                    safe_exception_summary(exc),
                )
            if not complete:
                remaining.append(manager)
        with lock:
            deferred = self.__dict__.setdefault("_deferred_tts_managers", [])
            newly_added = [
                manager
                for manager in deferred
                if all(manager is not existing for existing in managers)
            ]
            deferred[:] = remaining + newly_added
            return bool(deferred)

    def _deferred_manual_cleanup_in_progress(self) -> bool:
        lock = self.__dict__.setdefault("_deferred_cleanup_lock", threading.RLock())
        with lock:
            controllers = tuple(
                self.__dict__.setdefault(
                    "_deferred_manual_translation_controllers",
                    [],
                )
            )
        remaining: list[object] = []
        for controller in controllers:
            try:
                try:
                    state = controller.close(wait_timeout_s=0.0)
                except TypeError:
                    state = controller.close()
                complete = True if state is None else bool(state)
            except Exception as exc:
                complete = False
                logger.debug(
                    "Failed to poll deferred manual cleanup: %s",
                    safe_exception_summary(exc),
                )
            if not complete:
                remaining.append(controller)
        with lock:
            deferred = self.__dict__.setdefault(
                "_deferred_manual_translation_controllers",
                [],
            )
            newly_added = [
                controller
                for controller in deferred
                if all(controller is not existing for existing in controllers)
            ]
            deferred[:] = remaining + newly_added
            return bool(deferred)

    def _schedule_pipeline_start_retry(self, delay_ms: int) -> None:
        if self._destroying:
            return
        timer = getattr(self, "_pipeline_start_retry_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._do_start)
            self._pipeline_start_retry_timer = timer
        if not timer.isActive():
            timer.start(max(25, int(delay_ms)))

    def _clear_startup_thread(self, thread: threading.Thread) -> None:
        if self._startup_thread is thread:
            self._startup_thread = None

    def _init_pipeline(
        self,
        session_id: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._raise_if_cancelled(session_id, cancel_event)
        mic_asr = None
        listen_asr = None
        installed = False
        try:
            prewarmed_main, prewarmed_listen = self._take_prewarmed_asr_pair(
                self._config
            )
            mic_asr, listen_asr = _create_asr_pair(
                self._config,
                prewarmed_main=prewarmed_main,
                prewarmed_listen=prewarmed_listen,
            )
            self._set_asr_provider_capture_enabled(
                mic_asr,
                not bool(getattr(self, "_mic_muted", False)),
            )
            mic_asr.load(
                progress_callback=lambda event: self._call_in_ui(
                    lambda e=event, sid=session_id: (
                        self._handle_model_progress(e)
                        if sid == self._listen_session and not self._destroying
                        else None
                    )
                )
            )
            if listen_asr is not mic_asr:
                listen_asr.load(
                    progress_callback=lambda event: self._call_in_ui(
                        lambda e=event, sid=session_id: (
                            self._handle_model_progress(e)
                            if sid == self._listen_session and not self._destroying
                            else None
                        )
                    )
                )
            self._raise_if_cancelled(session_id, cancel_event)

            with self._asr_lifecycle_lock():
                self._raise_if_cancelled(session_id, cancel_event)
                self._asr = mic_asr
                self._listen_asr = listen_asr
                installed = True
                self._refresh_asr_transcribe_locks()
                self._set_microphone_asr_capture_enabled(
                    not bool(getattr(self, "_mic_muted", False))
                )

                # Commit all runtime resources under the same lifecycle lock used
                # by stop/shutdown.  This prevents a cancelled startup from
                # creating capture or worker resources after shutdown has already
                # attempted to tear them down.
                self._raise_if_cancelled(session_id, cancel_event)
                self._start_workers()
                self._start_microphone_capture()
                self._raise_if_cancelled(session_id, cancel_event)
                self._running = True
                if self._desktop_capture_enabled:
                    try:
                        self._desktop_force_device_refresh = True
                        self._start_listen()
                    except Exception as exc:
                        logger.warning("Desktop listen did not start: %s", exc)
                        self._call_in_ui(
                            lambda sid=session_id: (
                                self._set_bottom(
                                    self._t("main_desktop_listen_failed"),
                                    "warning",
                                )
                                if self._running and sid == self._listen_session
                                else None
                            )
                        )
                        self._call_in_ui(
                            lambda sid=session_id: (
                                self._refresh_desktop_capture_button()
                                if self._running and sid == self._listen_session
                                else None
                            )
                        )
                        self._call_in_ui(
                            lambda sid=session_id, detail=str(exc): (
                                self._schedule_desktop_capture_recovery(detail)
                                if self._running and sid == self._listen_session
                                else None
                            )
                        )
            self._call_in_ui(
                lambda sid=session_id: (
                    self._on_started()
                    if self._running and sid == self._listen_session and not self._destroying
                    else None
                )
            )
        except Exception:
            if installed:
                self._running = False
                self._stop_listen()
                self._stop_microphone_capture()
                shutdown_barrier = self._stop_workers()
                self._close_asr_providers(wait_for=shutdown_barrier)
            else:
                self._close_asr_providers(mic_asr, listen_asr)
            raise

    def _raise_if_cancelled(
        self,
        session_id: int,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _StartupCancelled()
        if (
            self._destroying
            or session_id != self._listen_session
            or (
                cancel_event is not None
                and cancel_event is not self._startup_cancel_event
            )
        ):
            raise _StartupCancelled()

    def _cleanup_startup_failure(
        self,
        msg: str = "",
        *,
        show_error: bool,
        session_id: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        with self._asr_lifecycle_lock():
            if session_id is not None and session_id != self._listen_session:
                return
            if cancel_event is not None and cancel_event is not self._startup_cancel_event:
                return
            self._running = False
        self._reset_streaming_state()
        self._stop_listen()
        self._stop_microphone_capture()
        shutdown_barrier = self._stop_workers()
        self._close_osc_sender()
        self._close_asr_providers(wait_for=shutdown_barrier)
        self._refresh_start_button()
        if show_error:
            self._on_start_error(msg)
        else:
            self._set_status(self._t("status_ready"), "success", key="status_ready")

    def _do_stop(self) -> threading.Event | None:
        with self._asr_lifecycle_lock():
            startup_cancel_event = getattr(self, "_startup_cancel_event", None)
            if startup_cancel_event is not None:
                startup_cancel_event.set()
            self._listen_session = getattr(self, "_listen_session", 0) + 1
            self._running = False
        self._reset_streaming_state()
        self._reset_translation_failure_backoff()
        self._reset_avatar_params()

        # Capture must stop producing work before the staged scheduler is
        # cancelled.  ASR providers are closed only after all owned workers have
        # been asked to exit.
        self._stop_listen()
        self._stop_microphone_capture()
        shutdown_barrier = self._stop_workers()
        self._close_asr_providers(wait_for=shutdown_barrier)
        self._close_osc_sender()

        self._refresh_start_button()
        self._set_status(self._t("status_ready"), "success", key="status_ready")
        return shutdown_barrier

    def _asr_lifecycle_lock(self):
        lock = self.__dict__.get("_asr_close_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_asr_close_lock", threading.RLock())
        return lock

    def _start_asr_cleanup_thread(
        self,
        target: Callable[[], None],
        *,
        name: str,
    ) -> threading.Thread:
        lock = self._asr_lifecycle_lock()
        thread: threading.Thread

        def run() -> None:
            try:
                target()
            except Exception as exc:
                logger.error(
                    "Deferred ASR cleanup failed: %s",
                    safe_exception_summary(exc),
                )
            finally:
                current = threading.current_thread()
                with lock:
                    threads = self.__dict__.setdefault("_asr_cleanup_threads", [])
                    threads[:] = [item for item in threads if item is not current]

        thread = threading.Thread(target=run, daemon=True, name=name)
        with lock:
            threads = self.__dict__.setdefault("_asr_cleanup_threads", [])
            threads[:] = [item for item in threads if item.is_alive()]
            threads.append(thread)
            try:
                thread.start()
            except Exception:
                threads.remove(thread)
                raise
        return thread

    def _close_asr_providers(
        self,
        *providers: Any,
        wait_for: threading.Event | None = None,
    ) -> None:
        lock = self._asr_lifecycle_lock()
        with lock:
            current_asr = getattr(self, "_asr", None)
            current_listen_asr = getattr(self, "_listen_asr", None)
            candidates = providers or (current_asr, current_listen_asr)
            unique_candidates: list[Any] = []
            for provider in candidates:
                if provider is None or any(
                    existing is provider for existing in unique_candidates
                ):
                    continue
                unique_candidates.append(provider)

            closing = self.__dict__.setdefault("_closing_asr_providers", [])
            closed_refs = self.__dict__.setdefault("_closed_asr_provider_refs", [])
            closed_refs[:] = [
                reference for reference in closed_refs if reference() is not None
            ]
            to_close: list[Any] = []
            for provider in unique_candidates:
                if any(existing is provider for existing in closing) or any(
                    reference() is provider for reference in closed_refs
                ):
                    continue
                closing.append(provider)
                to_close.append(provider)

            if any(provider is current_asr for provider in unique_candidates):
                self._asr = None
            if any(provider is current_listen_asr for provider in unique_candidates):
                self._listen_asr = None
            self._refresh_asr_transcribe_locks()

        if not to_close:
            return

        def close_reserved() -> None:
            for provider in to_close:
                try:
                    provider.close()
                except Exception as exc:
                    logger.debug(
                        "Failed to close ASR provider: %s",
                        safe_exception_summary(exc),
                    )
                finally:
                    with lock:
                        closing = self.__dict__.setdefault(
                            "_closing_asr_providers",
                            [],
                        )
                        closing[:] = [
                            existing
                            for existing in closing
                            if existing is not provider
                        ]
                        try:
                            reference = weakref.ref(provider)
                        except TypeError:
                            # Some native extension objects do not support weak
                            # references. Do not retain a closed model merely to
                            # suppress a hypothetical duplicate close call.
                            continue
                        closed_refs = self.__dict__.setdefault(
                            "_closed_asr_provider_refs",
                            [],
                        )
                        if not any(
                            existing() is provider for existing in closed_refs
                        ):
                            closed_refs.append(reference)

        if wait_for is None or wait_for.is_set():
            close_reserved()
            return

        def close_after_workers() -> None:
            wait_for.wait()
            close_reserved()

        self._start_asr_cleanup_thread(
            close_after_workers,
            name="asr-provider-cleanup",
        )

    def _start_workers(self) -> None:
        self._realtime_delivery_cancel_event = threading.Event()
        context_store = getattr(self, "_translation_context_store", None)
        if isinstance(context_store, TranslationContextStore):
            context_store.clear()
        else:
            self._translation_context_store = TranslationContextStore()
        self._partial_task_queues = {}
        self._partial_workers = {}
        self._final_task_queues = {}
        self._final_workers = {}
        self._realtime_source_generations = {
            MIC_SOURCE: 0,
            DESKTOP_SOURCE: 0,
        }
        self._realtime_config_snapshot = _freeze_snapshot_value(copy.deepcopy(self._config))

        # Partial recognition is disposable UI feedback, not a pipeline stage.
        # Only create its queue/thread when the active microphone backend can
        # actually produce useful partials.  This avoids continuously slicing
        # and copying audio for CPU and online backends that immediately reject
        # partial work, and prevents desktop-listen partials from competing with
        # final microphone recognition for the same local model.
        for source in (MIC_SOURCE, DESKTOP_SOURCE):
            if not self._should_process_partial_asr(source):
                continue
            self._partial_task_queues[source] = queue.Queue(maxsize=PARTIAL_TASK_QUEUE_MAXSIZE)
            worker = threading.Thread(
                target=self._partial_worker_loop,
                args=(source,),
                daemon=True,
                name=f"partial-{source}",
            )
            worker.start()
            self._partial_workers[source] = worker

        translation_concurrency = self._realtime_translation_worker_concurrency()
        reserve_reverse_translation = (
            self._reverse_translation_capacity_enabled()
            and translation_concurrency > 1
        )
        logger.info(
            "Realtime translation scheduling configured workers=%d "
            "reverse_reserved=%s",
            translation_concurrency,
            reserve_reverse_translation,
        )
        scheduler = RealtimeScheduler(
            sources=(MIC_SOURCE, DESKTOP_SOURCE),
            asr_handler=self._scheduler_asr_stage,
            rewrite_handler=self._scheduler_rewrite_stage,
            rewrite_required=self._scheduler_rewrite_required,
            translation_handler=self._scheduler_translation_stage,
            delivery_handler=self._scheduler_delivery_stage,
            rewrite_state_factory=self._create_realtime_rewrite_worker_state,
            rewrite_state_finalizer=lambda state: state.close(),
            translation_state_factory=self._create_realtime_translation_worker_state,
            translation_state_finalizer=lambda state: state.close(),
            ingress_limits={
                MIC_SOURCE: FINAL_TASK_QUEUE_MAXSIZE,
                DESKTOP_SOURCE: DESKTOP_FINAL_TASK_QUEUE_MAXSIZE,
            },
            outstanding_limits={
                MIC_SOURCE: 12,
                DESKTOP_SOURCE: 12,
            },
            rewrite_queue_size=ASR_REWRITE_TASK_QUEUE_MAXSIZE,
            translation_queue_size=TRANSLATION_TASK_QUEUE_MAXSIZE,
            asr_concurrency=self._realtime_asr_worker_concurrency(),
            rewrite_concurrency=self._realtime_rewrite_worker_concurrency(),
            translation_concurrency=translation_concurrency,
            priority_source=DESKTOP_SOURCE,
            priority_burst=MIC_PRIORITY_BURST,
            reserve_priority_translation_capacity=reserve_reverse_translation,
            max_asr_queue_age_s={
                MIC_SOURCE: MAX_REALTIME_ASR_QUEUE_AGE_S,
                DESKTOP_SOURCE: MAX_REVERSE_ASR_QUEUE_AGE_S,
            },
            max_translation_queue_age_s={
                DESKTOP_SOURCE: MAX_REVERSE_TRANSLATION_QUEUE_AGE_S,
            },
            thread_name_prefix=f"realtime-{self._listen_session}",
        )
        self._realtime_scheduler = scheduler
        try:
            scheduler.start()
        except Exception:
            self._realtime_scheduler = None
            for work_queue in self._partial_task_queues.values():
                self._enqueue_latest(work_queue, None)
            raise

    def _stop_workers(self) -> threading.Event | None:
        delivery_cancel_event = getattr(
            self,
            "_realtime_delivery_cancel_event",
            None,
        )
        if delivery_cancel_event is not None:
            delivery_cancel_event.set()

        context_store = getattr(self, "_translation_context_store", None)
        if isinstance(context_store, TranslationContextStore):
            context_store.clear()

        tts_manager = getattr(self, "_tts_manager", None)
        if tts_manager is not None:
            try:
                clear_queue = getattr(tts_manager, "clear_queue", None)
                if callable(clear_queue):
                    clear_queue()
                stop_playback = getattr(tts_manager, "stop_playback", None)
                if callable(stop_playback):
                    stop_playback()
            except Exception as exc:
                logger.debug(
                    "Failed to cancel stale TTS session work: %s",
                    safe_exception_summary(exc),
                )

        osc_sender = getattr(self, "_sender", None)
        if osc_sender is None:
            osc_sender = getattr(self, "_osc_sender", None)
        if osc_sender is not None:
            try:
                clear_pending = getattr(osc_sender, "clear_pending_chatbox", None)
                if callable(clear_pending):
                    clear_pending()
            except Exception:
                logger.debug("Failed to cancel stale OSC session work", exc_info=True)

        priority_queue = getattr(self, "_ui_priority_callback_queue", None)
        ui_thread_id = getattr(self, "_ui_thread_id", threading.get_ident())
        if (
            priority_queue is not None
            and threading.get_ident() == ui_thread_id
        ):
            while True:
                try:
                    _delay_ms, callback = priority_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception:
                    logger.debug(
                        "Failed to cancel pending realtime UI delivery",
                        exc_info=True,
                    )

        partial_queues = tuple(
            getattr(self, "_partial_task_queues", {}).values()
        )
        partial_workers = tuple(
            getattr(self, "_partial_workers", {}).values()
        )
        scheduler = getattr(self, "_realtime_scheduler", None)

        # Detach owned runtime objects first so no new callback can discover and
        # enqueue into a scheduler or partial queue that is being cancelled.
        self._realtime_scheduler = None
        self._partial_workers = {}
        self._final_workers = {}
        self._partial_task_queues = {}
        self._final_task_queues = {}

        for work_queue in partial_queues:
            self._enqueue_latest(work_queue, None)

        self._cancel_pending_asr_requests(reason="realtime-worker-stop")

        deadline = time.monotonic() + WORKER_STOP_TIMEOUT_S
        scheduler_stopped = True
        if scheduler is not None:
            try:
                result = scheduler.stop(timeout=WORKER_STOP_TIMEOUT_S)
                scheduler_stopped = result is not False
            except Exception as exc:
                scheduler_stopped = False
                logger.error(
                    "Realtime scheduler stop failed: %s",
                    safe_exception_summary(exc),
                )

        current = threading.current_thread()
        for worker in partial_workers:
            join = getattr(worker, "join", None)
            if callable(join) and worker is not current:
                try:
                    join(timeout=max(0.0, deadline - time.monotonic()))
                except Exception:
                    logger.debug("Failed to join partial ASR worker", exc_info=True)

        def alive_threads(items: tuple[Any, ...]) -> tuple[Any, ...]:
            alive: list[Any] = []
            for item in items:
                is_alive = getattr(item, "is_alive", None)
                try:
                    if callable(is_alive) and is_alive():
                        alive.append(item)
                except Exception:
                    alive.append(item)
            return tuple(alive)

        partial_alive = alive_threads(partial_workers)
        scheduler_threads = tuple(getattr(scheduler, "threads", ())) if scheduler is not None else ()
        scheduler_alive = alive_threads(scheduler_threads)
        if scheduler_stopped and not scheduler_alive and not partial_alive:
            return None

        names = [
            str(getattr(worker, "name", type(worker).__name__))
            for worker in (*scheduler_alive, *partial_alive)
        ]
        logger.warning(
            "Realtime workers did not stop in time; deferring ASR provider cleanup%s",
            f": {', '.join(names)}" if names else "",
        )
        shutdown_barrier = threading.Event()

        def finish_shutdown() -> None:
            safe_to_close = scheduler is None
            if scheduler is not None:
                try:
                    result = scheduler.stop(timeout=None)
                    safe_to_close = result is not False
                except Exception as exc:
                    safe_to_close = False
                    logger.error(
                        "Deferred realtime scheduler stop failed: %s",
                        safe_exception_summary(exc),
                    )

            for worker in partial_workers:
                join = getattr(worker, "join", None)
                if callable(join) and worker is not threading.current_thread():
                    try:
                        join()
                    except Exception:
                        logger.debug(
                            "Deferred partial ASR worker join failed",
                            exc_info=True,
                        )

            remaining_scheduler = alive_threads(
                tuple(getattr(scheduler, "threads", ())) if scheduler is not None else ()
            )
            remaining_partial = alive_threads(partial_workers)
            if safe_to_close and not remaining_scheduler and not remaining_partial:
                shutdown_barrier.set()
                return
            logger.error(
                "ASR providers retained because realtime workers failed to terminate"
            )

        self._start_asr_cleanup_thread(
            finish_shutdown,
            name="realtime-worker-cleanup",
        )
        return shutdown_barrier

    @staticmethod
    def _drain_queue(work_queue: queue.Queue) -> None:
        while True:
            try:
                work_queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _enqueue_latest(work_queue: queue.Queue, payload) -> str:
        """Latest-only admission used exclusively by disposable partial ASR."""

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
        work_queue = self._partial_task_queues.get(source)
        if work_queue is None:
            return
        while True:
            payload = work_queue.get()
            if payload is None:
                return
            audio, asr_lang, generation, session_id, src = payload
            self._process_partial_audio_chunk(audio, asr_lang, generation, session_id, src)

    def _final_worker_loop(self, source: str) -> None:
        """Legacy synchronous queue consumer retained for API compatibility."""

        work_queue = self._final_task_queues.get(source)
        if work_queue is None:
            return
        while True:
            payload = work_queue.get()
            if payload is None:
                return
            audio, asr_lang, selected_src_lang, session_id, src = payload
            self._process_final_audio_segment(audio, asr_lang, selected_src_lang, session_id, src)

    # ----------------------------------------------------------------
    # Audio capture
    # ----------------------------------------------------------------
    def _start_microphone_capture(self) -> None:
        if bool(getattr(self, "_mic_muted", False)):
            self._mic_capture_paused_for_mute = True
            logger.info("Microphone capture remains paused while Mio is muted")
            return
        device_name = self._resolve_mic_input_device_name(refresh=True)
        matched_device_name = self._match_mic_input_device_name(device_name)
        if matched_device_name:
            device_name = matched_device_name
        dev_idx = self._devices.get(device_name)
        from src.audio.recorder import AudioRecorder
        audio_cfg = self._config.get("audio", {})
        if not isinstance(audio_cfg, dict):
            audio_cfg = {}
        configured_device_name = str(audio_cfg.get("input_device") or "").strip()
        if (
            self._mic_input_device_mode() != "auto"
            and configured_device_name
            and dev_idx is None
        ):
            logger.warning(
                "Configured microphone is unavailable; using the system default "
                "until it becomes available (configured=%s)",
                configured_device_name,
            )
        streaming_cfg = self._asr_streaming_settings()
        partial_callback = (
            (lambda audio: self._on_audio_chunk(audio, MIC_SOURCE))
            if MIC_SOURCE in getattr(self, "_partial_task_queues", {})
            else None
        )

        self._recorder = AudioRecorder(
            on_segment=self._on_audio_segment,
            on_chunk=partial_callback,
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            frame_duration_ms=int(audio_cfg.get("frame_duration_ms", 30)),
            vad_sensitivity=int(audio_cfg.get("vad_sensitivity", 2)),
            silence_threshold_s=self._effective_mic_tail_silence_s(audio_cfg),
            vad_speech_ratio=float(audio_cfg.get("vad_speech_ratio", 0.6)),
            vad_activation_threshold_s=float(audio_cfg.get("vad_activation_threshold_s", 0.2)),
            vad_min_rms=float(audio_cfg.get("vad_min_rms", 0.012)),
            min_segment_s=float(audio_cfg.get("min_segment_s", 0.45)),
            partial_min_speech_s=float(audio_cfg.get("partial_min_speech_s", 0.45)),
            max_segment_s=self._effective_mic_max_segment_s(audio_cfg),
            denoise_strength=float(audio_cfg.get("denoise_strength", 0.0)),
            chunk_interval_ms=streaming_cfg["chunk_interval_ms"],
            chunk_window_s=streaming_cfg["chunk_window_s"],
            ring_buffer_s=streaming_cfg["ring_buffer_s"],
            recent_speech_hold_s=streaming_cfg["recent_speech_hold_s"],
            input_device=dev_idx,
            on_vad_state=lambda active: self._call_in_ui(
                lambda state=active: self._handle_mic_vad_state(state)
            ),
        )
        self._recorder.start()
        self._mic_capture_paused_for_mute = False
        self._active_mic_input_device_name = self._recorder.active_input_device_name or device_name
        self._last_mic_started_at = time.monotonic()
        self._last_mic_result_at = self._last_mic_started_at
        self._last_mic_diagnostic_log_at = 0.0
        logger.info("Microphone capture started: %s", self._active_mic_input_device_name)

    def _effective_mic_tail_silence_s(self, audio_cfg: Mapping[str, Any]) -> float:
        try:
            configured = max(
                0.2,
                float(audio_cfg.get("vad_silence_threshold", 0.65)),
            )
        except (TypeError, ValueError):
            configured = 0.65
        if AppMode.from_value(self._config.get("app_mode")) is not AppMode.SIMULTANEOUS:
            return configured
        simul_cfg = self._config.get("simul_mode", {})
        if not isinstance(simul_cfg, Mapping):
            simul_cfg = {}
        try:
            target = max(
                0.2,
                min(float(simul_cfg.get("vad_silence_ms", 300)) / 1000.0, 1.5),
            )
        except (TypeError, ValueError):
            target = 0.3
        if bool(simul_cfg.get("aggressive_chunking", False)):
            target = min(target, 0.22)
        return min(configured, target)

    def _effective_mic_max_segment_s(self, audio_cfg: Mapping[str, Any]) -> float:
        try:
            configured = max(0.5, float(audio_cfg.get("max_segment_s", 6.0)))
        except (TypeError, ValueError):
            configured = 6.0
        if AppMode.from_value(self._config.get("app_mode")) is not AppMode.SIMULTANEOUS:
            return configured
        simul_cfg = self._config.get("simul_mode", {})
        aggressive = bool(
            isinstance(simul_cfg, Mapping)
            and simul_cfg.get("aggressive_chunking", False)
        )
        return min(configured, 2.5 if aggressive else 4.0)

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
                force_refresh = bool(
                    getattr(self, "_desktop_force_device_refresh", False)
                )
                self._desktop_force_device_refresh = False
                self._load_desktop_devices(force_refresh=force_refresh)
            device_name = self._desktop_output_device_name()
            if not device_name:
                raise RuntimeError(self._copy("vrc_listen_device_missing"))
        except Exception as exc:
            try:
                from src.audio.desktop_recorder import loopback_device_diagnostics

                diagnostics = loopback_device_diagnostics()
            except Exception:
                diagnostics = {"diagnostics_error": "unavailable"}
            logger.error(
                "Desktop listen output-device resolution failed: %s diagnostics=%s",
                exc,
                diagnostics,
            )
            raise RuntimeError(self._copy("vrc_listen_device_missing")) from exc
        audio_cfg = self._config.get("audio", {}) if isinstance(self._config.get("audio", {}), dict) else {}
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        segment_duration_s = self._listen_segment_duration_s()
        chunk_interval_ms = min(max(int(round(segment_duration_s * 500.0)), 700), 1400)
        streaming_cfg = self._asr_streaming_settings()
        partial_callback = (
            (lambda audio: self._on_audio_chunk(audio, DESKTOP_SOURCE))
            if DESKTOP_SOURCE in getattr(self, "_partial_task_queues", {})
            else None
        )
        from src.audio.desktop_recorder import DesktopAudioRecorder

        self._listen_recorder = DesktopAudioRecorder(
            on_segment=lambda audio: self._on_audio_segment(audio, DESKTOP_SOURCE),
            on_chunk=partial_callback,
            sample_rate=int(audio_cfg.get("sample_rate", 16000)),
            frame_duration_ms=int(audio_cfg.get("frame_duration_ms", 30)),
            vad_sensitivity=int(audio_cfg.get("vad_sensitivity", 1)),
            silence_threshold_s=self._listen_tail_silence_s(),
            vad_speech_ratio=float(listen_cfg.get("vad_speech_ratio", audio_cfg.get("vad_speech_ratio", 0.4))),
            vad_activation_threshold_s=float(listen_cfg.get("vad_activation_threshold_s", audio_cfg.get("vad_activation_threshold_s", 0.06))),
            vad_min_rms=float(listen_cfg.get("vad_min_rms", 0.020)),
            min_segment_s=float(audio_cfg.get("min_segment_s", 0.45)),
            partial_min_speech_s=float(audio_cfg.get("partial_min_speech_s", 0.45)),
            # Reverse listening should emit bounded clips even when loopback
            # audio never falls silent. The user-facing segment duration is
            # also the final VAD segment cap for this lane.
            max_segment_s=self._effective_listen_max_segment_s(audio_cfg),
            denoise_strength=float(listen_cfg.get("denoise_strength", 0.35)),
            silero_speech_threshold=float(listen_cfg.get("silero_speech_threshold", 0.15)),
            vad_type=str(listen_cfg.get("vad_type", "webrtc")).strip().lower(),
            output_device_name=device_name,
            chunk_interval_ms=max(
                chunk_interval_ms,
                streaming_cfg["chunk_interval_ms"],
            ),
            chunk_window_s=max(
                segment_duration_s,
                streaming_cfg["chunk_window_s"],
            ),
            ring_buffer_s=max(
                segment_duration_s,
                streaming_cfg["ring_buffer_s"],
            ),
            recent_speech_hold_s=streaming_cfg["recent_speech_hold_s"],
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
            self._call_in_ui(
                lambda: self._refresh_floating_window_status(False)
            )
            self._last_listen_started_at = time.monotonic()
            self._last_listen_result_at = self._last_listen_started_at
            self._last_listen_diagnostic_log_at = 0.0
            self._last_desktop_device_signature = (tuple(sorted(self._desktop_devices)), device_name)
            self._desktop_recovery_attempt = 0
            recovery_timer = getattr(self, "_desktop_recovery_timer", None)
            if recovery_timer is not None:
                self._call_in_ui(
                    lambda timer=recovery_timer: timer.stop()
                )
            self._log_listen_environment("after_start")
            logger.info("Desktop listen started successfully on output device: %s", device_name)
        except Exception:
            self._listen_recorder = None
            self._active_listen_output_device_name = None
            raise

    def _stop_listen(self) -> None:
        cancelled = self._invalidate_realtime_source(DESKTOP_SOURCE)
        if cancelled:
            logger.info("Cancelled %s stale reverse-translation task(s)", cancelled)
        self._reset_streaming_state(DESKTOP_SOURCE)
        self._listen_in_speech = False
        self._call_in_ui(
            lambda: self._refresh_floating_window_status(False)
        )
        self._active_listen_output_device_name = None
        if self._listen_recorder is not None:
            try:
                self._listen_recorder.stop()
            finally:
                self._listen_recorder = None
        logger.info("Desktop listen stopped")

    def _handle_desktop_capture_runtime_error(self, message: str) -> None:
        detail = str(message or "").strip()
        if detail:
            logger.warning("Desktop audio capture stopped after an error: %s", detail)
        self._set_bottom(
            self._t("main_desktop_listen_failed")
            if detail
            else self._t("main_desktop_listen_stopped"),
            "warning",
        )
        self._schedule_desktop_capture_recovery(detail)

    def _schedule_desktop_capture_recovery(self, message: str = "") -> None:
        if (
            getattr(self, "_destroying", False)
            or not getattr(self, "_running", False)
            or not getattr(self, "_desktop_capture_enabled", False)
        ):
            return
        timer = getattr(self, "_desktop_recovery_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._restart_desktop_capture)
            self._desktop_recovery_timer = timer
        if timer.isActive():
            return
        attempt = int(getattr(self, "_desktop_recovery_attempt", 0)) + 1
        self._desktop_recovery_attempt = attempt
        delay_ms = min(500 * (2 ** min(attempt - 1, 4)), 8000)
        logger.warning(
            "Scheduling desktop capture recovery attempt=%d delay_ms=%d reason=%s",
            attempt,
            delay_ms,
            message or "capture stopped",
        )
        timer.start(delay_ms)

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
        normalized = unicodedata.normalize("NFKC", str(name or ""))
        return " ".join(normalized.casefold().split())

    @classmethod
    def _audio_device_identity_parts(cls, name: str | None) -> frozenset[str]:
        normalized = cls._normalize_audio_device_name(name)
        if not normalized:
            return frozenset()
        parts = {normalized}
        for parenthesized in re.findall(r"\(([^()]*)\)", normalized):
            identity = " ".join(parenthesized.split()).strip(" -_:;")
            if len(identity) >= 4:
                parts.add(identity)
        return frozenset(parts)

    @classmethod
    def _microphone_device_names_match(
        cls,
        left: str | None,
        right: str | None,
    ) -> bool:
        left_norm = cls._normalize_audio_device_name(left)
        right_norm = cls._normalize_audio_device_name(right)
        if not left_norm or not right_norm:
            return False
        if (
            left_norm == right_norm
            or left_norm in right_norm
            or right_norm in left_norm
        ):
            return True
        return bool(
            cls._audio_device_identity_parts(left)
            & cls._audio_device_identity_parts(right)
        )

    @classmethod
    def _matching_microphone_device_name(
        cls,
        name: str | None,
        candidates: object,
    ) -> str | None:
        clean = str(name or "").strip()
        if not clean:
            return None
        candidate_names = tuple(
            str(candidate or "").strip() for candidate in (candidates or ())
        )
        for candidate_name in candidate_names:
            if candidate_name == clean:
                return candidate_name
        identity_matches = [
            candidate_name
            for candidate_name in candidate_names
            if cls._microphone_device_names_match(clean, candidate_name)
        ]
        return identity_matches[0] if len(identity_matches) == 1 else None

    def _match_mic_input_device_name(self, name: str | None) -> str | None:
        return self._matching_microphone_device_name(
            name,
            (getattr(self, "_devices", {}) or {}).keys(),
        )

    def _desktop_device_names_match(self, left: str | None, right: str | None) -> bool:
        return audio_device_names_match(left, right)

    def _match_desktop_device_name(self, name: str | None) -> str | None:
        clean = str(name or "").strip()
        if not clean:
            return None
        devices = getattr(self, "_desktop_devices", {}) or {}
        if clean in devices:
            return clean
        matched = unique_device_name_match(clean, devices)
        if matched is not None:
            return matched
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

    @staticmethod
    def _audio_diagnostic_stats_summary(stats: object) -> dict[str, object]:
        """Keep support telemetry useful without logging device inventories."""

        if not isinstance(stats, dict):
            return {}
        allowed = (
            "running",
            "worker_alive",
            "worker_failure_count",
            "stream_open",
            "frame_queue_size",
            "frame_queue_capacity",
            "frame_queue_high_watermark",
            "frame_queue_dropped",
            "stale_frames_discarded",
            "frames_processed",
            "segments_emitted",
            "last_frame_rms",
            "peak_frame_rms",
            "total_frames",
            "non_silent_frames",
            "capture_rate",
            "target_rate",
            "capture_channels",
            "channels",
            "vad_in_speech",
            "vad_speech_ratio",
            "vad_activation_ratio",
        )
        summary = {key: stats[key] for key in allowed if key in stats}
        summary["has_worker_error"] = bool(stats.get("last_worker_error"))
        summary["has_capture_error"] = bool(stats.get("last_error"))
        return summary

    @staticmethod
    def _process_audio_diagnostic_summary(snapshot: object) -> dict[str, object]:
        if not isinstance(snapshot, dict):
            return {}
        process_ids = snapshot.get("process_ids")
        matches = snapshot.get("matches")
        return {
            "is_running": bool(snapshot.get("is_running")),
            "process_count": len(process_ids) if isinstance(process_ids, list) else 0,
            "has_active_audio_session": bool(
                snapshot.get("has_active_audio_session")
            ),
            "matched_device_count": len(matches) if isinstance(matches, list) else 0,
            "has_default_output": bool(snapshot.get("default_output_device")),
            "has_active_output": bool(snapshot.get("active_device")),
            "probe_enabled": bool(snapshot.get("probe_enabled")),
        }

    def _log_listen_environment(self, stage: str) -> None:
        process_audio = self._listen_process_snapshot()
        logger.info(
            "Desktop listen environment [%s] enabled=%s running=%s available=%s "
            "has_selected_output=%s has_active_output=%s has_default_output=%s "
            "process_audio=%s",
            stage,
            self._desktop_capture_enabled,
            self._listen_recorder is not None,
            self._listen_available,
            bool(self._desktop_output_device_name()),
            bool(self._active_listen_output_device_name),
            bool(default_output_device_name()),
            self._process_audio_diagnostic_summary(process_audio),
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
        candidates: list[tuple[int, int, str]] = []
        for index, name in enumerate(getattr(self, "_desktop_devices", {}) or {}):
            if self._desktop_device_names_match(name, avoided_name):
                continue
            if self._listen_auto_should_avoid_output_device(name):
                continue
            normalized = self._normalize_audio_device_name(name)
            score = 0
            if any(token in normalized for token in LISTEN_REAL_OUTPUT_HINTS):
                score += 100
            if "headphone" in normalized or "headphones" in normalized:
                score += 20
            if any(token in normalized for token in LISTEN_VIRTUAL_OUTPUT_TOKENS):
                score -= 250
            candidates.append((score, -index, name))
        if not candidates:
            return None
        _score, _order, selected = max(candidates)
        return selected

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

    def _effective_listen_max_segment_s(self, audio_cfg: Mapping[str, Any]) -> float:
        try:
            audio_max = max(0.5, float(audio_cfg.get("max_segment_s", 6.0)))
        except (TypeError, ValueError):
            audio_max = 6.0
        return min(audio_max, self._listen_segment_duration_s())

    def _listen_tail_silence_s(self) -> float:
        listen_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        try:
            return max(0.2, float(listen_cfg.get("tail_silence_s", 0.40)))
        except (TypeError, ValueError):
            return 0.40

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
        if not self._running or not self._desktop_capture_enabled:
            self._last_desktop_device_signature = None
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
            self._listen_in_speech = False
            self._refresh_desktop_capture_button()
            self._refresh_floating_window_status(False)
            self._sync_settings_window_vrc_listen_state()
            logger.warning("Desktop audio capture restart failed: %s", message or exc)
            self._set_bottom(
                self._t("main_desktop_listen_failed"),
                "warning",
            )
            self._schedule_desktop_capture_recovery(message or str(exc))

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
            "Desktop listen diagnostics state=%s idle_for=%.1fs stats=%s "
            "process_audio=%s mic_active=%s output_format=%s self_suppress=%s",
            audio_state,
            now - idle_anchor if idle_anchor > 0 else 0.0,
            self._audio_diagnostic_stats_summary(stats),
            self._process_audio_diagnostic_summary(process_audio),
            bool(self._active_mic_input_device_name),
            self._get_output_format(),
            bool(self._desktop_capture_config().get("self_suppress", False)),
        )
        self._last_listen_diagnostic_log_at = now

    def _refresh_listen_availability(self, *, refresh_devices: bool = False) -> bool:
        try:
            if refresh_devices or not self._desktop_devices:
                self._load_desktop_devices()
            available = bool(self._desktop_devices)
        except Exception:
            available = False
        self._listen_available = available
        return available

    def _current_default_input_device_name(self, devices: list[dict]) -> str | None:
        if not devices:
            return None
        marked_defaults = [
            str(device.get("name", "") or "").strip()
            for device in devices
            if bool(device.get("is_default"))
            and str(device.get("name", "") or "").strip()
        ]
        if len(marked_defaults) == 1:
            return marked_defaults[0]
        try:
            return inventory_default_input_device_name(force_refresh=False)
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
        devices = _list_microphone_devices()
        self._cache_microphone_devices(devices)
        if getattr(self, "_device_combo", None) is not None:
            self._refresh_device_combo()
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
        if not self._running:
            self._last_mic_device_signature = None
            return
        if bool(getattr(self, "_mic_muted", False)) or bool(
            getattr(self, "_mic_capture_paused_for_mute", False)
        ):
            if self._recorder is not None:
                logger.info("Stopping microphone capture left active while muted")
                self._stop_microphone_capture()
            return
        try:
            previous = self._last_mic_device_signature
            signature = self._microphone_device_signature()
            self._last_mic_device_signature = signature
            _, default_name, mode, configured_name, resolved_name = signature
            recorder = self._recorder
            if recorder is None and not self._mic_recovery_in_progress:
                self._restart_microphone_capture("microphone recorder missing while running")
                return
            if recorder is not None and not recorder.is_running:
                self._restart_microphone_capture("microphone recorder stopped unexpectedly")
                return
            if recorder is not None:
                self._maybe_log_mic_diagnostics()
            device_names = signature[0]
            previous_names = previous[0] if previous is not None else ()
            previous_default = previous[1] if previous is not None else None
            previous_configured = previous[3] if previous is not None else None
            previous_resolved = previous[4] if previous is not None else None
            active_name = self._active_mic_input_device_name
            active_device = self._matching_microphone_device_name(
                active_name,
                device_names,
            )
            previous_active_device = self._matching_microphone_device_name(
                active_name,
                previous_names,
            )
            if (
                previous is not None
                and active_name
                and previous_active_device
                and not active_device
            ):
                logger.info(
                    "Detected active microphone removal (active=%s)",
                    active_name,
                )
                self._restart_microphone_capture("active microphone was removed")
                return
            if mode == "auto":
                default_changed = bool(previous_default or default_name) and not (
                    self._microphone_device_names_match(previous_default, default_name)
                )
                resolved_changed = bool(previous_resolved or resolved_name) and not (
                    self._microphone_device_names_match(previous_resolved, resolved_name)
                )
                if resolved_name and not self._microphone_device_names_match(
                    resolved_name,
                    active_name,
                ) and (
                    previous is None
                    or default_changed
                    or resolved_changed
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
                configured_device = self._matching_microphone_device_name(
                    configured_name,
                    device_names,
                )
                previous_configured_device = self._matching_microphone_device_name(
                    configured_name,
                    previous_names,
                )
                configured_changed = bool(
                    previous_configured or configured_name
                ) and not self._microphone_device_names_match(
                    previous_configured,
                    configured_name,
                )
                if configured_device and not self._microphone_device_names_match(
                    configured_device,
                    active_name,
                ) and (
                    previous is None
                    or previous_configured_device is None
                    or configured_changed
                ):
                    logger.info(
                        "Configured microphone became available "
                        "(configured=%s resolved=%s active=%s)",
                        configured_name,
                        configured_device,
                        active_name,
                    )
                    self._restart_microphone_capture("configured microphone became available")
                    return
                if not configured_device and previous is not None:
                    default_changed = bool(previous_default or default_name) and not (
                        self._microphone_device_names_match(
                            previous_default,
                            default_name,
                        )
                    )
                    if (
                        default_changed
                        and default_name
                        and not self._microphone_device_names_match(
                            default_name,
                            active_name,
                        )
                    ):
                        logger.info(
                            "Detected fallback microphone change "
                            "(previous_default=%s current_default=%s active=%s)",
                            previous_default,
                            default_name,
                            active_name,
                        )
                        self._restart_microphone_capture(
                            "fallback default microphone changed"
                        )
                        return
        except Exception:
            logger.exception("Microphone device watch failed")
        finally:
            if not self._destroying:
                self._schedule_mic_audio_watch()

    def _maybe_log_mic_diagnostics(self) -> None:
        now = time.monotonic()
        if bool(getattr(self, "_mic_muted", False)):
            return
        if (
            self._last_mic_diagnostic_log_at > 0
            and (now - self._last_mic_diagnostic_log_at)
            < MIC_DIAGNOSTIC_LOG_INTERVAL_S
        ):
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
        log_fn = (
            logger.warning
            if audio_state == "audio_present_but_no_segment"
            else logger.info
        )
        log_fn(
            "Microphone diagnostics state=%s idle_for=%.1fs stats=%s "
            "active=%s resolved=%s muted=%s output_format=%s",
            audio_state,
            now - idle_anchor if idle_anchor > 0 else 0.0,
            self._audio_diagnostic_stats_summary(stats),
            bool(self._active_mic_input_device_name),
            bool(self._resolve_mic_input_device_name(refresh=False)),
            bool(getattr(self, "_mic_muted", False)),
            self._get_output_format(),
        )
        silence_anchor = max(
            last_non_silent_at,
            float(self.__dict__.get("_last_mic_started_at", 0.0) or 0.0),
        )
        try:
            peak_rms = float(stats.get("peak_frame_rms") or 0.0)
            frames_processed = int(stats.get("frames_processed") or 0)
        except (TypeError, ValueError):
            peak_rms = 0.0
            frames_processed = 0
        if (
            audio_state == "no_mic_audio"
            and silence_anchor > 0
            and (now - silence_anchor) >= MIC_DIGITAL_SILENCE_RECOVERY_S
            and peak_rms >= 0.02
            and frames_processed >= 100
        ):
            # A stream that previously carried real speech but now supplies
            # digital silence can remain "running" indefinitely on Windows.
            # Reopen it once for this outage; a new recorder resets peak_rms,
            # preventing repeated restarts during an intentionally quiet room.
            logger.warning(
                "Recovering microphone after sustained digital silence "
                "silent_for=%.1fs active_device=%s frames_processed=%d",
                now - silence_anchor,
                stats.get("active_device"),
                frames_processed,
            )
            self._restart_microphone_capture("sustained digital silence")

    def _restart_microphone_capture(self, reason: str) -> None:
        if (
            self._destroying
            or not self._running
            or self._mic_recovery_in_progress
            or bool(getattr(self, "_mic_muted", False))
            or bool(getattr(self, "_mic_capture_paused_for_mute", False))
        ):
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

    def _asr_provider_key(self, provider: Any) -> Any:
        explicit_key = getattr(provider, "concurrency_key", None)
        if explicit_key is not None:
            try:
                hash(explicit_key)
                return explicit_key
            except Exception:
                pass
        provider_id = str(getattr(provider, "provider_id", "") or "").strip()
        if provider_id and provider_id != "base":
            return provider_id
        return ("asr-instance", id(provider))

    @staticmethod
    def _asr_provider_concurrency(provider: Any) -> int:
        try:
            return max(
                1,
                min(
                    int(getattr(provider, "max_concurrent_transcriptions", 1)),
                    4,
                ),
            )
        except (TypeError, ValueError):
            return 1

    def _realtime_asr_worker_concurrency(self) -> int:
        if self._performance_profile() == "low_power":
            return 1
        limits: dict[Any, int] = {}
        for provider in (getattr(self, "_asr", None), getattr(self, "_listen_asr", None)):
            if provider is None:
                continue
            key = self._asr_provider_key(provider)
            limits[key] = max(
                limits.get(key, 0),
                self._asr_provider_concurrency(provider),
            )
        if not limits:
            return ASR_WORKER_CONCURRENCY
        return max(1, min(sum(limits.values()), 4))

    def _realtime_translation_worker_concurrency(self) -> int:
        if self._performance_profile() == "low_power":
            return 1
        trans_cfg = self._config.get("translation", {})
        if not isinstance(trans_cfg, Mapping):
            return TRANSLATION_WORKER_CONCURRENCY
        backend = normalize_backend(trans_cfg.get("backend"))
        backend_cfg = trans_cfg.get(backend, {})
        if not isinstance(backend_cfg, Mapping):
            backend_cfg = {}
        configured = backend_cfg.get("max_concurrent_requests")
        if configured is not None:
            try:
                return max(1, min(int(configured), 4))
            except (TypeError, ValueError):
                pass
        base_url = str(backend_cfg.get("base_url", "") or "").strip().lower()
        if backend == "local_ai" or any(
            marker in base_url
            for marker in ("127.0.0.1", "localhost", "[::1]")
        ):
            return 1
        if backend in {
            "google_web",
            "microsoft_edge_web",
            "mymemory",
            "deepl",
            "libretranslate",
        }:
            base_concurrency = 2
        elif backend in {
            "openai",
            "openai_compatible",
            "grok_compatible",
            "deepseek",
            "zhipu",
            "qianwen",
            "xiaomi",
            "gemini",
            "kimi",
            "hunyuan",
            "xai",
            "mistral",
            "doubao",
            "nvidia",
            "anthropic",
            "anthropic_compatible",
            "custom",
        }:
            base_concurrency = 3
        else:
            base_concurrency = 2
        if self._reverse_translation_capacity_enabled():
            base_concurrency += 1
        return min(base_concurrency, 4)

    def _reverse_translation_capacity_enabled(self) -> bool:
        listen_cfg = self._config.get("vrc_listen", {})
        return bool(
            isinstance(listen_cfg, Mapping)
            and listen_cfg.get("enabled", False)
        )

    def _realtime_rewrite_worker_concurrency(self) -> int:
        if self._performance_profile() == "low_power":
            return 1
        trans_cfg = self._config.get("translation", {})
        if not isinstance(trans_cfg, Mapping):
            return 1
        backend = normalize_backend(trans_cfg.get("backend"))
        if backend in {"local_ai", "google_web", "mymemory", "deepl", "libretranslate"}:
            return 1
        backend_cfg = trans_cfg.get(backend, {})
        if isinstance(backend_cfg, Mapping):
            configured = backend_cfg.get("rewrite_concurrency")
            if configured is not None:
                try:
                    return max(1, min(int(configured), 3))
                except (TypeError, ValueError):
                    pass
        return ASR_REWRITE_WORKER_CONCURRENCY

    def _realtime_session_active(self, session_id: int) -> bool:
        return bool(
            not getattr(self, "_destroying", False)
            and getattr(self, "_running", False)
            and session_id == getattr(self, "_listen_session", -1)
        )

    def _realtime_task_active(self, task: RealtimeTask) -> bool:
        if not self._realtime_session_active(task.session_id):
            return False
        payload = getattr(task, "payload", None)
        if not isinstance(payload, _RealtimeAudioPayload):
            return True
        current_generation = int(
            getattr(self, "_realtime_source_generations", {}).get(task.source, 0)
        )
        return int(payload.source_generation) == current_generation

    @staticmethod
    def _realtime_context_source(source: str) -> str:
        return "listen" if source == DESKTOP_SOURCE else MIC_SOURCE

    def _invalidate_realtime_source(self, source: str) -> int:
        generations = getattr(self, "_realtime_source_generations", None)
        if not isinstance(generations, dict):
            generations = {MIC_SOURCE: 0, DESKTOP_SOURCE: 0}
            self._realtime_source_generations = generations
        generations[source] = int(generations.get(source, 0)) + 1

        session_id = int(getattr(self, "_listen_session", -1))
        store = getattr(self, "_translation_context_store", None)
        if isinstance(store, TranslationContextStore):
            store.clear_source(
                session_id=session_id,
                context_source=self._realtime_context_source(source),
            )

        scheduler = getattr(self, "_realtime_scheduler", None)
        cancel_source = getattr(scheduler, "cancel_source", None)
        if callable(cancel_source):
            try:
                return int(cancel_source(source, session_id=session_id) or 0)
            except Exception:
                logger.exception("Failed to cancel stale realtime source work: %s", source)
        return 0

    def _build_realtime_payload(
        self,
        audio: Any,
        *,
        source: str,
        asr_language: str | None,
        source_language: str | None,
    ) -> _RealtimeAudioPayload | None:
        provider = self._asr_for_source(source)
        if provider is None:
            return None
        config_snapshot = getattr(self, "_realtime_config_snapshot", None)
        if not isinstance(config_snapshot, Mapping):
            config_snapshot = _freeze_snapshot_value(copy.deepcopy(self._config))
        recorder = (
            getattr(self, "_listen_recorder", None)
            if source == DESKTOP_SOURCE
            else getattr(self, "_recorder", None)
        )
        segment_timing = getattr(recorder, "last_segment_timing", {})
        diagnostics = (
            dict(segment_timing)
            if isinstance(segment_timing, Mapping)
            else {}
        )
        diagnostics["payload_built_at"] = time.monotonic()
        return _RealtimeAudioPayload(
            audio=_immutable_audio_snapshot(audio),
            asr_provider=provider,
            asr_language=asr_language,
            source_language=source_language,
            target_language=str(getattr(self, "_current_tgt_lang", "ja") or "ja"),
            second_target_language=str(
                getattr(self, "_current_tgt_lang_2", "en") or "en"
            ),
            third_target_language=str(getattr(self, "_current_tgt_lang_3", "") or ""),
            listen_target_language=self._listen_target_language(),
            listen_prefix=self._copy("listen_prefix"),
            send_to_chatbox=(
                self._listen_send_to_chatbox_enabled()
                if source == DESKTOP_SOURCE
                else self._mic_send_to_chatbox_enabled()
            ),
            config_snapshot=config_snapshot,
            source_generation=int(
                getattr(self, "_realtime_source_generations", {}).get(source, 0)
            ),
            diagnostics=diagnostics,
        )

    def _on_audio_segment(self, audio, source: str = MIC_SOURCE):
        if not self._running:
            return None
        if source == MIC_SOURCE:
            if getattr(self, "_mic_muted", False):
                self._reset_streaming_state(MIC_SOURCE)
                return None
            self._reset_streaming_state(MIC_SOURCE)
            asr_lang = self._current_asr_lang
            selected_src_lang = self._current_src_lang
        else:
            if self._listen_tts_echo_suppress_active():
                self._reset_streaming_state(DESKTOP_SOURCE)
                return None
            self._reset_streaming_state(DESKTOP_SOURCE)
            selected_src_lang = self._listen_source_language()
            asr_lang = selected_src_lang or "auto"

        scheduler = getattr(self, "_realtime_scheduler", None)
        if scheduler is None:
            return None
        payload = self._build_realtime_payload(
            audio,
            source=source,
            asr_language=asr_lang,
            source_language=selected_src_lang,
        )
        if payload is None:
            return None
        admission = scheduler.submit(
            source=source,
            session_id=self._listen_session,
            provider_key=self._asr_provider_key(payload.asr_provider),
            payload=payload,
            diagnostics=payload.diagnostics,
            provider_concurrency=self._asr_provider_concurrency(
                payload.asr_provider
            ),
        )
        if admission.status is AdmissionStatus.FULL:
            logger.warning("Final sentence rejected by backpressure source=%s", source)
            session_id = self._listen_session
            self._call_in_ui(
                lambda sid=session_id: (
                    self._set_bottom(self._copy("realtime_queue_full"), "warning")
                    if self._realtime_session_active(sid)
                    else None
                )
            )
        elif admission.status is AdmissionStatus.ACCEPTED and source == MIC_SOURCE:
            # Diagnostics distinguish captured-and-admitted speech from a final
            # segment that was dropped at the scheduler capacity boundary.
            self._last_mic_result_at = time.monotonic()
        elif admission.status not in {AdmissionStatus.ACCEPTED, AdmissionStatus.STOPPED}:
            logger.warning(
                "Final sentence admission rejected source=%s status=%s",
                source,
                admission.status.value,
            )
        return admission

    def _on_audio_chunk(self, audio, source: str = MIC_SOURCE) -> None:
        if not self._running:
            return
        if source == MIC_SOURCE:
            if getattr(self, "_mic_muted", False):
                return
            asr_lang = self._current_asr_lang
        else:
            if self._listen_tts_echo_suppress_active():
                return
            asr_lang = self._listen_source_language() or "auto"
        if not self._should_process_partial_asr(source):
            return

        provider = self._asr_for_source(source)
        scheduler = getattr(self, "_realtime_scheduler", None)
        if provider is None or scheduler is None:
            return
        provider_key = self._asr_provider_key(provider)
        if scheduler.should_yield_partial(provider_key):
            return
        work_queue = self._partial_task_queues.get(source)
        if work_queue is None:
            return
        result = self._enqueue_latest(
            work_queue,
            (
                _immutable_audio_snapshot(audio),
                asr_lang,
                self._partial_generation,
                self._listen_session,
                source,
            ),
        )
        if result == "dropped":
            logger.debug("Partial ASR chunk dropped source=%s", source)

    def _scheduler_asr_stage(
        self,
        task: RealtimeTask,
        cancel_event: threading.Event,
    ) -> str:
        payload = task.payload
        if not isinstance(payload, _RealtimeAudioPayload):
            raise TypeError("Invalid realtime audio task payload")
        if cancel_event.is_set() or not self._realtime_task_active(task):
            return ""
        if task.source == MIC_SOURCE and getattr(self, "_mic_muted", False):
            return ""
        if task.source == DESKTOP_SOURCE and self._listen_tts_echo_suppress_active():
            return ""

        if task.source == MIC_SOURCE:
            self._call_in_ui(
                lambda sid=task.session_id: (
                    self._set_runtime_status("status_translating", "accent")
                    if self._realtime_session_active(sid)
                    else None
                )
            )
        else:
            self._call_in_ui(
                lambda sid=task.session_id: (
                    self._set_floating_listen_status(True)
                    if self._realtime_session_active(sid)
                    else None
                )
            )
        text = self._transcribe_with_provider(
            payload.asr_provider,
            task.source,
            payload.audio,
            payload.asr_language,
            is_final=True,
            request_context={
                "source": task.source,
                "sequence": task.sequence,
                "session_id": task.session_id,
                "timing": payload.diagnostics,
            },
            cancel_event=cancel_event,
        )
        return text

    def _create_realtime_translator(
        self,
        config: dict,
        *,
        source: str = MIC_SOURCE,
    ):
        runtime_config = copy.deepcopy(config)
        runtime_translation = runtime_config.get("translation", {})
        if isinstance(runtime_translation, dict):
            listen_cfg = runtime_config.get("vrc_listen", {})
            if not isinstance(listen_cfg, Mapping):
                listen_cfg = {}
            timeout_key = (
                "translation_timeout_s"
                if source == DESKTOP_SOURCE
                else "realtime_timeout_s"
            )
            timeout_default = (
                DEFAULT_REVERSE_TRANSLATION_TIMEOUT_S
                if source == DESKTOP_SOURCE
                else DEFAULT_REALTIME_TRANSLATION_TIMEOUT_S
            )
            try:
                realtime_timeout = float(
                    listen_cfg.get(timeout_key, timeout_default)
                    if source == DESKTOP_SOURCE
                    else runtime_translation.get(timeout_key, timeout_default)
                )
            except (TypeError, ValueError):
                realtime_timeout = timeout_default
            realtime_timeout = max(2.0, min(realtime_timeout, 30.0))
            timeout_fields = (
                "timeout_s",
                "connect_timeout_s",
                "pool_timeout_s",
                "read_timeout_s",
                "write_timeout_s",
                "wall_timeout_s",
            )
            for backend_config in runtime_translation.values():
                if isinstance(backend_config, dict):
                    backend_config["max_retries"] = 0
                    for field_name in timeout_fields:
                        if field_name not in backend_config:
                            continue
                        try:
                            configured_timeout = float(
                                backend_config.get(
                                    field_name,
                                    DEFAULT_REALTIME_TRANSLATION_TIMEOUT_S,
                                )
                            )
                        except (TypeError, ValueError):
                            configured_timeout = DEFAULT_REALTIME_TRANSLATION_TIMEOUT_S
                        backend_config[field_name] = min(
                            max(configured_timeout, 1.0),
                            realtime_timeout,
                        )
        context_store = getattr(self, "_translation_context_store", None)
        try:
            return create_translator(
                runtime_config,
                context_store=context_store,
            )
        except TypeError as exc:
            # Test doubles and third-party embedding shims may still expose the
            # historical one-argument factory signature.
            if "context_store" not in str(exc):
                raise
            return create_translator(runtime_config)

    @staticmethod
    def _prewarm_realtime_translator(translator: Any) -> bool:
        prewarm = getattr(translator, "prewarm", None)
        if not callable(prewarm):
            return False
        return bool(prewarm())

    def _realtime_translation_prewarm_config(self) -> dict:
        snapshot = getattr(self, "_realtime_config_snapshot", None)
        config = _thaw_snapshot_value(snapshot)
        if not isinstance(config, dict):
            config = copy.deepcopy(self._config)
        return config

    def _realtime_translation_credentials_available(self, config: dict) -> bool:
        return (
            first_missing_required_credential(
                config,
                scopes=("translation",),
                ui_language=getattr(self, "_ui_lang", None),
                active_only=True,
            )
            is None
        )

    def _create_realtime_translation_worker_state(
        self,
        worker_index: int,
    ) -> _RealtimeTranslationWorkerState:
        """Attach completed prewarm clients without delaying worker startup."""

        state = _RealtimeTranslationWorkerState()
        config = self._realtime_translation_prewarm_config()
        if (
            not self._translation_background_warmup_enabled(config)
            or not self._realtime_translation_credentials_available(config)
        ):
            return state
        signature = provider_runtime_config_signature(config)
        try:
            state.translator = self._take_realtime_prewarmed_translator(
                config,
                source=MIC_SOURCE,
            )
            mic_succeeded = state.translator is not None
            listen_cfg = config.get("vrc_listen", {})
            listen_enabled = bool(
                isinstance(listen_cfg, Mapping)
                and listen_cfg.get("enabled", False)
            )
            listen_succeeded = False
            if listen_enabled:
                state.listen_translator = self._take_realtime_prewarmed_translator(
                    config,
                    source=DESKTOP_SOURCE,
                )
                listen_succeeded = state.listen_translator is not None
            state.runtime_signature = signature
            logger.info(
                "Realtime translation worker startup finished "
                "worker=%d mic=%s listen=%s thread=%s",
                worker_index,
                mic_succeeded,
                listen_succeeded,
                threading.current_thread().name,
            )
        except Exception as exc:
            state.close()
            logger.warning(
                "Realtime translation worker startup failed "
                "worker=%d error=%s",
                worker_index,
                safe_exception_summary(exc),
            )
        return state

    def _create_realtime_rewrite_worker_state(
        self,
        worker_index: int,
    ) -> _RealtimeRewriteWorkerState:
        state = _RealtimeRewriteWorkerState()
        config = self._realtime_translation_prewarm_config()
        translation_cfg = config.get("translation", {})
        if not isinstance(translation_cfg, Mapping):
            return state
        if (
            normalize_asr_rewrite_style(
                translation_cfg.get(
                    "asr_rewrite_style",
                    ASR_REWRITE_DISABLED,
                )
            )
            == ASR_REWRITE_DISABLED
            or not self._realtime_translation_credentials_available(config)
        ):
            return state
        signature = provider_runtime_config_signature(config)
        try:
            state.translator = self._create_realtime_translator(
                config,
                source=MIC_SOURCE,
            )
            succeeded = self._prewarm_realtime_translator(state.translator)
            state.runtime_signature = signature
            logger.info(
                "Realtime rewrite worker prewarm finished "
                "worker=%d succeeded=%s thread=%s",
                worker_index,
                succeeded,
                threading.current_thread().name,
            )
        except Exception as exc:
            state.close()
            logger.warning(
                "Realtime rewrite worker prewarm failed "
                "worker=%d error=%s",
                worker_index,
                safe_exception_summary(exc),
            )
        return state

    def _scheduler_rewrite_stage(
        self,
        task: RealtimeTask,
        text: str,
        worker_state: Any,
        cancel_event: threading.Event,
    ) -> str:
        payload = task.payload
        if not isinstance(payload, _RealtimeAudioPayload):
            raise TypeError("Invalid realtime rewrite task payload")
        clean = str(text or "").strip()
        if (
            not clean
            or cancel_event.is_set()
            or not self._realtime_task_active(task)
        ):
            return clean

        config_snapshot = _thaw_snapshot_value(payload.config_snapshot)
        if not isinstance(config_snapshot, dict):
            config_snapshot = {}
        translation_cfg = config_snapshot.get("translation", {})
        if not isinstance(translation_cfg, Mapping):
            translation_cfg = {}
        style = normalize_asr_rewrite_style(
            translation_cfg.get("asr_rewrite_style", ASR_REWRITE_DISABLED)
        )

        # The selector changes only the local player's transcript. Rewriting
        # desktop/listen audio would alter the other speaker's words.
        rewritten = clean
        if task.source == MIC_SOURCE and style != ASR_REWRITE_DISABLED:
            try:
                if not isinstance(worker_state, _RealtimeRewriteWorkerState):
                    raise TypeError("Invalid realtime rewrite worker state")
                runtime_signature = provider_runtime_config_signature(
                    config_snapshot
                )
                if (
                    worker_state.runtime_signature != runtime_signature
                    or worker_state.translator is None
                ):
                    worker_state.close()
                    worker_state.translator = self._create_realtime_translator(
                        config_snapshot
                    )
                    worker_state.runtime_signature = runtime_signature
                worker_state.config_snapshot = payload.config_snapshot
                rewrite = getattr(worker_state.translator, "rewrite_asr", None)
                if not callable(rewrite):
                    raise RuntimeError(
                        "The selected translation provider cannot rewrite ASR text"
                    )
                rewrite_operation_invoked = False

                def rewrite_operation() -> object:
                    nonlocal rewrite_operation_invoked
                    rewrite_operation_invoked = True
                    with translation_context_scope(
                        session_id=task.session_id,
                        sequence=task.sequence,
                    ):
                        return rewrite(
                            clean,
                            style,
                            language_hint=payload.source_language or "auto",
                            context_source=MIC_SOURCE,
                        )

                try:
                    coordinator = getattr(self, "_rewrite_coordinator", None)
                    if coordinator is None:
                        rewrite_result = rewrite_operation()
                    else:
                        rewrite_result = coordinator.execute(
                            priority=REWRITE_PRIORITY_REALTIME,
                            config=config_snapshot,
                            style=style,
                            language_hint=payload.source_language or "auto",
                            text=clean,
                            operation=rewrite_operation,
                            wait_timeout_s=1.0,
                        )
                    rewritten = str(rewrite_result or "").strip() or clean
                finally:
                    if rewrite_operation_invoked:
                        metrics = translation_metrics_snapshot(
                            worker_state.translator
                        )
                        payload.diagnostics.update(
                            {
                                f"rewrite_{key}": value
                                for key, value in metrics.items()
                            }
                        )
            except Exception as exc:
                rewritten = clean
                failed_translator = (
                    worker_state.translator
                    if isinstance(worker_state, _RealtimeRewriteWorkerState)
                    else None
                )
                failure_metrics = translation_metrics_snapshot(failed_translator)
                if _translator_cleanup_is_supervised(
                    failed_translator,
                    failure_metrics,
                ):
                    # A wall-timeout cleanup thread already owns destructive
                    # close. Detach now so the next sentence creates a healthy
                    # client without racing a potentially blocking SDK close.
                    worker_state.translator = None
                    worker_state.runtime_signature = None
                    worker_state.config_snapshot = None
                logger.warning(
                    "ASR style rewrite failed open "
                    "(style=%s source=%s sequence=%d error=%s)",
                    style,
                    task.source,
                    task.sequence,
                    safe_exception_summary(exc),
                )

        return rewritten

    def _scheduler_rewrite_required(
        self,
        task: RealtimeTask,
        text: str,
    ) -> bool:
        """Keep no-op work out of the bounded provider rewrite queue."""

        if task.source != MIC_SOURCE or not str(text or "").strip():
            return False
        payload = task.payload
        if not isinstance(payload, _RealtimeAudioPayload):
            return True
        config_snapshot = _thaw_snapshot_value(payload.config_snapshot)
        if not isinstance(config_snapshot, dict):
            return False
        translation_cfg = config_snapshot.get("translation", {})
        if not isinstance(translation_cfg, Mapping):
            return False
        return normalize_asr_rewrite_style(
            translation_cfg.get(
                "asr_rewrite_style",
                ASR_REWRITE_DISABLED,
            )
        ) != ASR_REWRITE_DISABLED

    def _stage_realtime_source_context(
        self,
        task: RealtimeTask,
        payload: _RealtimeAudioPayload,
        text: str,
    ) -> None:
        clean = str(text or "").strip()
        store = getattr(self, "_translation_context_store", None)
        if not clean or not isinstance(store, TranslationContextStore):
            return
        if task.source == DESKTOP_SOURCE:
            targets = (payload.listen_target_language,)
        else:
            targets = (
                payload.target_language,
                payload.second_target_language,
                payload.third_target_language,
            )
        source_language = str(payload.source_language or "auto")
        for target_language in dict.fromkeys(
            str(target or "").strip() for target in targets
        ):
            if not target_language:
                continue
            store.stage_source(
                session_id=task.session_id,
                sequence=task.sequence,
                text=clean,
                src_lang=source_language,
                tgt_lang=target_language,
                context_source=self._realtime_context_source(task.source),
            )

    def _scheduler_translation_stage(
        self,
        task: RealtimeTask,
        text: str,
        worker_state: Any,
        cancel_event: threading.Event,
    ) -> RealtimeTranslationResult | None:
        payload = task.payload
        if not isinstance(payload, _RealtimeAudioPayload):
            raise TypeError("Invalid realtime translation task payload")
        if cancel_event.is_set() or not self._realtime_task_active(task):
            return None
        clean = str(text or "").strip()
        if not clean or (task.source == DESKTOP_SOURCE and len(clean) < 2):
            return None
        self._stage_realtime_source_context(task, payload, clean)
        if not isinstance(worker_state, _RealtimeTranslationWorkerState):
            raise TypeError("Invalid realtime translation worker state")

        if (
            worker_state.config_snapshot is not payload.config_snapshot
            or worker_state.config is None
            or worker_state.dispatcher is None
        ):
            config_snapshot = _thaw_snapshot_value(payload.config_snapshot)
            if not isinstance(config_snapshot, dict):
                config_snapshot = {}
            runtime_signature = provider_runtime_config_signature(config_snapshot)
            if worker_state.runtime_signature != runtime_signature:
                worker_state.close_translator()

            dispatcher = OutputDispatcher(config_snapshot)
            worker_state.config_snapshot = payload.config_snapshot
            worker_state.runtime_signature = runtime_signature
            worker_state.config = config_snapshot
            worker_state.dispatcher = dispatcher
            worker_state.mic_pipeline = MicPipeline(
                config_snapshot,
                dispatcher,
                translator_factory=lambda cfg: self._create_realtime_translator(
                    cfg,
                    source=MIC_SOURCE,
                ),
            )
            worker_state.listen_pipeline = ListenPipeline(
                config_snapshot,
                dispatcher,
                translator_factory=lambda cfg: self._create_realtime_translator(
                    cfg,
                    source=DESKTOP_SOURCE,
                ),
            )

        if task.source == DESKTOP_SOURCE:
            pipeline = worker_state.listen_pipeline
            if pipeline is None:
                raise RuntimeError("Realtime listen translation pipeline is unavailable")
            plan = pipeline.create_plan(
                clean,
                source_language=payload.source_language or "auto",
                target_language=payload.listen_target_language,
                listen_prefix=payload.listen_prefix,
                context_source=self._realtime_context_source(DESKTOP_SOURCE),
            )
        else:
            pipeline = worker_state.mic_pipeline
            if pipeline is None:
                raise RuntimeError("Realtime microphone translation pipeline is unavailable")
            plan = pipeline.create_plan(
                clean,
                source_language=payload.source_language or "auto",
                target_language=payload.target_language,
                second_target_language=payload.second_target_language,
                third_target_language=payload.third_target_language,
                context_source=MIC_SOURCE,
            )
        if plan.needs_api_translation and self._translation_cooldown_active(task.source):
            return None

        try:
            active_translator = (
                worker_state.listen_translator
                if task.source == DESKTOP_SOURCE
                else worker_state.translator
            )
            if plan.needs_api_translation and active_translator is None:
                # A background prewarm may have finished after this worker was
                # started. Claim it at first use; if it is still unavailable,
                # translate_plan creates a cold client without waiting for the
                # background job or performing a separate startup probe.
                active_translator = self._take_realtime_prewarmed_translator(
                    worker_state.config or {},
                    source=task.source,
                )
            try:
                result, translator = pipeline.translate_plan(
                    plan,
                    active_translator,
                    context_session_id=task.session_id,
                    defer_context_commit=True,
                    context_sequence=task.sequence,
                )
            except TypeError as exc:
                if not any(
                    name in str(exc)
                    for name in ("context_session_id", "context_sequence")
                ):
                    raise
                result, translator = pipeline.translate_plan(
                    plan,
                    active_translator,
                )
            if task.source == DESKTOP_SOURCE:
                worker_state.listen_translator = translator
            else:
                worker_state.translator = translator
            metrics = getattr(result, "provider_metrics", None)
            if not isinstance(metrics, Mapping) or not metrics:
                metrics = translation_metrics_snapshot(translator)
            payload.diagnostics.update(
                {f"translation_{key}": value for key, value in metrics.items()}
            )
            if result.api_translation_used:
                self._record_source_translation_success(task.source)
            return result
        except Exception as exc:
            failed_translator = (
                worker_state.listen_translator
                if task.source == DESKTOP_SOURCE
                else worker_state.translator
            )
            metrics = getattr(exc, "provider_metrics", None)
            if not isinstance(metrics, Mapping) or not metrics:
                metrics = translation_metrics_snapshot(failed_translator)
            payload.diagnostics.update(
                {f"translation_{key}": value for key, value in metrics.items()}
            )
            friendly = self._format_translation_error(exc)
            self._record_source_translation_failure(task.source, friendly)
            worker_state.close_translator(
                task.source,
                close_client=not _translator_cleanup_is_supervised(
                    failed_translator,
                    metrics,
                ),
            )
            raise

    def _scheduler_delivery_stage(self, completion: RealtimeCompletion) -> None:
        """Deliver one ordered completion and hold its backpressure slot until UI ack."""

        task = completion.task
        if bool(getattr(completion, "cancelled", False)) or not self._realtime_task_active(task):
            return
        self._commit_realtime_translation_context(completion)

        acknowledged = threading.Event()
        cancel_event = getattr(self, "_realtime_delivery_cancel_event", None)
        ui_enqueued_at = time.monotonic()
        ui_timing = {
            "started_at": ui_enqueued_at,
            "finished_at": ui_enqueued_at,
            "output_metrics": {},
        }

        def deliver_on_ui() -> None:
            ui_timing["started_at"] = time.monotonic()
            try:
                if cancel_event is None or not cancel_event.is_set():
                    diagnostics = getattr(task, "diagnostics", None)
                    if isinstance(diagnostics, dict):
                        diagnostics["ui_enqueued_at"] = ui_enqueued_at
                        diagnostics["ui_started_at"] = ui_timing["started_at"]
                    output_metrics = self._deliver_scheduler_completion_ui(completion)
                    if isinstance(output_metrics, Mapping):
                        ui_timing["output_metrics"] = dict(output_metrics)
            finally:
                ui_timing["finished_at"] = time.monotonic()
                acknowledged.set()

        if threading.get_ident() == getattr(self, "_ui_thread_id", None):
            deliver_on_ui()
            self._log_reverse_latency(
                completion,
                ui_enqueued_at=ui_enqueued_at,
                ui_started_at=ui_timing["started_at"],
                ui_finished_at=ui_timing["finished_at"],
                output_metrics=ui_timing["output_metrics"],
            )
            return
        if not self._call_in_ui(deliver_on_ui, priority=True):
            return

        while not acknowledged.wait(UI_DELIVERY_ACK_POLL_S):
            if cancel_event is not None and cancel_event.is_set():
                return
            if not self._realtime_task_active(task):
                return
        self._log_reverse_latency(
            completion,
            ui_enqueued_at=ui_enqueued_at,
            ui_started_at=ui_timing["started_at"],
            ui_finished_at=ui_timing["finished_at"],
            output_metrics=ui_timing["output_metrics"],
        )

    def _log_reverse_latency(
        self,
        completion: RealtimeCompletion,
        *,
        ui_enqueued_at: float,
        ui_started_at: float,
        ui_finished_at: float,
        output_metrics: Mapping[str, object] | None = None,
    ) -> None:
        task = completion.task
        if getattr(task, "source", None) not in {MIC_SOURCE, DESKTOP_SOURCE}:
            return
        diagnostics = getattr(task, "diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        output = output_metrics if isinstance(output_metrics, Mapping) else {}

        def seconds(name: str, default: float = 0.0) -> float:
            try:
                return max(0.0, float(diagnostics.get(name, default) or default))
            except (TypeError, ValueError):
                return max(0.0, float(default))

        def optional_ms(name: str) -> str:
            value = diagnostics.get(name)
            if value is None:
                return "na"
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return "na"
            if parsed < 0.0:
                return "na"
            return f"{parsed * 1000.0:.1f}"

        def output_wait(name: str) -> tuple[str, str]:
            attempted = bool(output.get(f"{name}_attempted", False))
            queued = bool(output.get(f"{name}_queued", False))
            if not attempted:
                return "na", "false"
            return ("pending" if queued else "not_queued"), str(queued).lower()

        context_s = seconds("translation_context_lookup_s") + seconds(
            "translation_prompt_build_s"
        )
        provider_s = seconds("translation_provider_s")
        if provider_s <= 0.0:
            provider_s = max(0.0, completion.translation_duration_s - context_s)
        speech_ended_at = seconds("speech_ended_at")
        total_anchor = speech_ended_at or task.submitted_at
        reorder_s = (
            completion.recognition_reorder_wait_s
            + completion.rewrite_reorder_wait_s
            + completion.ordered_delivery_wait_s
        )
        local_queue_s = (
            completion.asr_queue_wait_s
            + completion.rewrite_queue_wait_s
            + completion.translation_queue_wait_s
        )
        parsing_postprocess_s = seconds("translation_parse_s") + seconds(
            "translation_postprocess_s"
        )
        osc_wait_ms, osc_queued = output_wait("osc")
        tts_wait_ms, tts_queued = output_wait("tts")
        log_prefix = (
            "Reverse latency"
            if getattr(task, "source", None) == DESKTOP_SOURCE
            else "Realtime request latency"
        )
        logger.info(
            "%s source=%s sequence=%d stale_asr=%s stale_translation=%s "
            "vad_ms=%.1f capture_to_admission_ms=%.1f asr_queue_ms=%.1f "
            "asr_event_loop_queue_ms=%.1f asr_provider_queue_ms=%.1f "
            "asr_provider_ms=%.1f rewrite_queue_ms=%.1f rewrite_ms=%.1f "
            "rewrite_connection_pool_wait_ms=%s rewrite_dns_ms=%s "
            "rewrite_tcp_ms=%s rewrite_tls_ms=%s rewrite_response_header_ms=%s "
            "rewrite_provider_processing_ms=%s rewrite_first_token_ms=%s "
            "rewrite_full_response_ms=%s rewrite_parsing_postprocessing_ms=%.1f "
            "rewrite_provider_calls=%s rewrite_http_requests=%s "
            "translation_context_ms=%.1f "
            "translation_queue_ms=%.1f local_queue_ms=%.1f "
            "connection_pool_wait_ms=%s dns_ms=%s tcp_ms=%s tls_ms=%s "
            "response_header_ms=%s provider_processing_ms=%s "
            "translation_provider_ms=%.1f provider_calls=%s http_requests=%s "
            "streaming_first_token_ms=%s "
            "full_response_ms=%s parsing_postprocessing_ms=%.1f "
            "reorder_ms=%.1f ui_queue_ms=%.1f ui_delivery_ms=%.1f "
            "osc_wait_ms=%s osc_queued=%s tts_wait_ms=%s tts_queued=%s "
            "asr_connection_reused=%s translation_connection_reused=%s "
            "total_wall_ms=%.1f",
            log_prefix,
            task.source,
            task.sequence,
            completion.stale_asr,
            completion.stale_translation,
            seconds("vad_finalization_s") * 1000.0,
            max(0.0, task.submitted_at - seconds("segment_emitted_at", task.submitted_at))
            * 1000.0,
            completion.asr_queue_wait_s * 1000.0,
            seconds("asr_event_loop_queue_s") * 1000.0,
            seconds("asr_provider_queue_s") * 1000.0,
            seconds("asr_provider_s", completion.asr_duration_s) * 1000.0,
            completion.rewrite_queue_wait_s * 1000.0,
            completion.rewrite_duration_s * 1000.0,
            optional_ms("rewrite_pool_wait_s"),
            optional_ms("rewrite_dns_s"),
            optional_ms("rewrite_tcp_s"),
            optional_ms("rewrite_tls_s"),
            optional_ms("rewrite_response_headers_s"),
            optional_ms("rewrite_provider_processing_s"),
            optional_ms("rewrite_first_token_s"),
            optional_ms("rewrite_full_response_s"),
            (
                seconds("rewrite_parse_s")
                + seconds("rewrite_postprocess_s")
            )
            * 1000.0,
            diagnostics.get("rewrite_provider_calls", 0),
            diagnostics.get("rewrite_http_request_count", 0),
            context_s * 1000.0,
            completion.translation_queue_wait_s * 1000.0,
            local_queue_s * 1000.0,
            optional_ms("translation_pool_wait_s"),
            optional_ms("translation_dns_s"),
            optional_ms("translation_tcp_s"),
            optional_ms("translation_tls_s"),
            optional_ms("translation_response_headers_s"),
            optional_ms("translation_provider_processing_s"),
            provider_s * 1000.0,
            diagnostics.get("translation_provider_calls", 0),
            diagnostics.get("translation_http_request_count", 0),
            optional_ms("translation_first_token_s"),
            optional_ms("translation_full_response_s"),
            parsing_postprocess_s * 1000.0,
            reorder_s * 1000.0,
            max(0.0, ui_started_at - ui_enqueued_at) * 1000.0,
            max(0.0, ui_finished_at - ui_started_at) * 1000.0,
            osc_wait_ms,
            osc_queued,
            tts_wait_ms,
            tts_queued,
            diagnostics.get("asr_connection_reused", "unknown"),
            diagnostics.get("translation_connection_reused", "unknown"),
            max(0.0, ui_finished_at - total_anchor) * 1000.0,
        )

    def _commit_realtime_translation_context(
        self,
        completion: RealtimeCompletion,
    ) -> None:
        """Commit successful turns only after scheduler ordering is satisfied."""

        if not isinstance(completion, RealtimeCompletion):
            return
        task = completion.task
        payload = task.payload
        result = completion.result
        store = getattr(self, "_translation_context_store", None)
        if isinstance(store, TranslationContextStore) and not completion.successful:
            store.discard_staged(
                session_id=task.session_id,
                sequence=task.sequence,
                context_source=self._realtime_context_source(task.source),
            )
            return
        if (
            not isinstance(payload, _RealtimeAudioPayload)
            or not isinstance(result, RealtimeTranslationResult)
            or not isinstance(store, TranslationContextStore)
            or not result.api_translation_used
        ):
            if isinstance(store, TranslationContextStore):
                store.discard_staged(
                    session_id=task.session_id,
                    sequence=task.sequence,
                    context_source=self._realtime_context_source(task.source),
                )
            return

        source_language = str(payload.source_language or "auto")
        turns: list[tuple[str, str]] = []
        if task.source == DESKTOP_SOURCE:
            turns.append((payload.listen_target_language, result.translated_text))
        else:
            turns.append((payload.target_language, result.translated_text))
            if result.translated_text_2:
                turns.append(
                    (payload.second_target_language, result.translated_text_2)
                )
            if result.translated_text_3 and payload.third_target_language:
                turns.append(
                    (payload.third_target_language, result.translated_text_3)
                )

        seen_targets: set[str] = set()
        for target_language, translated_text in turns:
            normalized_target = str(target_language or "").strip()
            if not normalized_target or normalized_target in seen_targets:
                continue
            seen_targets.add(normalized_target)
            store.remember(
                session_id=task.session_id,
                text=result.original_text,
                translated=translated_text,
                src_lang=source_language,
                tgt_lang=normalized_target,
                context_source=self._realtime_context_source(task.source),
                sequence=task.sequence,
            )
        store.discard_staged(
            session_id=task.session_id,
            sequence=task.sequence,
            context_source=self._realtime_context_source(task.source),
        )

    @staticmethod
    def _realtime_output_request_context(
        completion: RealtimeCompletion,
        *,
        ui_delivered_at: float,
    ) -> dict[str, object]:
        task = completion.task
        diagnostics = task.diagnostics if isinstance(task.diagnostics, Mapping) else {}

        def timestamp(name: str, default: float) -> float:
            try:
                value = float(diagnostics.get(name, default) or default)
            except (TypeError, ValueError):
                value = float(default)
            return max(0.0, value)

        upstream_started_at = timestamp("speech_ended_at", task.submitted_at)
        ui_started_at = timestamp("ui_started_at", ui_delivered_at)
        return {
            "source": task.source,
            "session_id": task.session_id,
            "sequence": task.sequence,
            "upstream_started_at": upstream_started_at,
            "ui_delivered_at": ui_delivered_at,
            "ui_delivery_s": max(0.0, ui_delivered_at - ui_started_at),
        }

    @staticmethod
    def _realtime_osc_terminal_callback(
        *,
        source: str,
        session_id: int,
        sequence: int,
        diagnostics: dict[str, object] | None,
    ) -> Callable[[Mapping[str, object]], None]:
        """Build a terminal callback without retaining audio or provider state."""

        def callback(metrics: Mapping[str, object]) -> None:
            MainWindow._log_realtime_osc_terminal(
                source=source,
                session_id=session_id,
                sequence=sequence,
                diagnostics=diagnostics,
                metrics=metrics,
            )

        return callback

    @staticmethod
    def _log_realtime_osc_terminal(
        *,
        source: str,
        session_id: int,
        sequence: int,
        diagnostics: dict[str, object] | None,
        metrics: Mapping[str, object],
    ) -> None:
        if isinstance(diagnostics, dict):
            diagnostics.update(
                {f"osc_{key}": value for key, value in metrics.items()}
            )

        def milliseconds(name: str) -> str:
            value = metrics.get(name)
            if value is None:
                return "na"
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                return "na"
            if parsed < 0.0:
                return "na"
            return f"{parsed * 1000.0:.1f}"

        logger.info(
            "Realtime OSC terminal source=%s session_id=%s sequence=%d outcome=%s "
            "local_queue_ms=%s rate_limit_wait_ms=%s udp_send_ms=%s "
            "osc_wait_ms=%s ui_to_osc_ms=%s total_wall_ms=%s error_type=%s",
            source,
            session_id,
            sequence,
            metrics.get("outcome", "unknown"),
            milliseconds("queue_wait_s"),
            milliseconds("rate_wait_s"),
            milliseconds("send_s"),
            milliseconds("osc_total_s"),
            milliseconds("ui_to_osc_s"),
            milliseconds("pipeline_total_s"),
            metrics.get("error_type", ""),
        )

    def _deliver_scheduler_completion_ui(
        self,
        completion: RealtimeCompletion,
    ) -> Mapping[str, object] | None:
        """Apply a completion as one UI transaction on the Qt thread."""

        task = completion.task
        payload = task.payload
        if bool(getattr(completion, "cancelled", False)) or not self._realtime_task_active(task):
            return

        try:
            if completion.asr_error is not None:
                asr_error = completion.asr_error
                credential_error = isinstance(asr_error, ASRMissingAPIKeyError)
                message_key = (
                    "asr_credential_failure"
                    if credential_error
                    else (
                        "asr_queue_expired"
                        if bool(getattr(completion, "stale_asr", False))
                        else "asr_temporary_failure"
                    )
                )
                message = self._copy(message_key)
                logger.warning(
                    "Realtime ASR terminal failure source=%s sequence=%d error_type=%s "
                    "temporary=%s",
                    task.source,
                    task.sequence,
                    type(asr_error).__name__,
                    isinstance(asr_error, ASRTemporaryUnavailableError),
                )
                if credential_error:
                    self._prompt_for_missing_credential(("asr",))
                if task.source == DESKTOP_SOURCE:
                    self._set_bottom(
                        message,
                        "warning",
                        key=message_key,
                    )
                    self._show_listen_translation(message, source="error")
                else:
                    self._set_bottom(
                        message,
                        "danger" if credential_error else "warning",
                        key=message_key,
                    )
                    self._pulse_avatar_error()
                return

            if not isinstance(payload, _RealtimeAudioPayload):
                return

            error = completion.error
            if error is not None:
                if isinstance(error, RealtimeTranslationQueueExpiredError):
                    message = self._copy("translation_queue_expired")
                    self._set_bottom(
                        message,
                        "warning",
                        key="translation_queue_expired",
                    )
                    if task.source == DESKTOP_SOURCE:
                        self._show_listen_translation(message, source="error")
                    return
                friendly = self._format_translation_error(error)
                if task.source == DESKTOP_SOURCE:
                    self._set_bottom(
                        friendly.short_message,
                        key="translation_error",
                    )
                    self._show_listen_translation(
                        friendly.inline_message,
                        source="error",
                    )
                else:
                    self._set_bottom(
                        friendly.short_message,
                        "danger",
                        key="translation_error",
                    )
                    self._pulse_avatar_error()
                return

            result = completion.result
            if not isinstance(result, RealtimeTranslationResult):
                return

            output_message = result.output_message
            output_metrics: dict[str, object] = {
                "osc_attempted": False,
                "osc_queued": False,
                "tts_attempted": False,
                "tts_queued": False,
            }
            if task.source == DESKTOP_SOURCE:
                self._last_listen_result_at = time.monotonic()
                if output_message is not None:
                    self._dispatch_output_message(output_message, sinks=("overlay",))
                request_context = self._realtime_output_request_context(
                    completion,
                    ui_delivered_at=time.monotonic(),
                )
                if payload.send_to_chatbox:
                    output_metrics["osc_attempted"] = True
                    output_metrics["osc_queued"] = bool(
                        self._send_listen_chatbox(
                            result.chatbox_text,
                            session_id=task.session_id,
                            request_context=request_context,
                            completion_callback=self._realtime_osc_terminal_callback(
                                source=task.source,
                                session_id=task.session_id,
                                sequence=task.sequence,
                                diagnostics=(
                                    task.diagnostics
                                    if isinstance(task.diagnostics, dict)
                                    else None
                                ),
                            ),
                        )
                    )
            else:
                if output_message is not None:
                    self._dispatch_output_message(
                        output_message,
                        sinks=("ui", "overlay"),
                    )
                request_context = self._realtime_output_request_context(
                    completion,
                    ui_delivered_at=time.monotonic(),
                )
                osc_completion_callback = self._realtime_osc_terminal_callback(
                    source=task.source,
                    session_id=task.session_id,
                    sequence=task.sequence,
                    diagnostics=(
                        task.diagnostics
                        if isinstance(task.diagnostics, dict)
                        else None
                    ),
                )
                wait_for_tts = bool(
                    payload.send_to_chatbox
                    and output_message is not None
                    and self._should_wait_for_original_osc_after_tts(
                        payload.config_snapshot
                    )
                )
                if payload.send_to_chatbox and not wait_for_tts:
                    output_metrics["osc_attempted"] = True
                    output_metrics["osc_queued"] = bool(
                        self._send_chatbox_payload(
                            result.chatbox_text,
                            session_id=task.session_id,
                            request_context=request_context,
                            completion_callback=osc_completion_callback,
                        )
                    )
                if output_message is not None:
                    output_metrics["tts_attempted"] = True
                    tts_message = output_message
                    if isinstance(output_message, OutputMessage):
                        metadata = (
                            dict(output_message.metadata)
                            if isinstance(output_message.metadata, Mapping)
                            else {}
                        )
                        metadata["request_context"] = request_context
                        if wait_for_tts:
                            metadata["tts_completion_callback"] = (
                                self._delayed_original_chatbox_after_tts(
                                    output_message.original_text,
                                    session_id=task.session_id,
                                    request_context=request_context,
                                    completion_callback=osc_completion_callback,
                                )
                            )
                        tts_message = replace(output_message, metadata=metadata)
                    dispatch_result = self._dispatch_output_message(
                        tts_message,
                        sinks=("tts",),
                    )
                    if isinstance(dispatch_result, Mapping):
                        output_metrics["tts_queued"] = bool(
                            dispatch_result.get("tts", False)
                        )
                if wait_for_tts:
                    output_metrics["osc_attempted"] = True
                    output_metrics["osc_queued"] = bool(
                        output_metrics["tts_queued"]
                    )
            return output_metrics
        finally:
            self._restore_scheduler_status_ui(task.source, task.session_id)

    def _restore_scheduler_status_ui(self, source: str, session_id: int) -> None:
        if not self._realtime_session_active(session_id):
            return
        if source == MIC_SOURCE:
            self._restore_runtime_status("status_speaking", "status_translating")
        else:
            self._restore_floating_window_waiting_if_idle()

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
        provider = self._asr_for_source(source)
        scheduler = getattr(self, "_realtime_scheduler", None)
        if provider is None or scheduler is None:
            return
        if scheduler.should_yield_partial(self._asr_provider_key(provider)):
            return
        try:
            text = self._transcribe_for_source(source, audio, asr_lang, is_final=False)
            if not text or not self._running or session_id != self._listen_session:
                return
            if generation != self._partial_generation:
                return
            self._call_in_ui(
                lambda value=text, sid=session_id, gen=generation, src=source: (
                    self._on_partial_result(value, src)
                    if self._realtime_session_active(sid) and gen == self._partial_generation
                    else None
                )
            )
        except Exception as exc:
            logger.debug(
                "Partial transcription failed: %s",
                safe_exception_summary(exc),
            )

    def _asr_for_source(self, source: str):
        listen_asr = getattr(self, "_listen_asr", None)
        if source == DESKTOP_SOURCE and listen_asr is not None:
            return listen_asr
        return getattr(self, "_asr", None)

    def _asr_streaming_settings(self) -> dict[str, int | float]:
        """Return defensive, runtime-safe partial-ASR cadence settings."""

        asr_cfg = self._config.get("asr", {})
        if not isinstance(asr_cfg, Mapping):
            asr_cfg = {}
        streaming_cfg = asr_cfg.get("streaming", {})
        if not isinstance(streaming_cfg, Mapping):
            streaming_cfg = {}

        def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(streaming_cfg.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(value, maximum))

        def bounded_float(
            name: str,
            default: float,
            minimum: float,
            maximum: float,
        ) -> float:
            try:
                value = float(streaming_cfg.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(value, maximum))

        interval_ms = bounded_int("chunk_interval_ms", 250, 100, 5000)
        window_s = max(
            bounded_float("chunk_window_s", 1.6, 0.25, 30.0),
            interval_ms / 1000.0,
        )
        ring_buffer_s = max(
            bounded_float("ring_buffer_s", 4.0, 0.25, 60.0),
            window_s,
        )
        return {
            "chunk_interval_ms": interval_ms,
            "chunk_window_s": window_s,
            "ring_buffer_s": ring_buffer_s,
            "recent_speech_hold_s": bounded_float(
                "recent_speech_hold_s",
                0.8,
                0.0,
                5.0,
            ),
            "partial_stability_hits": bounded_int(
                "partial_stability_hits",
                2,
                1,
                10,
            ),
        }

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
        # Desktop partials had no isolated presentation lane and could overwrite
        # the microphone source text while consuming the same ASR model.  Keep
        # partial UI feedback microphone-only; desktop finals remain fully
        # concurrent in the staged scheduler.
        if source != MIC_SOURCE:
            return False
        asr = self._asr_for_source(source)
        if not bool(getattr(asr, "supports_partial", True)):
            return False
        if self._performance_profile() == "low_power":
            return False
        return self._asr_runtime_device_for_source(source) == "cuda"

    def _refresh_asr_transcribe_locks(self) -> None:
        guard = threading.Lock()
        locks: dict[Any, threading.Lock] = {}
        for asr in (getattr(self, "_asr", None), getattr(self, "_listen_asr", None)):
            if asr is not None:
                locks.setdefault(self._asr_provider_key(asr), threading.Lock())
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
        provider_key = self._asr_provider_key(asr)
        with guard:
            lock = locks.get(provider_key)
            if lock is None:
                lock = threading.Lock()
                locks[provider_key] = lock
            return lock

    def _desktop_listen_should_yield_to_mic(self) -> bool:
        """Partial desktop ASR yields; accepted final desktop sentences never do."""

        mic_asr = getattr(self, "_asr", None)
        listen_asr = getattr(self, "_listen_asr", None)
        if mic_asr is None or listen_asr is None:
            return False
        if self._asr_provider_key(mic_asr) != self._asr_provider_key(listen_asr):
            return False
        if self._mic_in_speech:
            return True
        scheduler = getattr(self, "_realtime_scheduler", None)
        return bool(
            scheduler is not None
            and scheduler.should_yield_partial(self._asr_provider_key(mic_asr))
        )

    def _transcribe_with_provider(
        self,
        asr: Any,
        source: str,
        audio: Any,
        asr_language: str | None,
        *,
        is_final: bool,
        request_context: Mapping[str, object] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if asr is None:
            raise RuntimeError("ASR is not ready")
        diagnostic_context = request_context or {"source": source}

        def recognize() -> str:
            realtime = getattr(asr, "transcribe_realtime", None)
            if is_final and callable(realtime):
                return str(
                    realtime(
                        audio,
                        language=asr_language,
                        is_final=True,
                        request_context=diagnostic_context,
                        cancel_event=cancel_event,
                    )
                    or ""
                ).strip()
            return str(
                asr.transcribe(
                    audio,
                    language=asr_language,
                    is_final=is_final,
                )
                or ""
            ).strip()

        if self._asr_provider_concurrency(asr) > 1:
            return recognize()
        lock = self._asr_transcribe_lock_for(asr)
        if is_final:
            lock.acquire()
        else:
            scheduler = getattr(self, "_realtime_scheduler", None)
            if (
                scheduler is not None
                and scheduler.should_yield_partial(self._asr_provider_key(asr))
            ):
                return ""
            if not lock.acquire(blocking=False):
                return ""
        try:
            return recognize()
        finally:
            lock.release()

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
        return self._transcribe_with_provider(
            self._asr_for_source(source),
            source,
            audio,
            asr_lang,
            is_final=is_final,
        )

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
                self._record_source_translation_success(DESKTOP_SOURCE)
        except Exception as exc:
            friendly = self._format_translation_error(exc)
            self._record_source_translation_failure(DESKTOP_SOURCE, friendly)
            raise
        if not self._running or session_id != self._listen_session:
            return
        self._last_listen_result_at = time.monotonic()
        output_message = result.output_message
        if output_message is not None:
            self._call_in_ui(lambda message=output_message: self._dispatch_output_message(message, sinks=("overlay",)))
        if self._listen_send_to_chatbox_enabled():
            self._call_in_ui(lambda payload=result.chatbox_text, sid=session_id: self._send_listen_chatbox(payload, session_id=sid))

    def _send_chatbox_payload(
        self,
        message: str,
        *,
        session_id: int | None = None,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> bool:
        if session_id is not None and (not self._running or session_id != self._listen_session):
            return False
        clean = _normalize_chatbox_text(message)
        if not clean:
            return False
        try:
            sent = self._ensure_output_dispatcher().send_chatbox_text(
                self._ensure_sender(),
                clean,
                request_context=request_context,
                completion_callback=completion_callback,
            )
            if not sent:
                self._set_bottom(self._copy("chatbox_send_not_queued"))
            return bool(sent)
        except Exception:
            logger.warning("Failed to send chatbox payload", exc_info=True)
            self._set_bottom(self._t("main_send_failed_detail"), "warning")
            self._pulse_avatar_error()
            return False

    def _send_listen_chatbox(
        self,
        message: str,
        *,
        session_id: int | None = None,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> bool:
        return self._send_chatbox_payload(
            message,
            session_id=session_id,
            request_context=request_context,
            completion_callback=completion_callback,
        )

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
                self._record_source_translation_success(source)
            output_message = result.output_message

            def deliver_outputs() -> None:
                if not self._running or session_id != self._listen_session:
                    return
                if output_message is not None:
                    self._dispatch_output_message(output_message, sinks=("ui", "overlay"))
                send_to_chatbox = self._mic_send_to_chatbox_enabled()
                wait_for_tts = bool(
                    send_to_chatbox
                    and output_message is not None
                    and self._should_wait_for_original_osc_after_tts(self._config)
                )
                if send_to_chatbox and not wait_for_tts:
                    self._send_chatbox_payload(
                        result.chatbox_text,
                        session_id=session_id,
                    )
                if output_message is not None:
                    tts_message = output_message
                    if wait_for_tts:
                        metadata = (
                            dict(output_message.metadata)
                            if isinstance(output_message.metadata, Mapping)
                            else {}
                        )
                        metadata["tts_completion_callback"] = (
                            self._delayed_original_chatbox_after_tts(
                                output_message.original_text,
                                session_id=session_id,
                            )
                        )
                        tts_message = replace(output_message, metadata=metadata)
                    self._dispatch_output_message(tts_message, sinks=("tts",))

            self._call_in_ui(deliver_outputs)
        except Exception as e:
            logger.debug(
                "Final transcription failed: %s",
                safe_exception_summary(e),
            )
            if source == DESKTOP_SOURCE:
                friendly = self._format_translation_error(e)
                self._call_in_ui(
                    lambda message=friendly.short_message: self._set_bottom(
                        message,
                        key="translation_error",
                    )
                )
                self._call_in_ui(lambda message=friendly.inline_message: self._show_listen_translation(message, source="error"))
            else:
                friendly = self._format_translation_error(e)
                self._record_source_translation_failure(source, friendly)
                self._call_in_ui(
                    lambda message=friendly.short_message: self._set_bottom(
                        message,
                        "danger",
                        key="translation_error",
                    )
                )
                self._call_in_ui(self._pulse_avatar_error)
        finally:
            if source == MIC_SOURCE:
                self._mic_in_speech = False
                self._call_in_ui(
                    lambda: self._restore_runtime_status("status_speaking", "status_translating")
                )
            elif source == DESKTOP_SOURCE:
                self._call_in_ui(self._restore_floating_window_waiting_if_idle)

    def _on_partial_result(self, text: str, source: str = MIC_SOURCE) -> None:
        if source != MIC_SOURCE:
            return
        clean = str(text or "").strip()
        if not clean:
            return

        required_hits = int(
            self._asr_streaming_settings()["partial_stability_hits"]
        )
        lock = self.__dict__.get("_partial_result_lock")
        if lock is None:
            lock = threading.Lock()
            self._partial_result_lock = lock
        with lock:
            candidate = str(self.__dict__.get("_partial_result_candidate", "") or "")
            hits = int(self.__dict__.get("_partial_result_hits", 0) or 0)
            if not candidate:
                candidate = clean
                hits = 1
            elif clean == candidate:
                hits += 1
            elif clean.startswith(candidate) or candidate.startswith(clean):
                candidate = clean
                hits += 1
            else:
                candidate = clean
                hits = 1
            self._partial_result_candidate = candidate
            self._partial_result_hits = hits
            stable = hits >= required_hits
        if stable:
            self._set_source_text(clean)

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
        metadata = message.metadata if isinstance(message.metadata, Mapping) else {}
        request_context = metadata.get("request_context")
        if not isinstance(request_context, Mapping):
            request_context = None
        completion_callback = metadata.get("tts_completion_callback")
        if not callable(completion_callback):
            completion_callback = None
        if source == "manual":
            kwargs: dict[str, object] = {
                "original_text": message.original_text,
                "translated_text": message.translated_text,
            }
            if request_context is not None:
                kwargs["request_context"] = request_context
            if completion_callback is not None:
                kwargs["completion_callback"] = completion_callback
            return self._auto_read_translation_result(
                **kwargs,
            )
        if source == "mic":
            kwargs = {
                "original_text": message.original_text,
                "translated_text": message.translated_text,
            }
            if request_context is not None:
                kwargs["request_context"] = request_context
            if completion_callback is not None:
                kwargs["completion_callback"] = completion_callback
            return self._auto_read_mic_translation(**kwargs)
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
            rewrite_coordinator=getattr(self, "_rewrite_coordinator", None),
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
        if self._prompt_for_missing_credential(("translation",)):
            self._set_status(self._t("status_error"), "danger", key="status_error")
            return
        controller = self._ensure_manual_translation_controller()
        controller.translator = getattr(self, "_translator", None)
        translation_cfg = self._config.get("translation", {})
        if not isinstance(translation_cfg, Mapping):
            translation_cfg = {}
        request = ManualTranslationRequest(
            text=src_text,
            source_language=getattr(self, "_current_src_lang", None),
            target_language=getattr(self, "_current_tgt_lang", "ja") or "ja",
            second_target_language=getattr(self, "_current_tgt_lang_2", "en") or "en",
            third_target_language=getattr(self, "_current_tgt_lang_3", "") or "",
            rewrite_typed_text=bool(
                translation_cfg.get("rewrite_typed_text", False)
            ),
            rewrite_style=normalize_asr_rewrite_style(
                translation_cfg.get(
                    "asr_rewrite_style",
                    ASR_REWRITE_DISABLED,
                )
            ),
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
        self._finish_manual_translation(
            success=False,
            error_message=friendly.short_message,
        )

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
        controller = self._ensure_manual_translation_controller()
        cancel = getattr(controller, "cancel_active_requests", None)
        self._manual_translation_generation = (
            cancel() if callable(cancel) else controller.invalidate()
        )
        self._translating = False
        friendly = self._format_translation_error(
            TimeoutError(f"Translation request timed out after {timeout_s:.0f}s")
        )
        self._show_tgt(friendly.short_message, is_error=True)
        self._pulse_avatar_error()
        self._finish_manual_translation(
            success=False,
            error_message=friendly.short_message,
        )
        self._refresh_translate_button()

    def _finish_manual_translation(
        self,
        *,
        success: bool = True,
        output_message: OutputMessage | None = None,
        error_message: str | None = None,
    ) -> None:
        send_after = self._manual_send_after_translate
        self._manual_send_after_translate = False
        callback = self._manual_done_callback
        self._manual_done_callback = None
        sent = False
        wait_for_tts = bool(
            success
            and send_after
            and self._should_wait_for_original_osc_after_tts(self._config)
        )
        if success and send_after and not wait_for_tts:
            sent = self._send_to_vrc()
        if success:
            if wait_for_tts:
                tts_callback = self._delayed_original_chatbox_after_tts(
                    (
                        output_message.original_text
                        if output_message is not None
                        else self._src_text
                    )
                )
                if output_message is not None:
                    metadata = (
                        dict(output_message.metadata)
                        if isinstance(output_message.metadata, Mapping)
                        else {}
                    )
                    metadata["tts_completion_callback"] = tts_callback
                    dispatch_result = self._dispatch_output_message(
                        replace(output_message, metadata=metadata),
                        sinks=("tts",),
                    )
                    sent = bool(dispatch_result.get("tts", False))
                else:
                    sent = self._auto_read_manual_translation(
                        completion_callback=tts_callback,
                    )
            elif output_message is not None:
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
            if error_message:
                self._set_bottom(
                    error_message,
                    "danger",
                    key="translation_error",
                )
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
        except Exception:
            logger.warning("Failed to send current translation to VRChat", exc_info=True)
            QMessageBox.critical(
                self,
                self._t("send_failed_title"),
                self._t("main_send_failed_detail"),
            )
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
        service.error.connect(self._on_osc_service_error)
        self._osc_service = service
        return service

    def _on_osc_service_error(self, message: str) -> None:
        logger.warning("VRChat OSC service error: %s", message)
        self._set_bottom(self._t("main_osc_error"), "warning")

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
        # Normalized production configs always contain sync_mute_self. Keep an
        # absent key opt-in here so minimal/embedder configs do not unexpectedly
        # bind a UDP listener.
        sync_mute_self = bool(osc_cfg.get("sync_mute_self", False))
        allow_avatar_control = bool(osc_cfg.get("allow_avatar_control", False))
        listener_enabled = bool(osc_cfg.get("listener_enabled", False))
        # Receiving MuteSelf/control parameters necessarily requires the OSC
        # socket. Treat either inbound feature as enabling the listener so an
        # otherwise contradictory settings combination cannot silently break
        # synchronization.
        if not (listener_enabled or sync_mute_self or allow_avatar_control):
            service.stop_listener()
            return
        try:
            service.start_listener(
                host=str(osc_cfg.get("receive_host", "127.0.0.1") or "127.0.0.1"),
                port=int(osc_cfg.get("receive_port", 9001)),
                sync_mute_self=sync_mute_self,
            )
        except Exception:
            logger.warning("Failed to start VRChat OSC listener", exc_info=True)
            self._set_bottom(self._t("main_osc_listener_failed"), "warning")

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
        desired = bool(muted)
        logger.info("Applying VRChat MuteSelf state to Mio: %s", desired)
        self._set_mic_muted(
            desired,
            bottom_key="mic_mute_on" if desired else "mic_mute_off",
        )

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
        key = "main_tts_enabled" if enabled else "main_tts_disabled"
        self._set_bottom(self._t(key), key=key)

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
        return resolved

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

    def _queue_tts_playback(
        self,
        text: str,
        *,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
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
            logger.debug(
                "TTS deduplicated (window_s=%.1f text_chars=%d)",
                tts_dedup_s,
                len(clean),
            )
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
        completion_lock = threading.Lock()
        completion_state: dict[str, object] = {
            "decision_made": False,
            "accepted": False,
            "delivered": False,
            "pending": None,
        }

        def deliver_completion(success: bool, message: str) -> None:
            if not callable(completion_callback):
                return
            try:
                completion_callback(bool(success), str(message or ""))
            except Exception:
                logger.debug(
                    "TTS completion callback failed",
                    exc_info=True,
                )

        def _done(success: bool, _message: str) -> None:
            terminal_at = time.monotonic()
            if suppress_echo:
                self._finish_listen_tts_echo_suppression(
                    LISTEN_TTS_ECHO_SUPPRESS_TAIL_S if success else 0.0
                )
            if not success:
                self._handle_tts_failure(_message)
            if isinstance(request_context, Mapping):
                try:
                    ui_delivered_at = float(
                        request_context.get("ui_delivered_at", terminal_at)
                        or terminal_at
                    )
                except (TypeError, ValueError):
                    ui_delivered_at = terminal_at
                try:
                    upstream_started_at = float(
                        request_context.get("upstream_started_at", ui_delivered_at)
                        or ui_delivered_at
                    )
                except (TypeError, ValueError):
                    upstream_started_at = ui_delivered_at
                logger.info(
                    "Realtime TTS terminal source=%s session_id=%s sequence=%s "
                    "outcome=%s tts_wait_ms=%.1f total_wall_ms=%.1f error_code=%s",
                    request_context.get("source", "-"),
                    request_context.get("session_id", "-"),
                    request_context.get("sequence", "-"),
                    "success" if success else "failed",
                    max(0.0, terminal_at - ui_delivered_at) * 1000.0,
                    max(0.0, terminal_at - upstream_started_at) * 1000.0,
                    tts_error_code(_message) if not success else "",
                )
            should_deliver = False
            with completion_lock:
                if not bool(completion_state["decision_made"]):
                    completion_state["pending"] = (
                        bool(success),
                        str(_message or ""),
                    )
                elif (
                    bool(completion_state["accepted"])
                    and not bool(completion_state["delivered"])
                ):
                    completion_state["delivered"] = True
                    should_deliver = True
            if should_deliver:
                deliver_completion(bool(success), str(_message or ""))

        engine_cfg = self._current_tts_engine_config()
        speak_kwargs = {
            "callback": _done,
        }
        if request_context is not None:
            speak_kwargs["request_context"] = request_context
        try:
            accepted = manager.speak(
                clean,
                voice,
                self._safe_tts_rate(engine_cfg.get("rate")),
                self._safe_tts_volume(engine_cfg.get("volume")),
                **speak_kwargs,
            )
        except TypeError as exc:
            if "request_context" not in str(exc) or request_context is None:
                raise
            accepted = manager.speak(
                clean,
                voice,
                self._safe_tts_rate(engine_cfg.get("rate")),
                self._safe_tts_volume(engine_cfg.get("volume")),
                callback=_done,
            )
        pending_completion = None
        with completion_lock:
            completion_state["decision_made"] = True
            completion_state["accepted"] = bool(accepted)
            pending_completion = completion_state["pending"]
            completion_state["pending"] = None
            should_deliver_pending = bool(
                accepted
                and isinstance(pending_completion, tuple)
                and len(pending_completion) == 2
                and not completion_state["delivered"]
            )
            if should_deliver_pending:
                completion_state["delivered"] = True
        if should_deliver_pending and isinstance(pending_completion, tuple):
            deliver_completion(
                bool(pending_completion[0]),
                str(pending_completion[1] or ""),
            )
        if not accepted and suppress_echo:
            self._finish_listen_tts_echo_suppression(0.0)
        return bool(accepted)

    def _handle_tts_failure(self, message: object) -> None:
        if self._current_tts_engine().strip().lower() != "qwen_tts":
            return
        code = tts_error_code(message)
        if code in {"cancelled", "stopped"}:
            return
        message_key = {
            "authentication": "qwen_tts_auth_error",
            "configuration": "qwen_tts_configuration_error",
            "network": "qwen_tts_network_error",
            "timeout": "qwen_tts_timeout_error",
            "rate_limit": "qwen_tts_rate_limit_error",
            "unsupported_model": "qwen_tts_model_error",
            "invalid_endpoint": "qwen_tts_endpoint_error",
            "safety": "qwen_tts_safety_error",
            "queue_full": "qwen_tts_busy_error",
            "pipeline_full": "qwen_tts_busy_error",
            "suspended": "qwen_tts_busy_error",
            "playback": "qwen_tts_playback_error",
            "provider": "qwen_tts_provider_error",
            "invalid_input": "qwen_tts_input_error",
            "unavailable": "qwen_tts_unavailable_error",
        }.get(code, "qwen_tts_provider_error")

        def report() -> None:
            self._set_bottom(
                self._copy(message_key),
                "warning",
                key=message_key,
            )

        self._call_in_ui(report, priority=True)

    def _manual_translation_tts_text(self, *, original_text: str, translated_text: str) -> str:
        if self._get_output_format() == "original_only":
            return original_text
        return translated_text or original_text

    @staticmethod
    def _should_wait_for_original_osc_after_tts(
        config: Mapping[str, object] | None,
    ) -> bool:
        if not isinstance(config, Mapping):
            return False
        translation = config.get("translation", {})
        if not isinstance(translation, Mapping):
            return False
        output_format = normalize_output_format(translation.get("output_format"))
        if output_format != OUTPUT_FORMAT_ORIGINAL_ONLY_READ_TRANSLATION:
            return False
        return bool(
            translation.get(
                ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
                True,
            )
        )

    def _delayed_original_chatbox_after_tts(
        self,
        original_text: str,
        *,
        session_id: int | None = None,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[Mapping[str, object]], None] | None = None,
    ) -> Callable[[bool, str], None]:
        delivered_lock = threading.Lock()
        delivered = False

        def on_tts_terminal(success: bool, _message: str) -> None:
            nonlocal delivered
            if not success:
                return
            with delivered_lock:
                if delivered:
                    return
                delivered = True

            def send_original() -> None:
                self._send_chatbox_payload(
                    original_text,
                    session_id=session_id,
                    request_context=request_context,
                    completion_callback=completion_callback,
                )

            self._call_in_ui(
                send_original,
                delay_ms=ORIGINAL_ONLY_READ_TRANSLATION_OSC_DELAY_MS,
            )

        return on_tts_terminal

    def _auto_read_translation_result(
        self,
        *,
        original_text: str,
        translated_text: str,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        tts_cfg = self._tts_config()
        if not self._sync_tts_enabled_from_config():
            return False
        if not bool(tts_cfg.get("auto_read", True)):
            return False
        text = self._manual_translation_tts_text(
            original_text=original_text,
            translated_text=translated_text,
        )
        if request_context is None:
            if completion_callback is None:
                return self._queue_tts_playback(text)
            return self._queue_tts_playback(
                text,
                completion_callback=completion_callback,
            )
        if completion_callback is None:
            return self._queue_tts_playback(
                text,
                request_context=request_context,
            )
        return self._queue_tts_playback(
            text,
            request_context=request_context,
            completion_callback=completion_callback,
        )

    def _auto_read_mic_translation(
        self,
        *,
        original_text: str,
        translated_text: str,
        request_context: Mapping[str, object] | None = None,
        completion_callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        return self._auto_read_translation_result(
            original_text=original_text,
            translated_text=translated_text,
            request_context=request_context,
            completion_callback=completion_callback,
        )

    def _auto_read_manual_translation(
        self,
        *,
        completion_callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        return self._auto_read_translation_result(
            original_text=self._src_text,
            translated_text=self._last_tgt_text,
            completion_callback=completion_callback,
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
        self._schedule_tts_prewarm()

    def _schedule_tts_prewarm(self) -> None:
        tts_cfg = self._tts_config()
        engine = self._current_tts_engine().lower()
        engine_cfg = tts_cfg.get(engine, {})
        if not isinstance(engine_cfg, Mapping):
            engine_cfg = {}
        if (
            not bool(tts_cfg.get("enabled", False))
            or not bool(tts_cfg.get("auto_read", True))
            or engine not in {"xtts", "xtts_v2", "xtts-v2", "xttsts"}
            or not bool(engine_cfg.get("prewarm", True))
            or self._performance_profile() == "low_power"
        ):
            return
        session_id = self._listen_session
        QTimer.singleShot(
            250,
            lambda sid=session_id: self._prewarm_tts_for_session(sid),
        )

    def _prewarm_tts_for_session(self, session_id: int) -> None:
        if not self._realtime_session_active(session_id):
            return
        manager = self._ensure_tts_manager()
        if manager is None:
            return
        voice = self._tts_voice_for_engine(manager)
        prewarm = getattr(manager, "prewarm", None)
        if callable(prewarm):
            prewarm(voice)

    def _on_start_error(self, msg: str) -> None:
        logger.warning("Listening startup failed: %s", msg)
        self._set_status(self._t("status_error"), "danger", key="status_error")
        self._refresh_start_button()
        QMessageBox.critical(
            self,
            self._t("listen_start_failed_title"),
            self._t("main_start_failed_detail"),
        )

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
                    text = f"{text} {format_locale_percent(float(progress) * 100, self._ui_lang)}"
                self._set_bottom(text, "accent")
                self._show_bottom_progress(float(progress) if progress is not None else None, indeterminate=progress is None)
                return
            if stage == "download_retry":
                attempt = int(event.get("attempt") or 1)
                maximum = int(event.get("max_attempts") or attempt)
                self._set_bottom(
                    self._t(
                        "model_download_retrying",
                        attempt=format_locale_number(attempt, self._ui_lang),
                        maximum=format_locale_number(maximum, self._ui_lang),
                    ),
                    "warning",
                )
                self._show_bottom_progress(None, indeterminate=True)
                return
            if stage == "ready":
                self._set_bottom(self._t("model_ready"), "success", key="model_ready")
                self._hide_bottom_progress()
                return
            msg = str(event.get("message", "")).strip()
            if msg:
                logger.warning("Model preparation failed: %s", msg)
                self._set_bottom(
                    self._t("model_download_hint_error"),
                    "danger",
                    key="model_download_hint_error",
                )
        else:
            msg = str(event).strip()
            if msg:
                logger.warning("Model preparation failed: %s", msg)
                self._set_bottom(
                    self._t("model_download_hint_error"),
                    "danger",
                    key="model_download_hint_error",
                )

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
            value = self._clean_status_text(self._copy(key))
        if (key and key.startswith("qwen_tts_")) or key == "update_install_success_message":
            return value
        lowered = value.lower()
        if any(token in lowered for token in ("network", "connection", "timeout", "timed out", "dns", "socket", "网络")):
            return self._copy("report_network_error")
        if any(token in lowered for token in ("quota", "rate limit", "429", "too many requests", "限流", "配额")):
            return self._copy("report_request_limited")
        if any(token in lowered for token in ("api key", "apikey", "unauthorized", "401", "403", "配置", "密钥")):
            return self._copy("report_config_error")
        if (
            color == "danger"
            and key != "translation_error"
            and (len(value) > 52 or ":" in value or "：" in value)
        ):
            return self._copy("report_runtime_error")
        for separator in ("。", ".", "，", ",", "；", ";", "\n"):
            if separator in value and len(value) > 42:
                value = value.split(separator, 1)[0].strip()
                break
        max_length = 80 if key == "translation_error" else 52
        if len(value) > max_length:
            value = value[: max_length - 3].rstrip() + "..."
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
            self._start_btn.setFixedHeight(SIDE_PRIMARY_BUTTON_HEIGHT)
            self._start_btn.style().unpolish(self._start_btn)
            self._start_btn.style().polish(self._start_btn)
        if getattr(self, "_quick_controls_hint", None):
            self._quick_controls_hint.setText(self._t("status_running") if self._running else self._t("status_ready"))

    def _refresh_mic_mute_button(self) -> None:
        if self._mute_btn:
            self._mute_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
            self._mute_btn.setText(self._copy("mic_mute_on" if self._mic_muted else "mic_mute_off"))
            self._mute_btn.setProperty("active", self._mic_muted)
            self._mute_btn.style().unpolish(self._mute_btn)
            self._mute_btn.style().polish(self._mute_btn)


    def _refresh_theme_button(self) -> None:
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
            self._swap_lang_btn.setText("" if not swap_icon.isNull() else self._copy("swap_languages"))
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
    def _call_in_ui(
        self,
        callback,
        delay_ms: int = 0,
        *,
        priority: bool = False,
    ) -> bool:
        if self._destroying:
            return False
        if threading.get_ident() != self._ui_thread_id:
            work_queue = (
                getattr(self, "_ui_priority_callback_queue", None)
                if priority
                else self._ui_callback_queue
            )
            if work_queue is None:
                work_queue = self._ui_callback_queue
            try:
                work_queue.put_nowait((delay_ms, callback))
                self.sig_ui_callback.emit()
                return True
            except queue.Full:
                self._ui_callback_drop_count = (
                    getattr(self, "_ui_callback_drop_count", 0) + 1
                )
                dropped = self._ui_callback_drop_count
                if priority or dropped == 1 or dropped & (dropped - 1) == 0:
                    logger.warning(
                        "UI callback backpressure dropped callback priority=%s "
                        "dropped_total=%d",
                        priority,
                        dropped,
                    )
                return False
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
        priority_queue = getattr(self, "_ui_priority_callback_queue", None)
        while processed < UI_CALLBACK_DRAIN_LIMIT:
            item = None
            if priority_queue is not None:
                try:
                    item = priority_queue.get_nowait()
                except queue.Empty:
                    pass
            if item is None:
                try:
                    item = self._ui_callback_queue.get_nowait()
                except queue.Empty:
                    break
            delay_ms, callback = item
            QTimer.singleShot(delay_ms, lambda cb=callback: self._run_ui_callback(cb))
            processed += 1
        # Re-arm only after doing work; producers also emit a wake-up signal.
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
        devices = []
        if refresh:
            try:
                devices = _list_microphone_devices()
            except Exception:
                logger.debug("Failed to enumerate microphone devices", exc_info=True)
                devices = []
            self._cache_microphone_devices(devices)
        if not devices and self._devices:
            devices = [
                {"name": name, "index": index}
                for name, index in self._devices.items()
            ]
        if mode == "auto":
            default_name = (
                self._current_default_input_device_name(devices)
                if refresh
                else getattr(self, "_default_mic_device_name", None)
            )
            if default_name:
                return self._match_mic_input_device_name(default_name) or default_name
            for name in self._devices:
                if name:
                    return name
            return None
        configured_name = str(audio_cfg.get("input_device") or "").strip() or None
        return self._match_mic_input_device_name(configured_name) or configured_name

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

    def _cache_microphone_devices(self, devices: list[dict]) -> None:
        self._devices = {
            str(device.get("name", "")).strip(): int(device.get("index", -1))
            for device in devices
            if str(device.get("name", "")).strip()
        }
        marked_defaults = [
            str(device.get("name", "")).strip()
            for device in devices
            if bool(device.get("is_default"))
            and str(device.get("name", "")).strip()
        ]
        self._default_mic_device_name = marked_defaults[0] if len(marked_defaults) == 1 else None

    def _load_desktop_devices(self, *, force_refresh: bool = False) -> None:
        devices: dict[str, int] = {}
        try:
            enumerated = _list_desktop_output_devices(
                force_refresh=force_refresh
            )
        except Exception:
            logger.exception("Desktop output device enumeration failed")
            enumerated = []
        for device in enumerated:
            name = str(device.get("name", "")).strip()
            if name and name not in devices:
                devices[name] = int(device.get("index", -1))
        if not devices and self._desktop_devices:
            logger.warning("Desktop loopback device refresh returned empty; keeping cached devices")
            return
        self._desktop_devices = devices
        if not devices:
            try:
                from src.audio.desktop_recorder import loopback_device_diagnostics

                logger.error(
                    "No desktop output device detected; diagnostics=%s",
                    loopback_device_diagnostics(),
                )
            except Exception:
                logger.debug("Failed to collect desktop output diagnostics", exc_info=True)

    def _apply_loaded_devices(self, devices: list[dict]) -> None:
        self._devices_loading = False
        self._cache_microphone_devices(devices)
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
            lock = self.__dict__.get("_partial_result_lock")
            if lock is None:
                lock = threading.Lock()
                self._partial_result_lock = lock
            with lock:
                self._partial_result_candidate = ""
                self._partial_result_hits = 0
        if source is None or source == DESKTOP_SOURCE:
            self._desktop_in_speech = False
            self._listen_in_speech = False

    def _stop_hotkeys(self) -> None:
        hotkeys = (
            getattr(self, "_text_input_hotkey", None),
            getattr(self, "_mic_mute_hotkey", None),
        )
        self._text_input_hotkey = None
        self._mic_mute_hotkey = None
        for hk in hotkeys:
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
            self._mic_mute_hotkey.start(wait_for_ready=False)
        except Exception as e:
            logger.warning("Failed to register mic mute hotkey: %s", e)

        try:
            self._text_input_hotkey = GlobalHotkey(
                text_input_cfg.get("hotkey", DEFAULT_TEXT_INPUT_HOTKEY),
                lambda: self._call_in_ui(self._open_text_input_popup),
                name="text-input",
                hotkey_id=2,
            )
            self._text_input_hotkey.start(wait_for_ready=False)
        except Exception as e:
            logger.warning("Failed to register text input hotkey: %s", e)

    def _on_config_saved(self) -> None:
        was_running = self._running
        startup_thread = getattr(self, "_startup_thread", None)
        try:
            was_starting = startup_thread is not None and startup_thread.is_alive()
        except Exception:
            was_starting = startup_thread is not None
        if was_running or was_starting:
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
        self._current_asr_lang = src_lang
        self._main_theme_preference = _main_theme_preference_from_config(self._config)
        self._main_theme = _resolve_main_theme(self._main_theme_preference)
        app = QApplication.instance()
        if app is not None:
            apply_application_font(app, self._config)
        self._desktop_capture_enabled = bool(
            self._config.get("vrc_listen", {}).get("enabled", False)
        )
        self._listen_overlay_enabled = bool(
            self._config.get("vrc_listen", {}).get("show_overlay", False)
        )
        overlay_service = getattr(self, "_overlay_service", None)
        if overlay_service is not None:
            overlay_service.set_enabled(self._listen_overlay_enabled, reveal=False)
        self._mode_manager = ModeManager(self._config)
        mode_change = self._mode_manager.apply_current_mode()
        self._sync_tts_enabled_from_config()
        self._resolve_virtual_output_async()
        central = self.centralWidget()
        if isinstance(central, BackgroundWidget):
            central.set_theme(self._main_theme)
            central.set_background_path(self._background_image_path())
        self._clear_cached_translator()
        self._close_asr_providers()
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
        if previous_lang != self._ui_lang:
            self._refresh_open_window_languages()
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
        self._stop_hotkeys()
        self._register_hotkeys()
        self._restart_background_provider_initialization()
        if was_running or was_starting:
            self._schedule_pipeline_start_retry(100)

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

    def _reset_translation_failure_backoff(self, source: str | None = None) -> None:
        lock = self.__dict__.get("_translation_state_lock")
        if lock is None:
            lock = threading.Lock()
            self._translation_state_lock = lock
        with lock:
            states = self.__dict__.setdefault("_translation_backoff_by_source", {})
            if source is not None:
                states.pop(str(source), None)
                return
            states.clear()
            self._translation_failure_streak = 0
            self._translation_cooldown_until = 0.0
            self._translation_cooldown_category = None

    def _translation_cooldown_remaining(self, source: str | None = None) -> float:
        with self._translation_state_lock:
            if source is not None:
                states = self.__dict__.get("_translation_backoff_by_source", {})
                state = states.get(str(source)) if isinstance(states, dict) else None
                if isinstance(state, dict):
                    return max(
                        0.0,
                        float(state.get("until", 0.0) or 0.0) - time.monotonic(),
                    )
            return max(0.0, self._translation_cooldown_until - time.monotonic())

    def _translation_cooldown_active(self, source: str) -> bool:
        remaining = self._translation_cooldown_remaining(source)
        if remaining <= 0:
            return False
        logger.debug(
            "Skipping translation while cooldown is active (source=%s remaining_s=%.1f)",
            source,
            remaining,
        )
        return True

    def _record_translation_success(self, source: str | None = None) -> None:
        self._reset_translation_failure_backoff(source)

    def _record_translation_failure(self, friendly, source: str | None = None) -> float:
        base_cooldown = TRANSLATION_FAILURE_COOLDOWN_S.get(friendly.category, 0.0)
        if base_cooldown <= 0:
            return 0.0
        now = time.monotonic()
        with self._translation_state_lock:
            if source is not None:
                states = self.__dict__.setdefault("_translation_backoff_by_source", {})
                state = states.setdefault(
                    str(source),
                    {"streak": 0, "until": 0.0, "category": None},
                )
                if now > float(state.get("until", 0.0) or 0.0) + TRANSLATION_FAILURE_MAX_COOLDOWN_S:
                    state["streak"] = 0
                state["streak"] = int(state.get("streak", 0) or 0) + 1
                multiplier = 2 ** min(int(state["streak"]) - 1, 3)
                cooldown_s = min(
                    base_cooldown * multiplier,
                    TRANSLATION_FAILURE_MAX_COOLDOWN_S,
                )
                state["until"] = now + cooldown_s
                state["category"] = friendly.category
                return cooldown_s
            if now > self._translation_cooldown_until + TRANSLATION_FAILURE_MAX_COOLDOWN_S:
                self._translation_failure_streak = 0
            self._translation_failure_streak += 1
            multiplier = 2 ** min(self._translation_failure_streak - 1, 3)
            cooldown_s = min(base_cooldown * multiplier, TRANSLATION_FAILURE_MAX_COOLDOWN_S)
            self._translation_cooldown_until = now + cooldown_s
            self._translation_cooldown_category = friendly.category
            return cooldown_s

    def _record_source_translation_success(self, source: str) -> None:
        try:
            self._record_translation_success(source)
        except TypeError:
            # Compatibility with embedding/test shims that replace the legacy
            # no-argument callback.
            self._record_translation_success()

    def _record_source_translation_failure(self, source: str, friendly) -> float:
        try:
            return float(self._record_translation_failure(friendly, source) or 0.0)
        except TypeError:
            return float(self._record_translation_failure(friendly) or 0.0)

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

    # ----------------------------------------------------------------
    # Compact restored main UI
    # ----------------------------------------------------------------
    def _disable_native_status_bar(self) -> None:
        bar = self.statusBar()
        bar.hide()
        bar.setMaximumHeight(0)
        bar.setSizeGripEnabled(False)

    def _ui_scale(self) -> float:
        app = QApplication.instance()
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        try:
            dpi = float(screen.logicalDotsPerInch()) if screen is not None else BASE_DPI
        except Exception:
            dpi = BASE_DPI
        scale = dpi / BASE_DPI if dpi > 0 else 1.0
        return max(MIN_MAIN_UI_SCALE, min(MAX_MAIN_UI_SCALE, scale))

    def _scaled(self, value: int | float) -> int:
        return max(1, int(round(float(value) * self._ui_scale())))

    def _main_scale_styles(self) -> str:
        return ""

    def _configure_language_combo(self, combo: QComboBox | None, base_width: int) -> None:
        if combo is None:
            return
        combo.setMinimumWidth(self._scaled(max(92, base_width - 28)))
        combo.setMaximumWidth(10000)
        combo.setFixedHeight(self._scaled(30))
        combo.setMinimumContentsLength(8)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _fit_button_to_text(
        btn: QPushButton | None,
        *,
        min_width: int,
        height: int,
        padding: int = 24,
        icon_gap: int = 0,
    ) -> None:
        if btn is None:
            return
        text_width = btn.fontMetrics().horizontalAdvance(btn.text())
        btn.setMinimumWidth(max(min_width, text_width + padding + icon_gap))
        btn.setFixedHeight(height)

    def _apply_adaptive_layout(self, *, force: bool = False) -> None:
        if getattr(self, "_src_lang_combo", None) is None:
            return
        width = max(1, self.width())
        side = getattr(self, "_side_panel", None)
        if side is not None:
            side_min = max(240, self._scaled(240))
            side_max = max(260, self._scaled(260))
            side.setMinimumWidth(side_min)
            side.setMaximumWidth(side_max)
        for combo, base in (
            (getattr(self, "_src_lang_combo", None), LANG_COMBO_SOURCE_WIDTH),
            (getattr(self, "_tgt_lang_combo", None), LANG_COMBO_TARGET_WIDTH),
            (getattr(self, "_tgt_lang2_combo", None), LANG_COMBO_TARGET_WIDTH),
        ):
            self._configure_language_combo(combo, base)
        for btn in (
            getattr(self, "_manual_input_btn", None),
            getattr(self, "_translate_btn", None),
            getattr(self, "_clear_btn", None),
            getattr(self, "_copy_source_btn", None),
            getattr(self, "_copy_result_btn", None),
            getattr(self, "_send_to_vrc_btn", None),
            getattr(self, "_listen_overlay_btn", None),
            getattr(self, "_guide_btn_secondary", None),
        ):
            if btn is not None:
                btn.setMaximumWidth(10000)
        if getattr(self, "_header_frame", None) is not None:
            self._header_frame.setFixedHeight(self._scaled(HEADER_HEIGHT))
        if getattr(self, "_flow_panel", None) is not None:
            self._flow_panel.setMaximumHeight(self._scaled(LANG_FLOW_MAX_HEIGHT))
        for panel in (getattr(self, "_left_panel", None), getattr(self, "_right_panel", None)):
            if panel is not None:
                panel.setMaximumHeight(self._scaled(TEXT_PANEL_MAX_HEIGHT))
        for pane in (getattr(self, "_src_text_widget", None), getattr(self, "_tgt_text_widget", None)):
            if pane is not None:
                pane.setMinimumHeight(self._scaled(TEXT_PANE_MIN_HEIGHT))
                pane.setMaximumHeight(self._scaled(TEXT_PANEL_MAX_HEIGHT))
        if getattr(self, "_action_strip", None) is not None:
            self._action_strip.setMaximumHeight(self._scaled(ACTION_STRIP_MAX_HEIGHT))
        if width < 910:
            for label in (getattr(self, "_status_label", None), getattr(self, "_assist_label", None)):
                if label is not None:
                    label.setVisible(False)
        else:
            for label in (getattr(self, "_status_label", None), getattr(self, "_assist_label", None)):
                if label is not None:
                    label.setVisible(True)

    def _build_ui(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_app_stylesheet(self._main_theme))
        self.setStyleSheet(build_main_window_styles(self._main_theme))
        apply_window_chrome_theme(self, self._main_theme)

        background = BackgroundWidget("", self)
        background.set_theme(self._main_theme)
        self.setCentralWidget(background)

        outer_layout = QVBoxLayout(background)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        shell = QFrame(background)
        shell.setObjectName("appChrome")
        shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_shadow(shell, blur=30, alpha=18 if self._main_theme == "light" else 64, y_offset=10)
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
        header.setFixedHeight(HEADER_HEIGHT)
        self._header_frame = header
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(10)
        self._header_layout = layout

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self._brand_layout = brand
        icon_label = QLabel()
        icon = self._load_icon_pixmap(APP_ICON_PNG_FILE, 40)
        if icon is not None:
            icon_label.setPixmap(icon)
        icon_label.setFixedSize(40, 40)
        self._app_icon_label = icon_label
        brand.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        self._brand_text_layout = brand_text
        self._brand_title_label = QLabel(self._t("window_title"))
        self._brand_title_label.setObjectName("brandTitle")
        self._brand_title_label.setWordWrap(False)
        self._brand_title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._creator_banner_label = QLabel(self._copy("creator_banner_compact"))
        self._creator_banner_label.setObjectName("brandSubtitle")
        self._creator_banner_label.setWordWrap(False)
        brand_text.addWidget(self._brand_title_label)
        brand_text.addWidget(self._creator_banner_label)
        brand.addLayout(brand_text)
        layout.addLayout(brand, 1)

        self._status_label = QLabel(self._t("status_ready"))
        self._status_label.setObjectName("statusPill")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setFixedHeight(28)
        self._status_label.setMinimumWidth(88)
        self._status_label.setMaximumWidth(150)
        self._status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._update_badge_btn = QPushButton(self._t("update_badge"))
        self._update_badge_btn.setObjectName("updateBadge")
        self._update_badge_btn.setFixedHeight(28)
        self._update_badge_btn.clicked.connect(self._open_update_window)
        self._update_badge_btn.hide()
        layout.addWidget(self._update_badge_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._ui_lang_combo = NoWheelComboBox()
        self._ui_lang_combo.setObjectName("headerCombo")
        self._ui_lang_combo.setFixedSize(HEADER_ACTION_WIDTH, 34)
        self._ui_lang_combo.addItems([label for label, _ in UI_LANGUAGE_OPTIONS])
        self._ui_lang_combo.currentTextChanged.connect(self._on_ui_lang_selected)
        action_row.addWidget(self._ui_lang_combo)

        self._settings_btn = QPushButton(self._copy("settings_short"))
        self._settings_btn.setObjectName("headerButton")
        self._settings_btn.setFixedSize(HEADER_ACTION_WIDTH, 34)
        self._settings_btn.clicked.connect(self.show_settings)
        action_row.addWidget(self._settings_btn)

        self._tweaks_btn = QPushButton("")
        self._tweaks_btn.setObjectName("headerButton")
        self._tweaks_btn.setFixedSize(HEADER_ACTION_WIDTH, 34)
        self._tweaks_btn.clicked.connect(self._toggle_tweaks_panel)
        action_row.addWidget(self._tweaks_btn)

        self._theme_btn = QPushButton("")
        self._theme_btn.setObjectName("themeIconButton")
        self._theme_btn.setFixedSize(34, 34)
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
        layout.setSpacing(8)
        self._content_layout = layout
        layout.addWidget(self._build_translation_card(), 1)
        layout.addWidget(self._build_side_card(), 0)
        return content

    def _build_translation_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("translationCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self._translation_card_layout = layout

        flow_panel = QFrame()
        flow_panel.setObjectName("langFlowPanel")
        flow_panel.setMaximumHeight(LANG_FLOW_MAX_HEIGHT)
        self._flow_panel = flow_panel
        flow_layout = QHBoxLayout(flow_panel)
        flow_layout.setContentsMargins(10, 7, 10, 7)
        flow_layout.setSpacing(7)
        self._flow_layout = flow_layout
        self._flow_source_row = flow_layout
        self._flow_target_row = flow_layout

        self._src_header_label = QLabel(self._copy("source_lang_short"))
        self._src_header_label.setObjectName("sectionTitleMain")
        flow_layout.addWidget(self._src_header_label)
        self._src_lang_combo = NoWheelComboBox()
        self._src_lang_combo.setObjectName("langCombo")
        self._configure_language_combo(self._src_lang_combo, LANG_COMBO_SOURCE_WIDTH)
        self._src_lang_combo.currentTextChanged.connect(self._on_src_lang_change)
        flow_layout.addWidget(self._src_lang_combo, 2)
        self._swap_lang_btn = QPushButton("")
        self._swap_lang_btn.setObjectName("swapIconButton")
        self._swap_lang_btn.setFixedSize(LANG_ROW_BUTTON_SIZE, LANG_ROW_BUTTON_SIZE)
        self._swap_lang_btn.setIconSize(QSize(15, 15))
        self._swap_lang_btn.clicked.connect(self._swap_langs)
        flow_layout.addWidget(self._swap_lang_btn)
        self._char_label = None

        self._tgt_header_label = QLabel(self._copy("translation_lang_1_short"))
        self._tgt_header_label.setObjectName("sectionTitleMain")
        flow_layout.addWidget(self._tgt_header_label)
        self._tgt_lang_combo = NoWheelComboBox()
        self._tgt_lang_combo.setObjectName("langCombo")
        self._configure_language_combo(self._tgt_lang_combo, LANG_COMBO_TARGET_WIDTH)
        self._tgt_lang_combo.currentTextChanged.connect(self._on_tgt_lang_change)
        flow_layout.addWidget(self._tgt_lang_combo, 2)

        self._tgt2_header_label = QLabel(self._copy("translation_lang_2_short"))
        self._tgt2_header_label.setObjectName("sectionTitleMain")
        flow_layout.addWidget(self._tgt2_header_label)
        self._tgt_lang2_combo = NoWheelComboBox()
        self._tgt_lang2_combo.setObjectName("langCombo")
        self._configure_language_combo(self._tgt_lang2_combo, LANG_COMBO_TARGET_WIDTH)
        self._tgt_lang2_combo.currentTextChanged.connect(self._on_tgt_lang2_change)
        flow_layout.addWidget(self._tgt_lang2_combo, 2)
        layout.addWidget(flow_panel)

        panes = QHBoxLayout()
        panes.setContentsMargins(0, 0, 0, 0)
        panes.setSpacing(8)
        self._panes_layout = panes

        self._left_panel = self._panel()
        self._left_panel.setObjectName("editorPanel")
        self._left_panel.setProperty("role", "source")
        self._left_panel.setMaximumHeight(TEXT_PANEL_MAX_HEIGHT)
        left_layout = QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(10, 9, 10, 9)
        left_layout.setSpacing(6)
        self._left_panel_layout = left_layout
        self._src_text_widget = QPlainTextEdit()
        self._src_text_widget.setObjectName("textPane")
        self._src_text_widget.setPlaceholderText(self._src_placeholder)
        self._src_text_widget.setReadOnly(True)
        self._src_text_widget.setMinimumHeight(TEXT_PANE_MIN_HEIGHT)
        self._src_text_widget.setMaximumHeight(TEXT_PANEL_MAX_HEIGHT)
        left_layout.addWidget(self._src_text_widget, 1)
        panes.addWidget(self._left_panel, 1)

        self._right_panel = self._panel()
        self._right_panel.setObjectName("editorPanel")
        self._right_panel.setProperty("role", "target")
        self._right_panel.setMaximumHeight(TEXT_PANEL_MAX_HEIGHT)
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(10, 9, 10, 9)
        right_layout.setSpacing(6)
        self._right_panel_layout = right_layout
        self._tgt_text_widget = QPlainTextEdit()
        self._tgt_text_widget.setObjectName("textPane")
        self._tgt_text_widget.setReadOnly(True)
        self._tgt_text_widget.setMinimumHeight(TEXT_PANE_MIN_HEIGHT)
        self._tgt_text_widget.setMaximumHeight(TEXT_PANEL_MAX_HEIGHT)
        right_layout.addWidget(self._tgt_text_widget, 1)
        panes.addWidget(self._right_panel, 1)
        layout.addLayout(panes, 1)

        action_strip = QFrame()
        action_strip.setObjectName("actionStrip")
        action_strip.setMaximumHeight(ACTION_STRIP_MAX_HEIGHT)
        self._action_strip = action_strip
        action_layout = QVBoxLayout(action_strip)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(5)
        self._action_layout = action_layout

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        self._action_top_layout = top_row
        self._manual_input_btn = QPushButton(self._t("manual_input"))
        self._manual_input_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._manual_input_btn, min_width=86, height=ACTION_BUTTON_HEIGHT)
        self._manual_input_btn.clicked.connect(self._open_text_input_popup)
        top_row.addWidget(self._manual_input_btn)
        self._translate_btn = QPushButton(self._t("translate"))
        self._translate_btn.setObjectName("primaryButton")
        self._fit_button_to_text(self._translate_btn, min_width=82, height=ACTION_BUTTON_HEIGHT)
        self._translate_btn.clicked.connect(self._on_translate_clicked)
        top_row.addWidget(self._translate_btn)
        self._clear_btn = QPushButton(self._t("clear"))
        self._clear_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._clear_btn, min_width=64, height=ACTION_BUTTON_HEIGHT)
        self._clear_btn.clicked.connect(self._clear_input)
        top_row.addWidget(self._clear_btn)
        top_row.addStretch(1)
        self._send_to_vrc_btn = QPushButton(self._t("send_to_vrc"))
        self._send_to_vrc_btn.setObjectName("primaryButton")
        send_icon = ui_icon("send.svg", 15, "#ffffff")
        if not send_icon.isNull():
            self._send_to_vrc_btn.setIcon(send_icon)
            self._send_to_vrc_btn.setIconSize(QSize(15, 15))
        self._fit_button_to_text(self._send_to_vrc_btn, min_width=116, height=ACTION_BUTTON_HEIGHT, icon_gap=18)
        self._send_to_vrc_btn.clicked.connect(self._on_send_clicked)
        top_row.addWidget(self._send_to_vrc_btn)
        action_layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        self._action_bottom_layout = bottom_row
        bottom_row.addStretch(1)
        self._copy_source_btn = QPushButton(self._t("copy_source"))
        self._copy_source_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._copy_source_btn, min_width=94, height=ACTION_BUTTON_HEIGHT)
        self._copy_source_btn.clicked.connect(self._copy_source)
        bottom_row.addWidget(self._copy_source_btn)
        self._copy_result_btn = QPushButton(self._t("copy"))
        self._copy_result_btn.setObjectName("secondaryButton")
        self._fit_button_to_text(self._copy_result_btn, min_width=90, height=ACTION_BUTTON_HEIGHT)
        self._copy_result_btn.clicked.connect(self._copy_result)
        bottom_row.addWidget(self._copy_result_btn)
        action_layout.addLayout(bottom_row)
        layout.addWidget(action_strip)
        return card

    def _build_side_card(self) -> QFrame:
        tokens = _main_theme_palette(self._main_theme)
        card = QFrame()
        card.setObjectName("sidePanel")
        card.setMinimumWidth(max(240, int(tokens["SIDE_WIDTH"]) - 64))
        card.setMaximumWidth(max(260, int(tokens["SIDE_WIDTH"]) - 44))
        self._side_panel = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self._side_layout = layout

        title_row = QVBoxLayout()
        title_row.setSpacing(2)
        self._side_title_layout = title_row
        self._quick_controls_label = QLabel(self._copy("quick_controls"))
        self._quick_controls_label.setObjectName("controlSectionTitle")
        title_row.addWidget(self._quick_controls_label)
        self._quick_controls_hint = None
        layout.addLayout(title_row)

        self._start_btn = QPushButton(self._t("start_listening"))
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.setFixedHeight(SIDE_PRIMARY_BUTTON_HEIGHT)
        self._start_btn.clicked.connect(self._toggle_listening)
        layout.addWidget(self._start_btn)

        mode_box = QFrame()
        mode_box.setObjectName("modeBox")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(4)
        self._mode_layout = mode_layout
        self._mode_translation_button = QPushButton(self._copy("mode_translation"))
        self._mode_translation_button.setObjectName("modeButton")
        self._mode_translation_button.setCheckable(True)
        self._mode_translation_button.setProperty("modeActive", "false")
        self._mode_translation_button.clicked.connect(lambda: self._set_app_mode(AppMode.TRANSLATION, persist=True))
        self._mode_translation_button.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        mode_layout.addWidget(self._mode_translation_button, 1)
        self._mode_simultaneous_button = QPushButton(self._copy("mode_simultaneous"))
        self._mode_simultaneous_button.setObjectName("modeButton")
        self._mode_simultaneous_button.setCheckable(True)
        self._mode_simultaneous_button.setProperty("modeActive", "false")
        self._mode_simultaneous_button.clicked.connect(lambda: self._set_app_mode(AppMode.SIMULTANEOUS, persist=True))
        self._mode_simultaneous_button.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        mode_layout.addWidget(self._mode_simultaneous_button, 1)
        layout.addWidget(mode_box)

        mic_group = QWidget()
        mic_group.setObjectName("micPanel")
        mic_layout = QVBoxLayout(mic_group)
        mic_layout.setContentsMargins(0, 2, 0, 0)
        mic_layout.setSpacing(6)
        self._mic_layout = mic_layout
        self._microphone_label = QLabel(self._t("microphone"))
        self._microphone_label.setObjectName("controlLabel")
        mic_layout.addWidget(self._microphone_label)
        self._device_combo = NoWheelComboBox()
        self._device_combo.setObjectName("deviceCombo")
        self._device_combo.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._device_combo.setMinimumWidth(0)
        self._device_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._device_combo.currentTextChanged.connect(self._on_device_combo_changed)
        mic_layout.addWidget(self._device_combo)
        self._refresh_device_combo()
        self._device_dropdown_btn = None

        mic_actions = QHBoxLayout()
        mic_actions.setSpacing(6)
        self._mic_actions_layout = mic_actions
        self._mute_btn = QPushButton("")
        self._mute_btn.setObjectName("activeButton")
        self._mute_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._mute_btn.clicked.connect(self._toggle_mic_mute)
        mic_actions.addWidget(self._mute_btn, 1)
        self._desktop_btn = QPushButton("")
        self._desktop_btn.setObjectName("activeButton")
        self._desktop_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._desktop_btn.clicked.connect(self._toggle_listen)
        mic_actions.addWidget(self._desktop_btn, 1)
        mic_layout.addLayout(mic_actions)
        layout.addWidget(mic_group)

        assist_actions = QHBoxLayout()
        assist_actions.setSpacing(6)
        self._assist_layout = assist_actions
        self._listen_overlay_btn = QPushButton("")
        self._listen_overlay_btn.setObjectName("activeButton")
        self._listen_overlay_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._listen_overlay_btn.setMinimumWidth(0)
        self._listen_overlay_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._listen_overlay_btn.clicked.connect(self._toggle_listen_overlay)
        assist_actions.addWidget(self._listen_overlay_btn, 1)
        self._guide_btn_secondary = QPushButton(self._copy("guide_short"))
        self._guide_btn_secondary.setObjectName("secondaryButton")
        self._guide_btn_secondary.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._guide_btn_secondary.setMinimumWidth(0)
        self._guide_btn_secondary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._guide_btn_secondary.clicked.connect(self._open_osc_guide)
        assist_actions.addWidget(self._guide_btn_secondary, 1)
        layout.addLayout(assist_actions)

        layout.addStretch(1)
        return card

    def _refresh_static_texts(self) -> None:
        self.setWindowTitle(self._t("window_title"))
        self._src_placeholder = self._t("source_placeholder")
        if self._brand_title_label:
            self._brand_title_label.setText(self._t("window_title"))
        if self._creator_banner_label:
            self._creator_banner_label.setText(self._copy("creator_banner_compact"))
        if self._ui_lang_combo:
            label = self._ui_lang_reverse.get(self._ui_lang)
            if label:
                self._set_combo_text(self._ui_lang_combo, label)
        if self._settings_btn:
            self._settings_btn.setText(self._copy("settings_short"))
            self._settings_btn.setFixedSize(HEADER_ACTION_WIDTH, 34)
        self._refresh_tweaks_button()
        if self._guide_btn:
            self._guide_btn.setText(self._copy("guide_short"))
        if self._guide_btn_secondary:
            self._guide_btn_secondary.setText(self._copy("guide_short"))
            self._guide_btn_secondary.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
            self._guide_btn_secondary.style().unpolish(self._guide_btn_secondary)
            self._guide_btn_secondary.style().polish(self._guide_btn_secondary)
        if self._sponsors_btn:
            self._sponsors_btn.setText(self._copy("sponsors_btn"))
            self._sponsors_btn.setFixedSize(FOOTER_SPONSOR_BUTTON_WIDTH, FOOTER_BUTTON_SIZE)
            self._sponsors_btn.setIconSize(QSize(FOOTER_SPONSOR_ICON_SIZE, FOOTER_SPONSOR_ICON_SIZE))
            self._sponsors_btn.setIcon(ui_icon(ICON_SPONSOR_FILE, FOOTER_SPONSOR_ICON_SIZE, "#ffffff"))
        self._refresh_update_badge()
        if self._manual_input_btn:
            self._manual_input_btn.setText(self._t("manual_input"))
            self._fit_button_to_text(self._manual_input_btn, min_width=86, height=ACTION_BUTTON_HEIGHT)
        if self._translate_btn:
            self._translate_btn.setText(self._t("translating") if not self._translate_btn.isEnabled() else self._t("translate"))
            self._fit_button_to_text(self._translate_btn, min_width=82, height=ACTION_BUTTON_HEIGHT)
        if self._clear_btn:
            self._clear_btn.setText(self._t("clear"))
            self._fit_button_to_text(self._clear_btn, min_width=64, height=ACTION_BUTTON_HEIGHT)
        if self._copy_source_btn:
            self._copy_source_btn.setText(self._t("copy_source"))
            self._fit_button_to_text(self._copy_source_btn, min_width=94, height=ACTION_BUTTON_HEIGHT)
        if self._copy_result_btn:
            self._copy_result_btn.setText(self._t("copy"))
            self._fit_button_to_text(self._copy_result_btn, min_width=90, height=ACTION_BUTTON_HEIGHT)
        if self._send_to_vrc_btn:
            self._send_to_vrc_btn.setText(self._t("send_to_vrc"))
            self._fit_button_to_text(self._send_to_vrc_btn, min_width=116, height=ACTION_BUTTON_HEIGHT, icon_gap=18)
        if getattr(self, "_quick_controls_label", None):
            self._quick_controls_label.setText(self._copy("quick_controls"))
        if getattr(self, "_src_header_label", None):
            self._src_header_label.setText(self._copy("source_lang_short"))
        if getattr(self, "_tgt_header_label", None):
            self._tgt_header_label.setText(self._copy("translation_lang_1_short"))
        if getattr(self, "_tgt2_header_label", None):
            self._tgt2_header_label.setText(self._copy("translation_lang_2_short"))
        if getattr(self, "_microphone_label", None):
            self._microphone_label.setText(self._t("microphone"))
        self._refresh_device_combo()
        if getattr(self, "_assist_label", None):
            self._assist_label.setText(self._copy("guide_short"))
        if self._status_label is not None and getattr(self, "_status_key", None):
            self._set_status(self._copy(self._status_key), self._status_color, key=self._status_key)
        if self._bottom_bar is not None and getattr(self, "_bottom_key", None):
            self._set_bottom(self._copy(self._bottom_key), self._bottom_color, key=self._bottom_key)
        self._refresh_language_combos()
        self._set_source_text(self._src_text)
        self._refresh_start_button()
        self._refresh_mic_mute_button()
        self._refresh_mode_buttons()
        self._refresh_desktop_capture_button()
        self._refresh_listen_overlay_button()
        if self._listen_overlay_btn:
            self._listen_overlay_btn.setFixedHeight(SIDE_CONTROL_BUTTON_HEIGHT)
        self._refresh_theme_button()
        self._apply_adaptive_layout(force=True)

    def _refresh_language_combos(self) -> None:
        if getattr(self, "_refreshing_language_combos", False):
            return
        self._refreshing_language_combos = True
        try:
            self._all_target_lang_options = list(get_target_language_options(ui_language=self._ui_lang))
            self._target_lang_codes = {label: code for label, code in self._all_target_lang_options}
            target_reverse = {code: label for label, code in self._all_target_lang_options}
            trans_cfg = self._config.setdefault("translation", {})
            tgt_code = str(trans_cfg.get("target_language", self._current_tgt_lang) or "ja")
            tgt2_code = str(trans_cfg.get("target_language_2", self._current_tgt_lang_2) or "en")
            self._current_tgt_lang = tgt_code
            self._current_tgt_lang_2 = tgt2_code

            self._all_manual_lang_options = list(get_manual_source_language_options({tgt_code}, ui_language=self._ui_lang))
            self._src_lang_codes = {label: code for label, code in self._all_manual_lang_options}
            src_reverse = {code: label for label, code in self._all_manual_lang_options}
            src_code = str(trans_cfg.get("source_language", "auto") or "auto")
            self._current_src_lang = None if src_code == "auto" else src_code
            self._current_asr_lang = src_code

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
            if self._tgt_lang2_combo:
                blocked = self._tgt_lang2_combo.blockSignals(True)
                self._tgt_lang2_combo.clear()
                self._tgt_lang2_combo.addItems([label for label, _ in self._all_target_lang_options])
                self._tgt_lang2_combo.setCurrentText(target_reverse.get(tgt2_code, self._tgt_lang2_combo.itemText(0)))
                self._tgt_lang2_combo.blockSignals(blocked)
        finally:
            self._refreshing_language_combos = False

    def _on_tgt_lang2_change(self, selected_label: str | None = None) -> None:
        if selected_label is None and self._tgt_lang2_combo:
            selected_label = self._tgt_lang2_combo.currentText()
        code = self._target_lang_codes.get(str(selected_label or ""), self._current_tgt_lang_2 or "en")
        self._current_tgt_lang_2 = code
        translation = self._config.setdefault("translation", {})
        translation["target_language_2"] = code
        translation["language_pair_source"] = "manual"
        if not getattr(self, "_refreshing_language_combos", False):
            self._refresh_realtime_config_snapshot()
            self._schedule_config_save()

    def _set_source_text(self, text: str, text_color: str | None = None) -> None:
        safe = (text or "").strip()
        if len(safe) > CHATBOX_CHAR_LIMIT:
            safe = safe[:CHATBOX_CHAR_LIMIT]
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

    def _refresh_tweaks_button(self) -> None:
        if not self._tweaks_btn:
            return
        self._tweaks_btn.setText(self._t("quick_switch_button"))
        self._tweaks_btn.setToolTip(self._t("quick_switch_tooltip"))
        self._tweaks_btn.setFixedSize(HEADER_ACTION_WIDTH, 34)
        icon = ui_icon("activity.svg", 18, icon_tint(self._main_theme, strong=True))
        self._tweaks_btn.setIcon(icon)
        self._tweaks_btn.setIconSize(QSize(18, 18))
        self._tweaks_btn.style().unpolish(self._tweaks_btn)
        self._tweaks_btn.style().polish(self._tweaks_btn)

    def _toggle_tweaks_panel(self) -> None:
        if self._tweaks_panel is None:
            self._tweaks_panel = RealtimeTweaksPanel(
                parent=self,
                config=self._config,
                ui_language=self._ui_lang,
                theme=self._main_theme,
                on_change=self._on_quick_switch_changed,
            )
            self._tweaks_panel.finished.connect(self._on_tweaks_panel_closed)
        if self._tweaks_panel.isVisible():
            self._tweaks_panel.hide()
            return
        self._tweaks_panel.show()
        self._tweaks_panel.raise_()
        self._tweaks_panel.activateWindow()

    def _clear_cached_translator(self) -> None:
        cached_translator = getattr(self, "_translator", None)
        self._translator = None
        controller = getattr(self, "_manual_translation_controller", None)
        controller_translator = None
        controller_released = False
        if controller is not None and hasattr(controller, "translator"):
            try:
                controller_translator = controller.translator
                controller.translator = None
                controller_released = True
            except Exception as exc:
                logger.debug(
                    "Failed to release manual translator client: %s",
                    safe_exception_summary(exc),
                )
        if (
            cached_translator is not None
            and (
                cached_translator is not controller_translator
                or not controller_released
            )
        ):
            close = getattr(cached_translator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug(
                        "Failed to close cached translator client: %s",
                        safe_exception_summary(exc),
                    )

    def _set_quick_translation_provider(self, provider: object) -> None:
        backend = normalize_backend(str(provider or ""))
        trans_cfg = self._config.setdefault("translation", {})
        trans_cfg["backend"] = backend
        trans_cfg["backend_source"] = "manual"
        backend_cfg = trans_cfg.setdefault(backend, {})
        if not isinstance(backend_cfg, dict):
            backend_cfg = {}
            trans_cfg[backend] = backend_cfg
        for key in ("base_url", "model", "timeout_s", "max_retries"):
            default = get_backend_value(backend, key)
            if default != "" and not backend_cfg.get(key):
                backend_cfg[key] = default
        self._clear_cached_translator()
        self._reset_translation_failure_backoff()

    def _set_quick_translation_model(self, model: object) -> None:
        trans_cfg = self._config.setdefault("translation", {})
        backend = normalize_backend(str(trans_cfg.get("backend", "")))
        trans_cfg["backend"] = backend
        trans_cfg["backend_source"] = "manual"
        backend_cfg = trans_cfg.setdefault(backend, {})
        if not isinstance(backend_cfg, dict):
            backend_cfg = {}
            trans_cfg[backend] = backend_cfg
        backend_cfg["model"] = str(model or "").strip()
        self._clear_cached_translator()
        self._reset_translation_failure_backoff()

    def _set_quick_output_format(self, value: object) -> None:
        self._config.setdefault("translation", {})["output_format"] = normalize_output_format(str(value or ""))

    def _set_quick_original_only_read_translation_wait_for_tts(
        self,
        value: object,
    ) -> None:
        self._config.setdefault("translation", {})[
            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY
        ] = bool(value)

    def _set_quick_asr_rewrite_style(self, value: object) -> None:
        self._config.setdefault("translation", {})["asr_rewrite_style"] = (
            normalize_asr_rewrite_style(value)
        )

    def _set_quick_tts_language(self, value: object) -> None:
        tts_cfg = self._config.setdefault("tts", {})
        engine = str(tts_cfg.get("engine", "edge") or "edge").strip() or "edge"
        engine_cfg = tts_cfg.setdefault(engine, {})
        if not isinstance(engine_cfg, dict):
            engine_cfg = {}
            tts_cfg[engine] = engine_cfg
        if engine == "style_bert_vits2":
            style_cfg = tts_cfg.setdefault("style_bert_vits2", {})
            if isinstance(style_cfg, dict):
                style_cfg["bert_language"] = str(value or "").strip()
        else:
            engine_cfg["language"] = str(value or "").strip()
        self._reset_tts_manager_if_runtime_changed()

    def _set_quick_tts_voice(self, value: object) -> None:
        tts_cfg = self._config.setdefault("tts", {})
        engine = str(tts_cfg.get("engine", "edge") or "edge").strip() or "edge"
        engine_cfg = tts_cfg.setdefault(engine, {})
        if not isinstance(engine_cfg, dict):
            engine_cfg = {}
            tts_cfg[engine] = engine_cfg
        engine_cfg["voice"] = str(value or "").strip()

    def _set_quick_rewrite_typed_text(self, value: object) -> None:
        self._config.setdefault("translation", {})["rewrite_typed_text"] = bool(
            value
        )

    def _set_quick_noise_reduction(self, value: object) -> None:
        try:
            strength = max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            strength = 0.0
        self._config.setdefault("audio", {})["denoise_strength"] = strength
        recorder = getattr(self, "_recorder", None)
        setter = getattr(recorder, "set_denoise_strength", None)
        if callable(setter):
            setter(strength)

    def _on_quick_switch_changed(self, key: str, value: object) -> None:
        handlers = {
            "translation_provider": self._set_quick_translation_provider,
            "translation_model": self._set_quick_translation_model,
            "output_format": self._set_quick_output_format,
            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY: (
                self._set_quick_original_only_read_translation_wait_for_tts
            ),
            "asr_rewrite_style": self._set_quick_asr_rewrite_style,
            "rewrite_typed_text": self._set_quick_rewrite_typed_text,
            "tts_language": self._set_quick_tts_language,
            "tts_voice": self._set_quick_tts_voice,
            "noise_reduction": self._set_quick_noise_reduction,
        }
        handler = handlers.get(str(key))
        if handler is None:
            return
        handler(value)
        if key in {
            "translation_provider",
            "translation_model",
            "output_format",
            ORIGINAL_ONLY_READ_TRANSLATION_WAIT_FOR_TTS_KEY,
            "asr_rewrite_style",
        }:
            self._refresh_realtime_config_snapshot()
        if key in {"translation_provider", "translation_model"}:
            if (
                self._translation_background_warmup_enabled()
                and first_missing_required_credential(
                    self._config,
                    scopes=("translation",),
                    ui_language=getattr(self, "_ui_lang", None),
                    active_only=True,
                )
                is None
            ):
                try:
                    self._ensure_manual_translation_controller().prewarm_async()
                    self._start_translation_background_prewarm()
                except Exception:
                    logger.exception(
                        "Could not restart selected translation provider prewarm"
                    )
        self._schedule_config_save()
        self._set_bottom(self._t("quick_switch_updated"))

    def _refresh_realtime_config_snapshot(self) -> None:
        """Atomically publish configuration for future admitted sentences.

        Existing payloads retain their immutable old snapshot, so a provider,
        model, output-format, or rewrite-style change never mutates work already in
        flight. Translation workers rebuild their private client state when the
        first task carrying the new snapshot reaches them.
        """

        if not getattr(self, "_running", False):
            return
        try:
            self._realtime_config_snapshot = _freeze_snapshot_value(
                copy.deepcopy(self._config)
            )
        except Exception:
            logger.exception("Failed to publish realtime configuration snapshot")

    def _tts_runtime_signature(self) -> tuple:
        tts_cfg = self._tts_config()
        engine = self._current_tts_engine()
        engine_cfg = self._current_tts_engine_config()
        runtime_engine_cfg = {
            str(key): value
            for key, value in engine_cfg.items()
            if str(key) not in _TTS_REQUEST_SCOPED_CONFIG_KEYS
        }
        try:
            serialized_engine_cfg = json.dumps(
                runtime_engine_cfg,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=repr,
            ).encode("utf-8")
        except Exception:
            serialized_engine_cfg = repr(runtime_engine_cfg).encode(
                "utf-8",
                errors="backslashreplace",
            )
        engine_config_digest = hashlib.sha256(serialized_engine_cfg).hexdigest()
        perf_cfg = self._performance_config()

        def performance_int(name: str, default: int) -> int:
            try:
                return int(perf_cfg.get(name, default))
            except (TypeError, ValueError):
                return default

        return (
            engine,
            engine_config_digest,
            bool(tts_cfg.get("allow_fallback", True)),
            str(tts_cfg.get("output_device", "")),
            str(tts_cfg.get("output_device_name", "")),
            bool(tts_cfg.get("output_to_vrchat", False)),
            bool(tts_cfg.get("monitor_enabled", False)),
            performance_int("tts_cache_max_mb", 24),
            performance_int("tts_cache_max_items", 60),
        )

    def _ensure_tts_manager(self):
        if self._prompt_for_missing_credential(("tts",)):
            return None
        signature = self._tts_runtime_signature()
        manager_lock = self._tts_manager_lifecycle_lock()
        with manager_lock:
            existing = getattr(self, "_tts_manager", None)
            existing_signature = getattr(self, "_tts_manager_signature", None)
        if existing is not None:
            if existing_signature == signature:
                return existing
            self._reset_tts_manager()
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
            close = getattr(manager, "close", None)
            if callable(close):
                close()
            else:
                stop = getattr(manager, "stop", None)
                if callable(stop):
                    stop()
            return None
        manager.start()
        retained = manager
        with manager_lock:
            current = getattr(self, "_tts_manager", None)
            current_signature = getattr(self, "_tts_manager_signature", None)
            if current is not None and current_signature == signature:
                retained = current
            elif current is None and self._tts_runtime_signature() == signature:
                self._tts_manager = manager
                self._tts_manager_signature = signature
                manager = None
        if manager is not None:
            self._close_tts_manager_instance(manager)
        return retained if retained is not manager else None

    def _reset_tts_manager(
        self,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        self._cancel_tts_background_prewarm()
        manager_lock = self._tts_manager_lifecycle_lock()
        with manager_lock:
            manager = getattr(self, "_tts_manager", None)
            self._tts_manager = None
            self._tts_manager_signature = None
        if manager is None:
            return True
        try:
            close = getattr(manager, "close", None)
            if callable(close):
                if timeout_seconds is None:
                    state = close()
                else:
                    try:
                        state = close(timeout_seconds=timeout_seconds)
                    except TypeError:
                        state = close()
            else:
                stop = getattr(manager, "stop", None)
                if callable(stop):
                    if timeout_seconds is None:
                        state = stop()
                    else:
                        try:
                            state = stop(timeout_seconds=timeout_seconds)
                        except TypeError:
                            state = stop()
                else:
                    state = None
            complete = (
                True
                if state is None
                else bool(getattr(state, "quiescent", state))
            )
            if bool(getattr(state, "engine_close_deferred", False)):
                complete = False
            if not complete:
                self._remember_deferred_tts_manager(manager)
            return complete
        except Exception as exc:
            self._remember_deferred_tts_manager(manager)
            logger.debug(
                "Failed to stop TTS manager: %s",
                safe_exception_summary(exc),
            )
            return False

    def _remember_deferred_tts_manager(self, manager: object) -> None:
        lock = self.__dict__.setdefault("_deferred_cleanup_lock", threading.RLock())
        with lock:
            deferred = self.__dict__.setdefault("_deferred_tts_managers", [])
            if all(existing is not manager for existing in deferred):
                deferred.append(manager)

    def _reset_tts_manager_if_runtime_changed(self) -> None:
        manager_lock = self._tts_manager_lifecycle_lock()
        with manager_lock:
            manager = getattr(self, "_tts_manager", None)
            signature = getattr(self, "_tts_manager_signature", None)
        if manager is not None and signature != self._tts_runtime_signature():
            self._reset_tts_manager()

    def _settings_preload_enabled(self) -> bool:
        perf_cfg = self._performance_config()
        if not bool(perf_cfg.get("preload_settings_window", False)):
            return False
        if self._performance_profile() == "low_power":
            return False
        return self._current_tts_engine() not in {"style_bert_vits2", "xtts"}

    def _on_language_changed(self, language_code: str) -> None:
        self._apply_ui_language(language_code)

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
