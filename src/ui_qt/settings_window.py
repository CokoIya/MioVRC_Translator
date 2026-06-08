from __future__ import annotations

import copy
import logging
import queue
import shutil
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, QTimer, QUrl, Qt, Signal, QVariantAnimation
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QLinearGradient, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.asr.hf_model_downloader import DownloadState, get_downloader, model_is_complete
from src.asr.model_registry import (
    ASR_ENGINE_FOLLOW_MAIN,
    LISTEN_SELECTABLE_ASR_ENGINES,
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_DEFAULT_REGION,
    QWEN3_ASR_MODEL_CHOICES,
    USER_SELECTABLE_ASR_ENGINES,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)
from src.asr.text_corrections import (
    dictionary_status,
    update_official_dictionary,
    upsert_user_dictionary_entry,
)
from src.audio.desktop_recorder import list_output_devices as _list_desktop_output_devices
from src.audio.recorder import AudioRecorder
from src.tts.api_tts_config import (
    TTS_API_ENGINE_IDS,
    get_tts_api_base_url,
    get_tts_api_default_value,
    get_tts_api_region_options,
    get_tts_api_voice_options,
    normalize_tts_api_region,
    resolve_tts_api_config,
)
from src.tts.factory import create_tts_engine
from src.tts.manager import TTSManager, find_best_virtual_output_device, resolve_output_device
from src.tts.style_bert_vits2_engine import (
    STYLE_BERT_LANGUAGE_NAMES,
    list_style_bert_vits2_voices,
    normalize_style_bert_bert_language,
    style_bert_bert_model_id,
)
from src.tts.style_bert_vits2_models import (
    StyleBertVits2ModelError,
    import_style_bert_model_path,
    list_imported_style_bert_models,
)
from src.ui_qt.icon_utils import ui_icon
from src.ui_qt.pytorch_cuda_install_dialog import PytorchCudaInstallDialog
from src.ui_qt.styles import build_settings_window_styles
from src.ui_qt.theme import MAIN_THEME_CONFIG_KEY, icon_tint, normalize_theme_preference, resolve_theme, theme_tokens
from src.ui_qt.window_utils import apply_window_chrome_theme, play_theme_fade
from src.ui_qt.widgets import NoWheelComboBox
from src.updater.update_checker import UpdateInfo, check_for_update
from src.utils import config_manager
from src.utils.app_paths import backgrounds_dir
from src.utils.logger import logs_dir
from src.utils.global_hotkey import normalize_hotkey, HotkeyError
from src.utils.gpu_support import cuda_pytorch_installed, detect_nvidia_driver, gpu_runtime_available
from src.utils.i18n import tr
from src.utils.ui_config import (
    get_backend_order,
    DEFAULT_ASR_ENGINE,
    OUTPUT_FORMAT_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    backend_base_url_is_editable,
    backend_has_service_regions,
    backend_model_is_selectable,
    backend_region_for_ui_language,
    backend_region_from_base_url,
    get_backend_api_key_hint,
    get_backend_config_value,
    get_backend_label,
    get_backend_region_base_url,
    get_backend_region_options,
    get_backend_model_hint,
    get_backend_model_options,
    get_backend_model_profile,
    get_backend_spec,
    get_backend_value,
    get_output_format_options,
    normalize_output_format,
    get_manual_source_language_options,
    get_target_language_options,
    get_ui_language,
    normalize_backend,
    normalize_backend_region,
)
from src.utils.translation_config_validation import missing_required_translation_api_key
from src.version import APP_VERSION

logger = logging.getLogger(__name__)

SETTINGS_WINDOW_WIDTH = 1180
SETTINGS_WINDOW_HEIGHT = 740
SETTINGS_NAV_WIDTH = 250
SETTINGS_UPDATE_BUTTON_MIN_WIDTH = 136
SETTINGS_UPDATE_BUTTON_PADDING = 46
SETTINGS_HINT_WRAP = 640
TTS_TEST_TIMEOUT_MS = 60_000
STYLE_BERT_TTS_TEST_TIMEOUT_MS = 240_000
TTS_TEST_TEXT_BY_LANGUAGE = {
    "jp": "こんにちは、これは読み上げテストです。",
    "ja": "こんにちは、これは読み上げテストです。",
    "en": "Hello, this is a speech test.",
    "zh": "你好，这是中文朗读测试。",
}

THEME_LABELS = {
    "zh-CN": {"system": "跟随系统", "dark": "深色", "light": "浅色"},
    "en": {"system": "Follow System", "dark": "Dark", "light": "Light"},
    "ja": {"system": "システムに追従", "dark": "ダーク", "light": "ライト"},
    "ko": {"system": "시스템 따르기", "dark": "다크", "light": "라이트"},
    "ru": {"system": "Следовать системе", "dark": "Тёмная", "light": "Светлая"},
}

DENOISE_PRESETS = (
    ("off", 0.0),
    ("light", 0.35),
    ("medium", 0.6),
    ("strong", 0.85),
)

DENOISE_LABELS = {
    "zh-CN": {
        "title": "降噪强度",
        "hint": "降低背景噪声会更稳定，但过强可能让语音变闷。",
        "off": "关闭",
        "light": "轻微",
        "medium": "中等",
        "strong": "强力",
    },
    "en": {
        "title": "Noise reduction",
        "hint": "Noise reduction improves stability, but strong values can dull speech.",
        "off": "Off",
        "light": "Light",
        "medium": "Medium",
        "strong": "Strong",
    },
    "ja": {
        "title": "ノイズ低減",
        "hint": "背景ノイズを抑えると安定しますが、強すぎると声がこもります。",
        "off": "オフ",
        "light": "弱",
        "medium": "中",
        "strong": "強",
    },
    "ru": {
        "title": "Шумоподавление",
        "hint": "Шумоподавление повышает стабильность, но слишком сильное может приглушить голос.",
        "off": "Выкл.",
        "light": "Лёгкое",
        "medium": "Среднее",
        "strong": "Сильное",
    },
    "ko": {
        "title": "노이즈 감소",
        "hint": "배경 소음을 줄이면 더 안정적이지만 너무 강하면 음성이 답답해질 수 있습니다.",
        "off": "끄기",
        "light": "약하게",
        "medium": "보통",
        "strong": "강하게",
    },
}

TTS_ENGINE_IDS = (
    "edge",
    "gtts",
    "pyttsx3",
    "voicevox",
    "aivis_speech",
    "mimo_tts",
    "qwen_tts",
    "style_bert_vits2",
)
DEFAULT_TTS_ENGINE = "edge"
MIXLINE_DOWNLOAD_URL = "https://www.logitechg.com/en-us/software/mixline.html"
VOICEVOX_DOWNLOAD_URL = "https://voicevox.hiroshiba.jp/"
AIVIS_SPEECH_DOWNLOAD_URL = "https://aivis-project.com/AivisSpeech"
NVIDIA_DRIVER_DOWNLOAD_URL = "https://www.nvidia.com/Download/index.aspx"


def _api_tts_voice_ids(engine: str) -> tuple[str, ...]:
    return tuple(voice_id for voice_id, *_rest in get_tts_api_voice_options(engine))


TTS_DEFAULT_VOICES = {
    "edge": (
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoyiNeural",
        "zh-CN-YunyangNeural",
        "ja-JP-NanamiNeural",
        "ja-JP-AoiNeural",
        "ja-JP-MayuNeural",
        "en-US-JennyNeural",
        "en-US-AriaNeural",
        "en-US-MichelleNeural",
    ),
    "gtts": ("zh-CN", "en", "ja", "ko", "ru"),
    "pyttsx3": (),
    "voicevox": (),
    "aivis_speech": (),
    "mimo_tts": ("mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"),
    "qwen_tts": _api_tts_voice_ids("qwen_tts"),
    "style_bert_vits2": (),
}

TTS_ENGINE_LABELS = {
    "edge": "Edge TTS（推荐）",
    "gtts": "Google TTS（需要外网）",
    "pyttsx3": "pyttsx3（离线）",
    "voicevox": "VOICEVOX（本地）",
    "aivis_speech": "AivisSpeech（本地）",
    "mimo_tts": "MiMo TTS（API）",
    "qwen_tts": "Qwen TTS（API）",
    "style_bert_vits2": "自定义音色",
}

TTS_ENGINE_I18N_KEYS = {
    "edge": "tts_engine_edge",
    "gtts": "tts_engine_gtts",
    "pyttsx3": "tts_engine_pyttsx3",
    "voicevox": "tts_engine_voicevox",
    "aivis_speech": "tts_engine_aivis_speech",
    "mimo_tts": "tts_engine_mimo_tts",
    "qwen_tts": "tts_engine_qwen_tts",
    "style_bert_vits2": "tts_engine_style_bert_vits2",
}


def _tts_engine_label(engine: str, ui_language: str | None) -> str:
    key = TTS_ENGINE_I18N_KEYS.get(engine)
    return tr(ui_language, key) if key else TTS_ENGINE_LABELS.get(engine, engine)

TTS_PLACEHOLDER_VOICE = "(\u65e0\u53ef\u7528\u58f0\u97f3)"

ROLEPLAY_PRESETS: dict[str, dict[str, str]] = {
    "custom": {
        "labels": {"zh-CN": "自定义", "en": "Custom", "ja": "カスタム", "ru": "Свое", "ko": "사용자 정의"},
        "persona_name": "",
        "persona_prompt": "",
        "persona_glossary": "",
        "politeness": "neutral",
        "tone": "natural",
    },
    "frieren": {
        "labels": {"zh-CN": "芙莉莲 / フリーレン / Frieren", "en": "芙莉莲 / フリーレン / Frieren", "ja": "芙莉莲 / フリーレン / Frieren", "ru": "芙莉莲 / フリーレン / Frieren", "ko": "芙莉莲 / フリーレン / Frieren"},
        "persona_name": "芙莉莲 / フリーレン / Frieren",
        "persona_prompt": "Use a calm, understated, slightly aloof style inspired by Frieren. Preserve meaning, keep phrasing concise, and avoid exaggerated emotion.",
        "persona_glossary": "Calm and restrained\nShort, plain wording\nSubtle dry humor only when it fits\nDo not overact",
        "politeness": "polite",
        "tone": "cool",
    },
    "violet_evergarden": {
        "labels": {"zh-CN": "薇尔莉特 / ヴァイオレット / Violet", "en": "薇尔莉特 / ヴァイオレット / Violet", "ja": "薇尔莉特 / ヴァイオレット / Violet", "ru": "薇尔莉特 / ヴァイオレット / Violet", "ko": "薇尔莉特 / ヴァイオレット / Violet"},
        "persona_name": "薇尔莉特 / ヴァイオレット / Violet",
        "persona_prompt": "Use a formal, graceful, sincere style inspired by Violet Evergarden. Preserve meaning with precise, elegant wording and restrained emotion.",
        "persona_glossary": "Polite and composed\nElegant, letter-like clarity\nSincere but not dramatic\nAvoid slang unless required",
        "politeness": "very_polite",
        "tone": "clear",
    },
    "artoria_pendragon": {
        "labels": {"zh-CN": "阿尔托莉雅 / アルトリア / Artoria", "en": "阿尔托莉雅 / アルトリア / Artoria", "ja": "阿尔托莉雅 / アルトリア / Artoria", "ru": "阿尔托莉雅 / アルトリア / Artoria", "ko": "阿尔托莉雅 / アルトリア / Artoria"},
        "persona_name": "阿尔托莉雅 / アルトリア / Artoria",
        "persona_prompt": "Use a dignified, knightly, principled style inspired by Artoria Pendragon. Keep the translation loyal to the source, firm, and respectful.",
        "persona_glossary": "Dignified and direct\nRespectful, principled wording\nA little noble, never pompous\nKeep intent unchanged",
        "politeness": "very_polite",
        "tone": "clear",
    },
    "marin_kitagawa": {
        "labels": {"zh-CN": "喜多川海梦 / 喜多川海夢 / Marin", "en": "喜多川海梦 / 喜多川海夢 / Marin", "ja": "喜多川海梦 / 喜多川海夢 / Marin", "ru": "喜多川海梦 / 喜多川海夢 / Marin", "ko": "喜多川海梦 / 喜多川海夢 / Marin"},
        "persona_name": "喜多川海梦 / 喜多川海夢 / Marin",
        "persona_prompt": "Use a bright, friendly, energetic style inspired by Marin Kitagawa. Keep meaning natural and casual, with expressive warmth but no random additions.",
        "persona_glossary": "Cheerful and casual\nFriendly otaku energy\nNatural spoken phrasing\nDo not make neutral text childish",
        "politeness": "casual",
        "tone": "cheerful",
    },
    "maomao": {
        "labels": {"zh-CN": "猫猫 / マオマオ / Maomao", "en": "猫猫 / マオマオ / Maomao", "ja": "猫猫 / マオマオ / Maomao", "ru": "猫猫 / マオマオ / Maomao", "ko": "猫猫 / マオマオ / Maomao"},
        "persona_name": "猫猫 / マオマオ / Maomao",
        "persona_prompt": "Use a sharp, observant, rational style inspired by Maomao. Preserve meaning with cool analysis, subtle dry sarcasm, and minimal sweetness.",
        "persona_glossary": "Observant and rational\nDry, subtle sarcasm when suitable\nAvoid overexcitement\nNo unnecessary flattery",
        "politeness": "neutral",
        "tone": "cool",
    },
    "kurisu_makise": {
        "labels": {"zh-CN": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu", "en": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu", "ja": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu", "ru": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu", "ko": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu"},
        "persona_name": "牧濑红莉栖 / 牧瀬紅莉栖 / Kurisu",
        "persona_prompt": "Use an intelligent, quick-witted, slightly sharp style inspired by Kurisu Makise. Preserve meaning with precise wording and mild tsundere-like pushback only when appropriate.",
        "persona_glossary": "Smart and precise\nQuick, lightly sharp reactions\nMild teasing only when the source supports it\nNo hostile rewrites",
        "politeness": "neutral",
        "tone": "playful",
    },
    "rem_rezero": {
        "labels": {"zh-CN": "雷姆 / レム / Rem", "en": "雷姆 / レム / Rem", "ja": "雷姆 / レム / Rem", "ru": "雷姆 / レム / Rem", "ko": "雷姆 / レム / Rem"},
        "persona_name": "雷姆 / レム / Rem",
        "persona_prompt": "Use a gentle, loyal, supportive style inspired by Rem. Preserve meaning with warm, careful wording and avoid making the line overly submissive.",
        "persona_glossary": "Gentle and supportive\nWarm but not melodramatic\nRespectful, careful phrasing\nDo not add devotion unless implied",
        "politeness": "polite",
        "tone": "warm",
    },
    "holo": {
        "labels": {"zh-CN": "赫萝 / ホロ / Holo", "en": "赫萝 / ホロ / Holo", "ja": "赫萝 / ホロ / Holo", "ru": "赫萝 / ホロ / Holo", "ko": "赫萝 / ホロ / Holo"},
        "persona_name": "赫萝 / ホロ / Holo",
        "persona_prompt": "Use a wise, playful, mature style inspired by Holo. Preserve meaning with elegant confidence, light teasing, and a slightly old-fashioned flavor only when natural.",
        "persona_glossary": "Wise and playful\nMature confidence\nLight teasing is okay\nUse old-fashioned flavor sparingly",
        "politeness": "polite",
        "tone": "playful",
    },
    "yor_forger": {
        "labels": {"zh-CN": "约尔 / ヨル / Yor", "en": "约尔 / ヨル / Yor", "ja": "约尔 / ヨル / Yor", "ru": "约尔 / ヨル / Yor", "ko": "约尔 / ヨル / Yor"},
        "persona_name": "约尔 / ヨル / Yor",
        "persona_prompt": "Use a gentle, earnest, polite style inspired by Yor Forger. Preserve meaning with soft sincerity, slight awkwardness when suitable, and no extra violence or dark jokes.",
        "persona_glossary": "Gentle and earnest\nPolite, sincere wording\nSlight awkward charm when it fits\nDo not add violent flavor",
        "politeness": "polite",
        "tone": "warm",
    },
    "mikasa_ackerman": {
        "labels": {"zh-CN": "三笠 / ミカサ / Mikasa", "en": "三笠 / ミカサ / Mikasa", "ja": "三笠 / ミカサ / Mikasa", "ru": "三笠 / ミカサ / Mikasa", "ko": "三笠 / ミカサ / Mikasa"},
        "persona_name": "三笠 / ミカサ / Mikasa",
        "persona_prompt": "Use a calm, direct, protective style inspired by Mikasa Ackerman. Preserve meaning with short, firm wording and minimal emotional decoration.",
        "persona_glossary": "Short and direct\nCalm, protective feeling\nFirm but not rude\nAvoid unnecessary emotion",
        "politeness": "neutral",
        "tone": "cool",
    },
}


def _roleplay_preset_label(preset_id: str, ui_language: str) -> str:
    preset = ROLEPLAY_PRESETS.get(preset_id, ROLEPLAY_PRESETS["custom"])
    labels = preset.get("labels", {})
    if isinstance(labels, dict):
        if ui_language in labels:
            return str(labels[ui_language])
        base_lang = ui_language.split("-", 1)[0]
        for lang, label in labels.items():
            if lang.split("-", 1)[0] == base_lang:
                return str(label)
        if "en" in labels:
            return str(labels["en"])
        for label in labels.values():
            return str(label)
    return str(preset_id)


def _roleplay_preset_options(ui_language: str) -> list[tuple[str, str]]:
    return [
        (_roleplay_preset_label(preset_id, ui_language), preset_id)
        for preset_id in ROLEPLAY_PRESETS
    ]


QT_SETTINGS_COPY = {
    "header_title": {"zh-CN": "设置", "en": "Settings", "ja": "設定"},
    "header_subtitle": {
        "zh-CN": "按你想做的事来调整 Mio，不需要懂专业术语。",
        "en": "Tune translation, speech, VRC listen, TTS, and roleplay settings.",
        "ja": "翻訳、音声、VRC リスン、TTS、ロールプレイ設定を調整します。",
    },
    "appearance_section": {"zh-CN": "基础外观设置", "en": "Appearance / Quick Setup", "ja": "外観 / クイック設定"},
    "recognition_section": {"zh-CN": "麦克风设置", "en": "Recognition", "ja": "認識"},
    "translation_domain_section": {"zh-CN": "总翻译设置", "en": "Translation", "ja": "翻訳"},
    "vr_integration_section": {"zh-CN": "VRChat 联动设置", "en": "VR Integration", "ja": "VR 連携"},
    "hotkey_voice_section": {"zh-CN": "快捷键设置", "en": "Hotkeys / Voice Control", "ja": "ホットキー / 音声制御"},
    "updates_models_section": {"zh-CN": "下载和更新", "en": "Updates / Models", "ja": "更新 / モデル"},
    "advanced_section": {"zh-CN": "高级", "en": "Advanced", "ja": "詳細"},
    "mode_wizard_section": {"zh-CN": "不知道怎么选？先点这里", "en": "First-run / Mode Guide", "ja": "初回起動 / モードガイド"},
    "mode_wizard_hint": {
        "zh-CN": "按“我说话发聊天框”“听别人说话”“让 Mio 朗读”等用途，自动打开常用开关。",
        "en": "Reopen the mode guide to apply recommended toggles for Chatbox, listening, TTS, manual input, or VR captions.",
        "ja": "モードガイドを再表示し、Chatbox、リスン、TTS、手動入力、VR 字幕向けの推奨スイッチを適用します。",
    },
    "open_mode_wizard": {"zh-CN": "帮我快速选择", "en": "Open Mode Guide", "ja": "モードガイドを開く"},
    "voice_control_placeholder": {
        "zh-CN": "这里先放快捷键设置。以后会加入“说一句话就控制 Mio”的功能。",
        "en": "Voice control is not implemented yet. This page currently manages global hotkeys; wake words and command actions will be added here later.",
        "ja": "音声制御は未実装です。現在はグローバルホットキーを管理し、後でウェイクワードとコマンドを追加します。",
    },
    "advanced_runtime_hint": {
        "zh-CN": "改麦克风、语言或发送样式后，点保存即可。Mio 会自己重新加载，正在录音时也会尽量恢复。",
        "en": "After device, language, or output-format changes, saving reloads the related listeners; active recording is stopped and restored.",
        "ja": "デバイス、言語、出力形式を変更して保存すると関連するリスナーを再読み込みします。録音中は一度停止して復帰します。",
    },
    "translation_section": {"zh-CN": "翻译内容怎么发", "en": "General / Translation", "ja": "一般 / 翻訳"},
    "voice_section": {"zh-CN": "麦克风和听写", "en": "Voice Settings", "ja": "音声設定"},
    "vrc_listen_section": {"zh-CN": "逆向翻译设置", "en": "VRC Listen", "ja": "逆翻訳"},
    "tts_section": {"zh-CN": "同声传译设置", "en": "TTS / Interpretation", "ja": "TTS / 同時通訳"},
    "roleplay_section": {"zh-CN": "翻译风格设置", "en": "Roleplay / Social", "ja": "ロールプレイ / ソーシャル"},
    "settings_check_update": {"zh-CN": "检查更新", "en": "Check Update", "ja": "更新確認"},
    "settings_update_checking": {
        "zh-CN": "检查中...",
        "en": "Checking...",
        "ja": "確認中...",
        "ru": "Проверка...",
        "ko": "확인 중...",
    },
    "settings_up_to_date": {"zh-CN": "当前已是最新版本。", "en": "You are up to date.", "ja": "最新バージョンです。"},
    "settings_check_update_failed": {"zh-CN": "检查更新失败", "en": "Update check failed", "ja": "更新確認に失敗しました"},
    "settings_check_update_failed_detail": {
        "zh-CN": "无法获取更新信息：{message}",
        "en": "Could not fetch update information: {message}",
        "ja": "更新情報を取得できませんでした: {message}",
        "ru": "Не удалось получить сведения об обновлении: {message}",
        "ko": "업데이트 정보를 가져올 수 없습니다: {message}",
    },
    "settings_update_available_message": {
        "zh-CN": "发现新版本：{version}\n\n{notes}",
        "en": "New version available: {version}\n\n{notes}",
        "ja": "新しいバージョンがあります: {version}\n\n{notes}",
        "ru": "Доступна новая версия: {version}\n\n{notes}",
        "ko": "새 버전을 사용할 수 있습니다: {version}\n\n{notes}",
    },
    "settings_app_language": {"zh-CN": "界面语言", "en": "UI Language", "ja": "UI 言語"},
    "settings_theme": {"zh-CN": "主题", "en": "Theme", "ja": "テーマ"},
    "settings_background": {"zh-CN": "背景图片", "en": "Background Image", "ja": "背景画像"},
    "settings_background_none": {"zh-CN": "（无）", "en": "(None)", "ja": "（なし）"},
    "browse": {"zh-CN": "浏览...", "en": "Browse...", "ja": "参照..."},
    "translation_provider": {"zh-CN": "用哪个翻译服务", "en": "Translation Backend", "ja": "翻訳バックエンド"},
    "translation_provider_params": {"zh-CN": "翻译服务账号", "en": "Backend Parameters", "ja": "バックエンド設定"},
    "api_key": {"zh-CN": "翻译服务密钥（API Key）", "en": "API Key", "ja": "API Key"},
    "base_url": {"zh-CN": "服务地址（Base URL）", "en": "Base URL", "ja": "Base URL"},
    "request_timeout": {"zh-CN": "等多久算超时（秒）", "en": "Request Timeout (s)", "ja": "リクエストタイムアウト（秒）"},
    "request_retries": {"zh-CN": "失败重试次数", "en": "Retry Count", "ja": "リトライ回数"},
    "source_language": {"zh-CN": "我输入的语言", "en": "Source Language", "ja": "ソース言語"},
    "target_language_2": {"zh-CN": "第二种翻译语言", "en": "Target Language 2", "ja": "目標言語2"},
    "output_format": {"zh-CN": "聊天框里怎么显示", "en": "Output Format", "ja": "出力形式"},
    "send_to_chatbox": {"zh-CN": "发送翻译到聊天框", "en": "Send translation to chatbox", "ja": "翻訳をチャットボックスへ送信"},
    "text_input_hotkey": {"zh-CN": "打开打字翻译的快捷键", "en": "Text Input Hotkey", "ja": "手動入力ホットキー"},
    "asr_backend": {"zh-CN": "Mio 怎么听懂你说话", "en": "Speech Model", "ja": "音声認識モデル"},
    "asr_provider_config": {"zh-CN": "在线听写账号", "en": "Online ASR Settings", "ja": "オンライン ASR 設定"},
    "asr_api_key": {"zh-CN": "在线听写密钥（API Key）", "en": "ASR API Key", "ja": "ASR API Key"},
    "asr_region": {"zh-CN": "服务区域", "en": "Service Region", "ja": "サービス地域"},
    "asr_listen": {"zh-CN": "听别人时用哪个听写服务", "en": "Reverse Translation Speech Model", "ja": "逆翻訳音声モデル"},
    "input_device_mode": {"zh-CN": "麦克风选择方式", "en": "Microphone Mode", "ja": "マイクモード"},
    "input_device_mode_auto": {"zh-CN": "自动跟随系统默认", "en": "Auto Follow System Default", "ja": "システム既定を追従"},
    "input_device_mode_fixed": {"zh-CN": "固定指定设备", "en": "Fixed Device", "ja": "固定デバイス"},
    "input_device": {"zh-CN": "麦克风", "en": "Microphone", "ja": "マイク"},
    "input_device_default": {"zh-CN": "系统默认麦克风", "en": "System Default Microphone", "ja": "システム既定マイク"},
    "input_device_missing": {"zh-CN": "未检测到麦克风", "en": "No microphone found", "ja": "マイクが見つかりません"},
    "mic_mute_hotkey": {"zh-CN": "一键闭麦的快捷键", "en": "Mic Mute Hotkey", "ja": "マイクミュートホットキー"},
    "streaming": {"zh-CN": "边说边出字", "en": "Streaming Recognition", "ja": "ストリーミング認識"},
    "partial_interval": {"zh-CN": "多久刷新一次字幕（毫秒）", "en": "Partial Refresh Interval ms", "ja": "部分更新間隔 ms"},
    "recognition_window": {"zh-CN": "每次听多长一段（秒）", "en": "Recognition Window s", "ja": "認識ウィンドウ s"},
    "partial_hits": {"zh-CN": "字幕稳定几次再显示", "en": "Stability Hits", "ja": "安定ヒット数"},
    "vad": {"zh-CN": "什么时候算说完", "en": "VAD / Silence", "ja": "VAD / 無音"},
    "vad_seconds": {"zh-CN": "安静多久后开始翻译（秒）", "en": "Tail Silence Seconds", "ja": "末尾無音秒"},
    "vad_sensitivity": {"zh-CN": "声音判断灵敏度 0-3", "en": "VAD Sensitivity 0-3", "ja": "VAD 感度 0-3"},
    "vad_speech_ratio": {"zh-CN": "语音比例 0-1", "en": "Speech Ratio 0-1", "ja": "音声比率 0-1"},
    "vad_activation": {"zh-CN": "说多长才算真的在说话（秒）", "en": "Activation Threshold s", "ja": "起動しきい値 s"},
    "vad_min_rms": {"zh-CN": "忽略多小的杂音", "en": "Minimum RMS", "ja": "最小 RMS"},
    "min_segment": {"zh-CN": "最短收音长度（秒）", "en": "Min Segment s", "ja": "最短セグメント s"},
    "max_segment": {"zh-CN": "最长收音长度（秒）", "en": "Max Segment s", "ja": "最長セグメント s"},
    "partial_min_speech": {"zh-CN": "最短说话长度（秒）", "en": "Partial Min Speech s", "ja": "部分最短音声 s"},
    "vrc_listen_enabled": {"zh-CN": "听别人说话并翻译", "en": "Enable reverse translation", "ja": "逆翻訳を有効化"},
    "vrc_listen_overlay": {"zh-CN": "显示悬浮窗", "en": "Show overlay", "ja": "オーバーレイ表示"},
    "vrc_listen_send": {"zh-CN": "发送到聊天框", "en": "Send to chatbox", "ja": "チャットへ送信"},
    "vrc_listen_device": {"zh-CN": "桌面音频设备", "en": "Desktop Audio Device", "ja": "デスクトップ音声デバイス"},
    "vrc_listen_device_default": {"zh-CN": "自动检测 VRChat 输出设备", "en": "Auto Detect VRChat Output", "ja": "VRChat 出力を自動検出"},
    "vrc_listen_device_missing": {"zh-CN": "未检测到播放设备", "en": "No playback device found", "ja": "再生デバイスがありません"},
    "self_suppress": {"zh-CN": "不要把 Mio 自己朗读的声音又翻译一遍", "en": "Suppress own TTS echo", "ja": "自分の TTS エコーを抑制"},
    "self_suppress_seconds": {"zh-CN": "朗读后暂停听别人多久（秒）", "en": "Self Suppress Seconds", "ja": "自己抑制秒数"},
    "segment_duration": {"zh-CN": "每次听别人多长一段（秒）", "en": "Segment Duration s", "ja": "セグメント秒数"},
    "tail_silence": {"zh-CN": "别人停顿多久后开始翻译（秒）", "en": "Tail Silence s", "ja": "末尾無音 s"},
    "tts_enable": {"zh-CN": "让 Mio 自动朗读", "en": "Enable interpretation voice", "ja": "同時通訳を有効化"},
    "tts_output_vrchat": {"zh-CN": "让 VRChat 也听到 Mio", "en": "Output to VRChat", "ja": "VRChat へ出力"},
    "tts_monitor": {"zh-CN": "我自己也听一遍 Mio 的声音", "en": "Monitor TTS voice", "ja": "TTS 音声をモニター"},
    "tts_auto_read": {"zh-CN": "自动朗读翻译结果", "en": "Auto-read translated result", "ja": "翻訳結果を自動読み上げ"},
    "tts_test_text": {"zh-CN": "你好，这是测试朗读。", "en": "Hello, this is a test.", "ja": "こんにちは、これは読み上げテストです。"},
    "tts_stop": {"zh-CN": "停止", "en": "Stop", "ja": "停止"},
    "tts_no_virtual_device": {"zh-CN": "未检测到 MixLine 虚拟麦克风", "en": "MixLine virtual microphone was not detected.", "ja": "MixLine 仮想マイクが見つかりません。"},
    "tts_api_section": {"zh-CN": "在线语音账号", "en": "API TTS", "ja": "API 音声合成"},
    "tts_service_region": {"zh-CN": "服务区域", "en": "Service Region", "ja": "サービス地域"},
    "tts_api_model": {"zh-CN": "使用哪个声音", "en": "Model", "ja": "モデル"},
    "tts_api_voice": {"zh-CN": "音色", "en": "Voice", "ja": "ボイス"},
    "tts_api_hint": {
        "zh-CN": "这些声音来自在线服务。密钥、服务区域和声音名称要和你的账号一致。",
        "en": "These voices are synthesized through cloud APIs. Keep the API key, service region, and model in sync with the account and available region.",
        "ja": "これらの音声はクラウド API で合成されます。API Key、サービス地域、モデルはアカウントと利用可能地域に合わせてください。",
    },
    "roleplay_enabled": {"zh-CN": "启用角色扮演", "en": "Enable roleplay", "ja": "ロールプレイを有効化"},
    "roleplay_preset": {"zh-CN": "预设", "en": "Preset", "ja": "プリセット"},
    "persona_name": {"zh-CN": "角色名", "en": "Persona Name", "ja": "キャラクター名"},
    "persona_prompt": {"zh-CN": "Mio 说话风格说明", "en": "BERT Prompt / Persona Prompt", "ja": "BERT Prompt / ペルソナ"},
    "persona_glossary": {"zh-CN": "词汇提示", "en": "Glossary Hints", "ja": "用語ヒント"},
    "save_failed": {"zh-CN": "保存失败", "en": "Save Failed", "ja": "保存失敗"},
    "settings_save_failed_message": {
        "zh-CN": "无法保存配置：{error}",
        "en": "Unable to save configuration: {error}",
        "ja": "設定を保存できません: {error}",
        "ru": "Не удалось сохранить настройки: {error}",
        "ko": "설정을 저장할 수 없습니다: {error}",
    },
    "api_missing_save_message": {
        "zh-CN": "{backend} 需要 API Key。现在的聊天框显示方式需要联网翻译，请先填写 API Key，或把“聊天框里怎么显示”改成“仅原文”。",
        "en": "{backend} needs an API Key. The current output format calls the translation API, so fill in the API Key first or switch Output Format to Original only.",
        "ja": "{backend} には API Key が必要です。現在の出力形式では翻訳 API を呼び出すため、API Key を入力するか、出力形式を「原文のみ」に変更してください。",
        "ru": "{backend} требует API Key. Текущий формат вывода вызывает API перевода; укажите API Key или переключите формат на только оригинал.",
        "ko": "{backend}에는 API Key가 필요합니다. 현재 출력 형식은 번역 API를 호출하므로 API Key를 입력하거나 출력 형식을 원문만으로 바꾸세요.",
    },
    "recognition_window_too_short": {
        "zh-CN": "每次听的时长不能短于字幕刷新间隔。",
        "en": "Recognition window must not be shorter than the refresh interval.",
        "ja": "認識ウィンドウは更新間隔より短くできません。",
        "ru": "Окно распознавания не должно быть короче интервала обновления.",
        "ko": "인식 창 길이는 새로고침 간격보다 짧을 수 없습니다.",
    },
    "hotkey_error": {"zh-CN": "快捷键有问题", "en": "Hotkey Error", "ja": "ホットキーエラー"},
}

QT_SETTINGS_COPY.update({
    "chatbox_template": {
        "zh-CN": "自定义聊天框内容",
        "en": "Chatbox Template",
        "ja": "Chatbox Template",
        "ru": "Шаблон Chatbox",
        "ko": "Chatbox 템플릿",
    },
    "fallback_backends": {
        "zh-CN": "备用翻译服务",
        "en": "Fallback Backends",
        "ja": "Fallback Backends",
        "ru": "Резервные бэкенды",
        "ko": "예비 번역 백엔드",
    },
    "target_language_3": {
        "zh-CN": "第三种翻译语言",
        "en": "Target Language 3",
        "ja": "Target Language 3",
        "ru": "Целевой язык 3",
        "ko": "대상 언어 3",
    },
    "target_language_disabled": {
        "zh-CN": "关闭",
        "en": "Disabled",
        "ja": "Disabled",
        "ru": "Отключено",
        "ko": "끄기",
    },
    "settings_dictionary": {
        "zh-CN": "识别纠错词典",
        "en": "Correction Dictionary",
        "ja": "補正辞書",
    },
    "settings_dictionary_hint": {
        "zh-CN": "只用来修正语音识别错字。平时不会联网，只有你点更新时才会下载官方词典。",
        "en": "Only used to fix speech-to-text mistakes. It stays offline until you click update.",
        "ja": "音声認識の誤字修正用です。更新ボタンを押した時だけオンライン取得します。",
    },
    "settings_dictionary_update": {
        "zh-CN": "更新词典",
        "en": "Update Dictionary",
        "ja": "辞書を更新",
    },
    "settings_dictionary_custom": {
        "zh-CN": "自定义词典",
        "en": "Custom Dictionary",
        "ja": "カスタム辞書",
    },
    "settings_dictionary_custom_hint": {
        "zh-CN": "第一栏填你希望 Mio 听成什么，第二栏填它经常听错成什么。多个错词可用逗号、分号或换行分隔。",
        "en": "Enter the corrected word first, then the ASR mistakes to replace. Separate multiple wrong terms with commas, semicolons, or new lines.",
        "ja": "1 つ目に補正後の単語、2 つ目に ASR が誤認識する語を入力します。複数ある場合はカンマ、セミコロン、改行で区切れます。",
    },
    "settings_dictionary_custom_replacement": {
        "zh-CN": "标准词",
        "en": "Correct Word",
        "ja": "補正後の単語",
    },
    "settings_dictionary_custom_patterns": {
        "zh-CN": "错词",
        "en": "Wrong Terms",
        "ja": "誤認識語",
    },
    "settings_dictionary_custom_patterns_hint": {
        "zh-CN": "再次保存同一个标准词时，会追加到原有词条下面，不会新建重复词条。",
        "en": "Saving the same correct word again appends to the existing entry instead of creating a duplicate.",
        "ja": "同じ補正後の単語を再保存すると、重複作成せず既存項目へ追加します。",
    },
    "settings_dictionary_custom_save": {
        "zh-CN": "保存词条",
        "en": "Save Entry",
        "ja": "項目を保存",
    },
    "settings_dictionary_custom_missing_replacement": {
        "zh-CN": "请先输入标准词。",
        "en": "Enter the correct word first.",
        "ja": "先に補正後の単語を入力してください。",
    },
    "settings_dictionary_custom_missing_patterns": {
        "zh-CN": "请至少输入一个错词。",
        "en": "Enter at least one wrong term.",
        "ja": "誤認識語を 1 つ以上入力してください。",
    },
    "settings_dictionary_custom_saved": {
        "zh-CN": "已将“{replacement}”写入用户词典，当前共有 {total} 个错词。",
        "en": "Saved \"{replacement}\" to the user dictionary with {total} wrong terms total.",
        "ja": "「{replacement}」をユーザー辞書へ保存しました。誤認識語は合計 {total} 件です。",
    },
    "settings_dictionary_custom_failed": {
        "zh-CN": "保存用户词典失败：{message}",
        "en": "Failed to save the user dictionary: {message}",
        "ja": "ユーザー辞書の保存に失敗しました: {message}",
    },
    "tts_import_custom_voice": {
        "zh-CN": "导入音色文件夹",
        "en": "Import Voice Folder",
        "ja": "音声フォルダーを読み込む",
    },
    "tts_custom_voice_count": {
        "zh-CN": "已导入 {count} 个音色包",
        "en": "{count} voice folder(s) imported",
        "ja": "{count} 件の音声フォルダーを読み込み済み",
    },
    "tts_custom_voice_import_done": {
        "zh-CN": "已导入 {count} 个音色包。",
        "en": "Imported {count} custom voice folder(s).",
        "ja": "{count} 件の音声フォルダーを読み込みました。",
    },
    "tts_custom_voice_import_failed": {
        "zh-CN": "导入失败：{message}",
        "en": "Import failed: {message}",
        "ja": "読み込みに失敗しました: {message}",
    },
    "tts_voice_loading": {
        "zh-CN": "正在加载音色...",
        "en": "Loading voices...",
        "ja": "ボイスを読み込み中...",
    },
    "tts_voice_none": {
        "zh-CN": "未找到可用音色",
        "en": "No voices available",
        "ja": "利用可能なボイスがありません",
    },
    "tts_voice_loading": {
        "zh-CN": "加载音色中…",
        "en": "Loading voices…",
        "ja": "音声を読み込み中…",
    },
    "tts_output_device_detected": {
        "zh-CN": "VRChat 输出设备：{device}",
        "en": "VRChat output device: {device}",
        "ja": "VRChat 出力デバイス: {device}",
        "ru": "Устройство вывода VRChat: {device}",
        "ko": "VRChat 출력 장치: {device}",
    },
    "tts_voice_local_service_unavailable": {
        "zh-CN": "没连上本地朗读程序，请先启动对应程序",
        "en": "Local speech engine not detected. Start it first.",
        "ja": "ローカル音声エンジンに接続できません。先に起動してください。",
    },
    "tts_voice_style_runtime_missing": {
        "zh-CN": "已找到音色，但朗读所需文件还没准备好",
        "en": "Voices found, but the inference runtime is not ready.",
        "ja": "ボイスは見つかりましたが、推論ランタイムが未準備です。",
    },
    "tts_voice_custom_missing": {
        "zh-CN": "还没有可用的自定义音色",
        "en": "No usable custom voice has been imported yet.",
        "ja": "利用できるカスタムボイスがまだありません。",
    },
    "tts_output_device_hint": {
        "zh-CN": "想让 VRChat 里的人听到 Mio 朗读，需要先装 MixLine。装好后，把你的真实麦克风接进 MixLine，再在 VRChat 里选择 MixLine 麦克风。",
        "en": "Install MixLine to route interpretation voice into the VRChat microphone. After installing, add your real mic in MixLine and select the MixLine virtual mic in VRChat.",
        "ja": "同時通訳の音声を VRChat のマイクへ送るには MixLine が必要です。インストール後、MixLine に実マイクを追加し、VRChat で MixLine の仮想マイクを選択してください。",
    },
    "tts_install_virtual_device": {
        "zh-CN": "想让 VRChat 里的人听到 Mio 朗读，需要安装 MixLine。\n\n大概这样做：\n1. 安装并启动 MixLine\n2. 在 MixLine 里接入你的真实麦克风\n3. 在 Mio 里打开“让 VRChat 也听到 Mio”\n4. 在 VRChat 里把麦克风选成 MixLine 的虚拟麦克风",
        "en": "To let VRChat hear the interpretation voice, install MixLine.\n\nSetup outline:\n1. Install and start MixLine\n2. Add your real microphone in MixLine\n3. Enable Output to VRChat in this app and route TTS to the MixLine virtual input\n4. In VRChat, choose the virtual microphone exposed by MixLine",
        "ja": "VRChat に同時通訳の音声を届けるには、MixLine が必要です。\n\n設定の流れ:\n1. MixLine をインストールして起動\n2. MixLine に実マイクを追加\n3. このアプリで「VRChat に出力」を有効化し、TTS を MixLine の仮想入力へ送る\n4. VRChat で MixLine が公開する仮想マイクを選択",
    },
    "tts_download_mixline": {
        "zh-CN": "下载 MixLine",
        "en": "Download MixLine",
        "ja": "MixLine をダウンロード",
    },
    "tts_show_guide": {
        "zh-CN": "安装 MixLine",
        "en": "Set Up MixLine",
        "ja": "MixLine を設定",
    },
    "close": {"zh-CN": "关闭", "en": "Close", "ja": "閉じる"},
    "hotkey_section": {"zh-CN": "快捷键", "en": "Hotkeys", "ja": "ホットキー"},
    "model_section": {"zh-CN": "模型下载", "en": "Model Status", "ja": "モデル状態"},
    "model_ready": {
        "zh-CN": "已下载：{model_id}",
        "en": "Model ready: {model_id}",
        "ja": "モデル準備済み: {model_id}",
    },
    "model_pending": {
        "zh-CN": "未下载：{model_id}",
        "en": "Model not loaded: {model_id} (downloads on first use)",
        "ja": "モデル未読み込み: {model_id}（初回起動時にダウンロード）",
    },
    "model_download": {
        "zh-CN": "下载模型",
        "en": "Download Model",
        "ja": "モデルをダウンロード",
    },
    "model_check_hint": {
        "zh-CN": "请按需求下载。已下载的模型会显示绿色；下载到本机后可以离线使用。",
        "en": "Local models use the built-in accelerated downloader with mirror fallback. Once downloaded, they work offline.",
        "ja": "ローカルモデルは内蔵の高速ダウンロード経路とミラー fallback を使います。完了後はオフラインで利用できます。",
    },
    "model_current_section": {
        "zh-CN": "当前麦克风使用的模型",
        "en": "Current microphone model",
        "ja": "現在のマイクモデル",
    },
    "model_picker_hint": {
        "zh-CN": "请按需求下载。中文/粤语优先 SenseVoice；经常听英语、日语等外语可以下载 Whisper。",
        "en": "Download what you need. SenseVoice is best for Chinese/Cantonese; Whisper is useful for English, Japanese, and other languages.",
        "ja": "必要なものだけダウンロードしてください。中国語/広東語は SenseVoice、英語・日本語などは Whisper が向いています。",
    },
    "model_download_desc_sensevoice": {
        "zh-CN": "适合中文、粤语。下载后，Mio 可以在本机把你的声音变成文字。",
        "en": "Best for Chinese and Cantonese. After download, Mio can recognize speech locally.",
        "ja": "中国語・広東語向け。ダウンロード後、Mio は音声をローカルで文字にできます。",
    },
    "model_download_desc_whisper": {
        "zh-CN": "适合听英语、日语等外语。下载后，不需要在线听写服务也能用。",
        "en": "Good for English, Japanese, and other languages. Works without an online speech service after download.",
        "ja": "英語・日本語などの外語向け。ダウンロード後はオンライン音声認識なしで使えます。",
    },
    "model_file_id": {
        "zh-CN": "文件：{model_id}",
        "en": "File: {model_id}",
        "ja": "ファイル：{model_id}",
    },
    "model_status_ready": {"zh-CN": "已下载", "en": "Downloaded", "ja": "ダウンロード済み"},
    "model_status_pending": {"zh-CN": "未下载", "en": "Not downloaded", "ja": "未ダウンロード"},
    "logs_section": {
        "zh-CN": "问题日志",
        "en": "Issue Logs",
        "ja": "問題ログ",
    },
    "open_logs_folder": {
        "zh-CN": "打开日志文件夹",
        "en": "Open Logs Folder",
        "ja": "ログフォルダーを開く",
    },
    "logs_folder_hint": {
        "zh-CN": "遇到问题时点这里，直接把日志文件夹里的 mio.log 发给开发者。",
        "en": "Use this when reporting issues. Send mio.log from the logs folder to the developer.",
        "ja": "問題報告時に使用します。ログフォルダー内の mio.log を開発者へ送ってください。",
    },
    "recommendation": {
        "zh-CN": "推荐度",
        "en": "Recommendation",
        "ja": "おすすめ度",
    },
    "recommended_high": {
        "zh-CN": "★★★★★ 推荐",
        "en": "★★★★★ Recommended",
        "ja": "★★★★★ 推奨",
    },
    "recommended_medium": {
        "zh-CN": "★★★★ 适合特定场景",
        "en": "★★★★ Good for specific cases",
        "ja": "★★★★ 特定用途向け",
    },
    "recommended_low": {
        "zh-CN": "★★★ 备用",
        "en": "★★★ Backup option",
        "ja": "★★★ 予備",
    },
    "model_title": {
        "zh-CN": "怎么选更合适",
        "en": "Model Pick",
        "ja": "モデル選び",
    },
    "model_score": {
        "zh-CN": "推荐程度",
        "en": "Live score",
        "ja": "リアルタイム評価",
    },
    "speed": {
        "zh-CN": "插件速度",
        "en": "Plugin Speed",
        "ja": "速度",
    },
    "quality": {
        "zh-CN": "翻译质量",
        "en": "Quality",
        "ja": "品質",
    },
    "fit": {
        "zh-CN": "插件建议",
        "en": "Plugin Fit",
        "ja": "推奨度",
    },
    "very_fast": {"zh-CN": "极快", "en": "Very fast", "ja": "とても速い"},
    "fast": {"zh-CN": "快", "en": "Fast", "ja": "速い"},
    "balanced": {"zh-CN": "均衡", "en": "Balanced", "ja": "バランス"},
    "slow": {"zh-CN": "偏慢", "en": "Slower", "ja": "やや遅い"},
    "basic": {"zh-CN": "基础", "en": "Basic", "ja": "基本"},
    "high": {"zh-CN": "高", "en": "High", "ja": "高い"},
    "very_recommended": {"zh-CN": "非常推荐", "en": "Highly Recommended", "ja": "とても推奨"},
    "recommended": {"zh-CN": "推荐", "en": "Recommended", "ja": "推奨"},
    "general": {"zh-CN": "一般", "en": "General", "ja": "普通"},
    "conditional": {"zh-CN": "一般", "en": "General", "ja": "普通"},
    "not_recommended": {"zh-CN": "不推荐", "en": "Not Recommended", "ja": "非推奨"},
    "live_default": {
        "zh-CN": "推荐给大多数玩家：速度够快，质量也稳，长时间实时翻译不容易拖慢聊天节奏。",
        "en": "Best for most players: quick enough, stable quality, and unlikely to slow down live chat.",
        "ja": "多くのプレイヤー向けです。十分速く、品質も安定し、会話のテンポを崩しにくいです。",
    },
    "balanced_quality": {
        "zh-CN": "偏质量但还不算慢：适合想让句子更自然、又不想等太久的玩家。",
        "en": "More quality without getting too slow. Pick this when you want smoother lines but still care about delay.",
        "ja": "品質寄りですが遅すぎません。自然な文章にしたいが待ちたくない場合に向きます。",
    },
    "quality_first": {
        "zh-CN": "质量上限高，但会慢：适合手动翻译、长句润色，不建议当默认实时字幕。",
        "en": "High ceiling, but slower. Better for manual translation or polished long lines than always-on subtitles.",
        "ja": "品質上限は高いですが遅めです。常時字幕より、手動翻訳や長文向けです。",
    },
    "economy_first": {
        "zh-CN": "省钱省延迟：短句能用，细节和语气容易比高分选项差一些。",
        "en": "Cheap and low-latency. Fine for short lines, but nuance and tone are weaker than higher-score models.",
        "ja": "低コストで低遅延です。短文には使えますが、細かいニュアンスは上位モデルに劣ります。",
    },
    "reasoning": {
        "zh-CN": "会想得更久：复杂文本可能更准，但实时语音里最容易让字幕慢半拍。",
        "en": "Thinks longer. It may help complex text, but live voice captions will feel delayed.",
        "ja": "より長く考えるタイプです。複雑な文には強い一方、音声字幕では遅れやすいです。",
    },
    "ultra_fast": {
        "zh-CN": "抢速度优先：适合热闹房间、短句聊天，质量不如高分档稳定。",
        "en": "Speed first. Good for busy rooms and short chat, with less stable quality than higher-score picks.",
        "ja": "速度優先です。にぎやかな部屋や短文向けですが、品質は高評価モデルほど安定しません。",
    },
    "flash_mt": {
        "zh-CN": "翻译专用快档：比普通聊天服务更适合实时字幕，适合追求低延迟。",
        "en": "Fast translation model. Better for live subtitles than a general chat model when latency matters.",
        "ja": "翻訳特化の高速モデルです。遅延重視なら一般チャットモデルより向いています。",
    },
    "mt_quality": {
        "zh-CN": "翻译专用质量档：速度和自然度都很强，适合当默认实时翻译。",
        "en": "Quality translation model. Strong speed and natural wording, a good default for live translation.",
        "ja": "翻訳特化の品質寄りモデルです。速度と自然さのバランスが良く、既定に向きます。",
    },
    "legacy_mt": {
        "zh-CN": "旧兼容档：能用但不是首选，新用户建议直接选 plus 或 flash。",
        "en": "Legacy option. Usable, but new users should usually pick plus or flash instead.",
        "ja": "旧互換向けです。使えますが、新規ユーザーは plus / flash を選ぶ方が無難です。",
    },
    "general_high_quality": {
        "zh-CN": "通用高质量档：表达更完整，适合复杂句子；实时速度通常不如翻译专用或 flash 档。",
        "en": "General high-quality model. Better phrasing for complex lines, usually slower than translation or flash models.",
        "ja": "汎用高品質モデルです。複雑な文の表現は良いですが、翻訳特化や flash より遅めです。",
    },
    "custom": {
        "zh-CN": "自定义选项：Mio 还没有实测分，建议先用短句试试速度和稳定性。",
        "en": "Custom model. Not scored from built-in experience yet; test short lines first for speed and stability.",
        "ja": "カスタムモデルです。内蔵評価はまだないため、短文で速度と安定性を確認してください。",
    },
    "qwen_model_recommendation": {
        "zh-CN": "推荐度 · ★★★★★ 推荐 · 在线听写延迟低，不下载语音包也能直接用。",
        "en": "Recommendation · ★★★★★ Recommended · Low-latency online ASR when local models are not installed.",
        "ja": "おすすめ度 · ★★★★★ 推奨 · ローカルモデルなしで使いやすい低遅延オンライン ASR。",
    },
    "gemini_model_recommendation": {
        "zh-CN": "推荐度 · ★★★★ 适合特定场景 · 适合需要 Gemini Live 流程的用户。",
        "en": "Recommendation · ★★★★ Specific cases · Good when you already use a Gemini Live flow.",
        "ja": "おすすめ度 · ★★★★ 特定用途向け · Gemini Live を使う構成向け。",
    },
    "missing_model_prompt_title": {
        "zh-CN": "需要先下载语音包",
        "en": "Local Model Required",
        "ja": "ローカルモデルが必要です",
    },
    "missing_model_prompt_body": {
        "zh-CN": "你选的 {engine_label} 需要先下载这个文件：{model_id}\n\n要打开下载窗口吗？文件会保存在 Mio 文件夹里，网络中断后重新下载会尽量接着来。",
        "en": "The selected {engine_label} engine needs a local model first: {model_id}\n\nOpen the built-in downloader now? Files are stored inside the Mio folder, and retrying will resume when possible after a network interruption.",
        "ja": "選択中の {engine_label} にはローカルモデルが必要です: {model_id}\n\n内蔵ダウンローダーを開きますか？ファイルは Mio フォルダー内に保存され、通信が切れた後も可能な限り続きから再開します。",
    },
    "open_downloader": {
        "zh-CN": "打开下载",
        "en": "Open Downloader",
        "ja": "ダウンロードを開く",
    },
    "later": {
        "zh-CN": "稍后",
        "en": "Later",
        "ja": "後で",
    },
    "download_voicevox": {
        "zh-CN": "下载 VOICEVOX",
        "en": "Download VOICEVOX",
        "ja": "VOICEVOX をダウンロード",
    },
    "download_aivis": {
        "zh-CN": "下载 AivisSpeech",
        "en": "Download AivisSpeech",
        "ja": "AivisSpeech をダウンロード",
    },
    "local_tts_unavailable": {
        "zh-CN": "未检测到本地服务。请先启动 {engine}，然后返回这里刷新音色。",
        "en": "{engine} is not detected. Start the local app first, then return here and refresh voices.",
        "ja": "{engine} が検出されません。先にローカルアプリを起動してから、ここへ戻ってボイスを更新してください。",
    },
    "local_tts_ready": {
        "zh-CN": "已检测到 {engine}，可以读取音色并让 Mio 朗读。",
        "en": "{engine} local service is detected. Voices can be loaded for interpretation.",
        "ja": "{engine} のローカルサービスを検出しました。ボイスを読み込んで同時通訳に使えます。",
    },
    "tts_custom_voice_picker": {
        "zh-CN": "自定义音色包",
        "en": "Custom voice pack",
        "ja": "カスタムボイスパック",
    },
    "tts_choose_voice_folder": {
        "zh-CN": "选择文件夹",
        "en": "Choose Folder",
        "ja": "フォルダーを選択",
    },
    "avatar_section": {"zh-CN": "让角色状态跟着 Mio 变化", "en": "Avatar / OSC", "ja": "Avatar 同期 / OSC"},
    "avatar_subtitle": {
        "zh-CN": "把 Mio 正在翻译、正在朗读、是否闭麦等状态发给你的 VRChat 角色。用不到就不用改。",
        "en": "Sync translation state to VRChat avatar parameters. Target language is sent as an integer parameter.",
        "ja": "翻訳状態を VRChat の Avatar パラメータへ同期します。対象言語は整数パラメータで送信します。",
    },
    "avatar_sync_enabled": {"zh-CN": "把 Mio 状态发给角色", "en": "Enable Avatar Sync", "ja": "Avatar 同期を有効化"},
    "avatar_sync_hint": {
        "zh-CN": "如果你会改 Avatar 菜单里的开关名，可以用这些名字：MioTranslating / MioSpeaking / MioMuted / MioError / MioTargetLanguage",
        "en": "Recommended params: MioTranslating / MioSpeaking / MioMuted / MioError / MioTargetLanguage",
        "ja": "推奨パラメータ: MioTranslating / MioSpeaking / MioMuted / MioError / MioTargetLanguage",
    },
    "avatar_param_translating": {"zh-CN": "正在翻译时的开关名", "en": "Translating Param", "ja": "翻訳中パラメータ"},
    "avatar_param_speaking": {"zh-CN": "Mio 正在朗读时的开关名", "en": "Speaking Param", "ja": "発話中パラメータ"},
    "avatar_param_muted": {"zh-CN": "麦克风关闭时的开关名", "en": "Mic Muted Param", "ja": "マイクミュートパラメータ"},
    "avatar_param_error": {"zh-CN": "出错时的开关名", "en": "Error Param", "ja": "エラーパラメータ"},
    "avatar_param_target_language": {"zh-CN": "目标语言的名字", "en": "Target Language Param", "ja": "対象言語パラメータ"},
    "audio_diagnostics": {"zh-CN": "查看声音情况", "en": "Audio Diagnostics", "ja": "音声診断"},
    "vad_calibration": {"zh-CN": "自动断句校准", "en": "VAD Calibration", "ja": "VAD キャリブレーション"},
    "open_mic_diagnostics": {"zh-CN": "查看麦克风声音情况", "en": "Open mic diagnostics", "ja": "マイク診断を開く"},
    "open_listen_diagnostics": {"zh-CN": "查看别人声音情况", "en": "Open listen diagnostics", "ja": "リスン診断を開く"},
    "open_mic_calibration": {"zh-CN": "校准我的麦克风断句", "en": "Calibrate mic VAD", "ja": "マイク VAD を調整"},
    "open_listen_calibration": {"zh-CN": "校准听别人时的断句", "en": "Calibrate listen VAD", "ja": "リスン VAD を調整"},
    "osc_listener_section": {"zh-CN": "VRChat 控制 Mio（OSC）", "en": "OSC Listener / Control", "ja": "OSC 受信 / 制御"},
    "osc_listener_enabled": {"zh-CN": "允许 VRChat 给 Mio 发开关（OSC）", "en": "Enable OSC listener", "ja": "OSC 受信を有効化"},
    "osc_receive_host": {"zh-CN": "Mio 接收地址", "en": "Receive Host", "ja": "受信ホスト"},
    "osc_receive_port": {"zh-CN": "Mio 接收端口", "en": "Receive Port", "ja": "受信ポート"},
    "osc_sync_mute_self": {"zh-CN": "VRChat 闭麦时，Mio 也跟着闭麦", "en": "Sync VRChat MuteSelf", "ja": "VRChat MuteSelf を同期"},
    "osc_allow_avatar_control": {"zh-CN": "允许角色菜单控制 Mio", "en": "Allow avatar controls", "ja": "Avatar 操作を許可"},
    "osc_control_prefix": {"zh-CN": "这些开关名字的开头", "en": "Control Param Prefix", "ja": "制御パラメータ接頭辞"},
    "osc_param_toggle_mic": {"zh-CN": "角色里“开关麦克风”的名字", "en": "Mic Toggle Param", "ja": "マイク切替パラメータ"},
    "osc_param_toggle_listen": {"zh-CN": "角色里“听别人”的名字", "en": "Listen Toggle Param", "ja": "リスン切替パラメータ"},
    "osc_param_toggle_tts": {"zh-CN": "角色里“让 Mio 朗读”的名字", "en": "TTS Toggle Param", "ja": "TTS 切替パラメータ"},
    "osc_param_toggle_overlay": {"zh-CN": "角色里“显示字幕窗”的名字", "en": "Overlay Toggle Param", "ja": "Overlay 切替パラメータ"},
    "listen_vad_min_rms": {"zh-CN": "听别人时忽略多小的杂音", "en": "Listen Minimum RMS", "ja": "リスン最小 RMS"},
})

QT_SETTINGS_COPY.update({
    "translation_service_region": {
        "zh-CN": "服务区域",
        "en": "Service Region",
        "ja": "サービス地域",
        "ru": "Регион сервиса",
        "ko": "서비스 지역",
    },
    "qwen_region_singapore": {
        "zh-CN": "国际站（新加坡，推荐）",
        "en": "International (Singapore, recommended)",
        "ja": "国際版（シンガポール、推奨）",
        "ru": "Международный (Сингапур, рекомендуется)",
        "ko": "국제 서비스(싱가포르, 권장)",
    },
    "qwen_region_china_mainland": {
        "zh-CN": "中国大陆",
        "en": "Mainland China",
        "ja": "中国本土",
        "ru": "Китайский материк",
        "ko": "중국 본토",
    },
    "qwen_region_custom": {
        "zh-CN": "自定义服务地址（高级）",
        "en": "Custom Base URL",
        "ja": "カスタム Base URL",
        "ru": "Пользовательский Base URL",
        "ko": "사용자 지정 Base URL",
    },
    "deepseek_region_official": {
        "zh-CN": "DeepSeek 官方（推荐）",
        "en": "DeepSeek official (recommended)",
        "ja": "DeepSeek 公式（推奨）",
        "ru": "DeepSeek official (рекомендуется)",
        "ko": "DeepSeek 공식(권장)",
    },
    "deepseek_region_custom": {
        "zh-CN": "第三方转发地址（高级）",
        "en": "Custom proxy / relay Base URL",
        "ja": "カスタム proxy / relay Base URL",
        "ru": "Свой proxy / relay Base URL",
        "ko": "사용자 지정 프록시 / 중계 Base URL",
    },
    "xiaomi_region_global": {
        "zh-CN": "按量付费（全球）",
        "en": "Pay-as-you-go (Global)",
        "ja": "従量課金（グローバル）",
        "ru": "Pay-as-you-go (глобальный)",
        "ko": "종량제(글로벌)",
    },
    "xiaomi_region_china_cluster": {
        "zh-CN": "Token Plan（中国集群）",
        "en": "Token Plan (China cluster)",
        "ja": "Token Plan（中国クラスター）",
        "ru": "Token Plan (кластер Китай)",
        "ko": "Token Plan(중국 클러스터)",
    },
    "xiaomi_region_singapore_cluster": {
        "zh-CN": "Token Plan（新加坡集群）",
        "en": "Token Plan (Singapore cluster)",
        "ja": "Token Plan（シンガポールクラスター）",
        "ru": "Token Plan (кластер Сингапур)",
        "ko": "Token Plan(싱가포르 클러스터)",
    },
    "xiaomi_region_europe_cluster": {
        "zh-CN": "Token Plan（欧洲集群）",
        "en": "Token Plan (Europe cluster)",
        "ja": "Token Plan（ヨーロッパクラスター）",
        "ru": "Token Plan (кластер Европа)",
        "ko": "Token Plan(유럽 클러스터)",
    },
    "xiaomi_region_custom": {
        "zh-CN": "自定义服务地址（高级）",
        "en": "Custom Base URL",
        "ja": "カスタム Base URL",
        "ru": "Пользовательский Base URL",
        "ko": "사용자 지정 Base URL",
    },
    "nvidia_region_global": {
        "zh-CN": "NVIDIA 官方在线服务（全球）",
        "en": "Hosted API (Global)",
        "ja": "ホスト API（グローバル）",
        "ru": "Hosted API (глобальный)",
        "ko": "호스팅 API(글로벌)",
    },
    "nvidia_region_custom": {
        "zh-CN": "自己的 NVIDIA 服务地址（高级）",
        "en": "Custom NIM / proxy Base URL",
        "ja": "カスタム NIM / プロキシ Base URL",
        "ru": "Свой NIM / proxy Base URL",
        "ko": "사용자 지정 NIM / 프록시 Base URL",
    },
    "qwen_translation_region_hint": {
        "zh-CN": "大陆玩家选中国大陆；海外玩家优先选国际站。只有在你有自己的转发地址或兼容服务地址时，才选自定义。",
        "en": "Use Mainland China in China; overseas players should prefer International. Choose Custom for a proxy or private compatible endpoint.",
        "ja": "中国本土では中国本土、海外では国際版を推奨します。プロキシや独自互換エンドポイントはカスタムを選びます。",
        "ru": "В Китае выберите материковый регион; за рубежом лучше международный. Custom подходит для прокси или частного совместимого адреса.",
        "ko": "중국 본토에서는 중국 본토를, 해외에서는 국제 서비스를 권장합니다. 프록시나 사설 호환 주소는 사용자 지정을 선택하세요.",
    },
})

_SETTINGS_RU_KO_COPY = {
    "header_title": {"ru": "Настройки", "ko": "설정"},
    "header_subtitle": {
        "ru": "Настройте перевод, речь, обратный перевод, синхронный перевод и ролевые параметры.",
        "ko": "번역, 음성, 역번역, 동시통역, 롤플레이 설정을 조정합니다.",
    },
    "translation_section": {"ru": "Общее / Перевод", "ko": "일반 / 번역"},
    "voice_section": {"ru": "Настройки речи", "ko": "음성 설정"},
    "vrc_listen_section": {"ru": "Обратный перевод", "ko": "역번역"},
    "tts_section": {"ru": "Озвучивание", "ko": "음성 읽기"},
    "roleplay_section": {"ru": "Ролевой стиль", "ko": "롤플레이"},
    "tts_api_section": {"ru": "API-озвучивание", "ko": "API 음성 합성"},
    "tts_service_region": {"ru": "Регион сервиса", "ko": "서비스 지역"},
    "tts_api_model": {"ru": "Модель", "ko": "모델"},
    "tts_api_voice": {"ru": "Голос", "ko": "음색"},
    "tts_api_hint": {
        "ru": "Эти голоса синтезируются через облачные API. API Key, регион сервиса и модель должны соответствовать аккаунту и доступному региону.",
        "ko": "이 음색은 클라우드 API로 합성됩니다. API Key, 서비스 지역, 모델은 계정 및 사용 가능한 지역과 일치해야 합니다.",
    },
    "avatar_section": {"ru": "Синхронизация Avatar / OSC", "ko": "Avatar 동기화 / OSC"},
    "avatar_subtitle": {
        "ru": "Синхронизирует состояние перевода с параметрами VRChat Avatar. Целевой язык отправляется как целочисленный параметр.",
        "ko": "번역 상태를 VRChat Avatar 파라미터에 동기화합니다. 대상 언어는 정수 파라미터로 전송됩니다.",
    },
    "avatar_sync_enabled": {"ru": "Включить синхронизацию Avatar", "ko": "Avatar 동기화 켜기"},
    "avatar_sync_hint": {
        "ru": "Рекомендуемые параметры: MioTranslating / MioSpeaking / MioMuted / MioError / MioTargetLanguage",
        "ko": "권장 파라미터: MioTranslating / MioSpeaking / MioMuted / MioError / MioTargetLanguage",
    },
    "avatar_param_translating": {"ru": "Параметр перевода", "ko": "번역 중 파라미터"},
    "avatar_param_speaking": {"ru": "Параметр речи", "ko": "말하는 중 파라미터"},
    "avatar_param_muted": {"ru": "Параметр отключенного микрофона", "ko": "마이크 음소거 파라미터"},
    "avatar_param_error": {"ru": "Параметр ошибки", "ko": "오류 파라미터"},
    "avatar_param_target_language": {"ru": "Параметр целевого языка", "ko": "대상 언어 파라미터"},
    "settings_check_update": {"ru": "Проверить обновления", "ko": "업데이트 확인"},
    "settings_up_to_date": {"ru": "Установлена последняя версия.", "ko": "최신 버전입니다."},
    "settings_check_update_failed": {"ru": "Не удалось проверить обновления", "ko": "업데이트 확인 실패"},
    "settings_app_language": {"ru": "Язык интерфейса", "ko": "UI 언어"},
    "settings_theme": {"ru": "Тема", "ko": "테마"},
    "settings_background": {"ru": "Фоновое изображение", "ko": "배경 이미지"},
    "settings_background_none": {"ru": "(нет)", "ko": "(없음)"},
    "browse": {"ru": "Обзор...", "ko": "찾아보기..."},
    "translation_provider": {"ru": "Бэкенд перевода", "ko": "번역 백엔드"},
    "translation_provider_params": {"ru": "Параметры бэкенда", "ko": "백엔드 매개변수"},
    "request_timeout": {"ru": "Таймаут запроса (с)", "ko": "요청 시간 제한(초)"},
    "request_retries": {"ru": "Повторы при ошибке", "ko": "실패 재시도 횟수"},
    "source_language": {"ru": "Исходный язык", "ko": "원본 언어"},
    "target_language_2": {"ru": "Целевой язык 2", "ko": "대상 언어 2"},
    "output_format": {"ru": "Формат вывода", "ko": "출력 형식"},
    "send_to_chatbox": {"ru": "Отправлять в Chatbox", "ko": "Chatbox로 전송"},
    "text_input_hotkey": {"ru": "Горячая клавиша ввода", "ko": "텍스트 입력 단축키"},
    "asr_backend": {"ru": "Бэкенд распознавания", "ko": "음성 인식 백엔드"},
    "asr_provider_config": {"ru": "Параметры ASR", "ko": "ASR 설정"},
    "asr_api_key": {"ru": "ASR API Key", "ko": "ASR API Key"},
    "asr_region": {"ru": "Регион сервиса", "ko": "서비스 지역"},
    "asr_listen": {"ru": "ASR для обратного перевода", "ko": "역번역 ASR"},
    "input_device_mode": {"ru": "Режим микрофона", "ko": "마이크 모드"},
    "input_device_mode_auto": {"ru": "Следовать системному", "ko": "시스템 기본값 따르기"},
    "input_device_mode_fixed": {"ru": "Фиксированное устройство", "ko": "고정 장치"},
    "input_device": {"ru": "Микрофон", "ko": "마이크"},
    "input_device_default": {"ru": "Авто: системный микрофон", "ko": "자동: 시스템 기본 마이크"},
    "input_device_missing": {"ru": "Выбранное устройство не найдено", "ko": "선택한 장치를 찾을 수 없음"},
    "mic_mute_hotkey": {"ru": "Горячая клавиша mute", "ko": "마이크 음소거 단축키"},
    "streaming": {"ru": "Потоковое распознавание", "ko": "스트리밍 인식"},
    "partial_interval": {"ru": "Интервал partial (мс)", "ko": "Partial 갱신 간격(ms)"},
    "recognition_window": {"ru": "Окно распознавания (с)", "ko": "인식 창(초)"},
    "partial_hits": {"ru": "Стабильные совпадения", "ko": "안정 접두사 횟수"},
    "vad": {"ru": "VAD", "ko": "VAD"},
    "vad_seconds": {"ru": "Тишина в конце (с)", "ko": "문장 끝 무음(초)"},
    "vrc_listen_enabled": {"ru": "Включить обратный перевод", "ko": "역번역 켜기"},
    "vrc_listen_overlay": {"ru": "Показывать окно", "ko": "오버레이 표시"},
    "vrc_listen_send": {"ru": "Отправлять в Chatbox", "ko": "Chatbox로 전송"},
    "vrc_listen_device": {"ru": "Устройство вывода VRChat", "ko": "VRChat 출력 장치"},
    "vrc_listen_device_default": {"ru": "Авто: системный вывод", "ko": "자동: 시스템 기본 출력"},
    "vrc_listen_device_missing": {"ru": "Выбранный вывод не найден", "ko": "선택한 출력 장치를 찾을 수 없음"},
    "tts_enable": {"ru": "Включить озвучивание", "ko": "음성 읽기 켜기"},
    "tts_output_vrchat": {"ru": "Выводить голос в VRChat", "ko": "VRChat로 음성 출력"},
    "tts_monitor": {"ru": "Слушать локально", "ko": "로컬 모니터링"},
    "tts_auto_read": {"ru": "Авточтение", "ko": "자동 읽기"},
    "tts_test_text": {"ru": "Тестовый текст", "ko": "테스트 문장"},
    "tts_stop": {"ru": "Остановить озвучивание", "ko": "음성 중지"},
    "tts_voice_loading": {"ru": "Загрузка голосов...", "ko": "음색을 불러오는 중..."},
    "tts_voice_none": {"ru": "Доступных голосов нет", "ko": "사용 가능한 음색이 없습니다"},
    "tts_voice_local_service_unavailable": {
        "ru": "Локальный речевой движок не найден. Сначала запустите его.",
        "ko": "로컬 음성 엔진을 찾지 못했습니다. 먼저 실행해 주세요.",
    },
    "tts_voice_style_runtime_missing": {
        "ru": "Голоса найдены, но среда инференса еще не готова.",
        "ko": "음색은 찾았지만 추론 런타임이 아직 준비되지 않았습니다.",
    },
    "tts_voice_custom_missing": {
        "ru": "Пользовательские голоса еще не импортированы.",
        "ko": "아직 사용할 수 있는 사용자 정의 음색이 없습니다.",
    },
    "roleplay_enabled": {"ru": "Включить ролевой стиль", "ko": "롤플레이 스타일 켜기"},
    "roleplay_preset": {"ru": "Пресет", "ko": "프리셋"},
    "persona_name": {"ru": "Имя персонажа", "ko": "페르소나 이름"},
    "persona_prompt": {"ru": "BERT Prompt / персона", "ko": "BERT Prompt / 페르소나"},
    "persona_glossary": {"ru": "Глоссарий", "ko": "용어 힌트"},
    "save_failed": {"ru": "Не удалось сохранить", "ko": "저장 실패"},
    "hotkey_error": {"ru": "Ошибка горячей клавиши", "ko": "단축키 오류"},
}

for _key, _values in _SETTINGS_RU_KO_COPY.items():
    QT_SETTINGS_COPY.setdefault(_key, {}).update(_values)

_SETTINGS_LANGUAGES = ("zh-CN", "en", "ja", "ru", "ko")


def _complete_localized_table(table: dict[str, dict[str, str]]) -> None:
    for values in table.values():
        fallback = values.get("en") or values.get("zh-CN") or next(iter(values.values()), "")
        for language in _SETTINGS_LANGUAGES:
            values.setdefault(language, fallback)


_complete_localized_table(QT_SETTINGS_COPY)

NAV_ITEMS = [
    ("common", QT_SETTINGS_COPY["appearance_section"]["zh-CN"]),
    ("voice", QT_SETTINGS_COPY["recognition_section"]["zh-CN"]),
    ("vrc_listen", QT_SETTINGS_COPY["vrc_listen_section"]["zh-CN"]),
    ("translation", QT_SETTINGS_COPY["translation_domain_section"]["zh-CN"]),
    ("tts", QT_SETTINGS_COPY["tts_section"]["zh-CN"]),
    ("vr_integration", QT_SETTINGS_COPY["vr_integration_section"]["zh-CN"]),
    ("hotkeys", QT_SETTINGS_COPY["hotkey_voice_section"]["zh-CN"]),
    ("model", QT_SETTINGS_COPY["updates_models_section"]["zh-CN"]),
    ("roleplay", QT_SETTINGS_COPY["roleplay_section"]["zh-CN"]),
    ("advanced", QT_SETTINGS_COPY["advanced_section"]["zh-CN"]),
]

FIELD_HINTS: dict[str, dict[str, str]] = {
    "asr_backend": {
        "zh-CN": "选择 Mio 用什么方式把你的声音变成文字。中文/粤语优先 SenseVoice；经常听外语可以选 Whisper；不想下载语音包时可以选 Qwen3-ASR 或 Gemini。",
        "en": "Controls how microphone speech becomes text. Use SenseVoice for Chinese/Cantonese, Whisper for foreign-language listening, or Qwen3-ASR/Gemini if you do not want a local model.",
        "ja": "マイク音声を文字化する方式です。中国語/広東語は SenseVoice、外語リスニングは Whisper、ローカルモデル不要なら Qwen3-ASR または Gemini を使います。",
    },
    "asr_device": {
        "zh-CN": "默认用 CPU，兼容大多数电脑。SenseVoice/Whisper 可以用 NVIDIA 显卡加速；建议 RTX 4060 或更高、8GB 显存以上。6GB 显存也能试，但同时开 VRChat 和 Mio 朗读时可能不稳。",
        "en": "CPU is the default for most lower-end PCs. Local SenseVoice/Whisper can use GPU support; recommended hardware is an NVIDIA RTX 4060 / laptop 4060 or better with at least 8 GB VRAM. 6 GB VRAM can try it, but VRChat and TTS may compete for memory.",
        "ja": "既定は CPU で、低スペック環境向けです。ローカル SenseVoice/Whisper は GPUサポートを使えます。NVIDIA RTX 4060 / Laptop 4060 以上、VRAM 8GB 以上がおすすめです。VRAM 6GB でも試せますが、VRChat や TTS とメモリを奪い合う場合があります。",
    },
    "settings_app_language": {
        "zh-CN": "只影响本工具界面语言，不影响翻译目标语言。",
        "en": "Changes this tool's UI language only. It does not change translation target language.",
        "ja": "このツールの表示言語だけを変更します。翻訳先言語には影響しません。",
    },
    "settings_background": {
        "zh-CN": "选择一张喜欢的图作为主窗口和设置页背景；HUD 会半透明覆盖在上面。",
        "en": "Pick an image for the main window and settings background. The HUD stays translucent above it.",
        "ja": "メイン画面と設定画面の背景画像を選びます。HUD は半透明で重なります。",
    },
    "translation_provider": {
        "zh-CN": "选择把文字翻译成目标语言的服务。Qwen/GPT 这类在线服务通常需要填写密钥（API Key）。",
        "en": "Translates recognized text into the target language. Online backends such as Qwen/GPT need an API key.",
        "ja": "認識した文字を対象言語へ翻訳します。Qwen/GPT などのオンラインバックエンドには API Key が必要です。",
    },
    "source_language": {
        "zh-CN": "你手动打字时用的语言。麦克风说话的语言由“我的麦克风”页决定。",
        "en": "Source language for manual text and translation. Speech language is still controlled by ASR settings.",
        "ja": "手動入力と翻訳の入力言語です。音声認識言語は ASR 設定側で決まります。",
    },
    "target_language_2": {
        "zh-CN": "用于三语显示的第二目标语言；只有输出格式2未关闭时才会调用第二次翻译。",
        "en": "Second target language for trilingual display. It is translated only when Output Format 2 is enabled.",
        "ja": "三言語表示用の 2 つ目の目標言語です。出力形式2が有効な時だけ翻訳します。",
    },
    "output_format": {
        "zh-CN": "决定发到 VRChat 聊天框里的样子，例如只发译文，或译文后面带上原文。",
        "en": "Controls what is sent to the VRChat chatbox, such as translation only or translation with original text.",
        "ja": "VRChat チャットボックスへ送る形式です。翻訳のみ、原文付きなどを選べます。",
    },
    "send_to_chatbox": {
        "zh-CN": "开启后，翻译完成会自动发到 VRChat 聊天框；关闭后只在 Mio 里显示。",
        "en": "When enabled, completed translations are sent to VRChat Chatbox. When off, they only show in the tool.",
        "ja": "有効にすると翻訳結果を VRChat Chatbox へ送信します。無効時はツール内表示のみです。",
    },
    "input_device_mode": {
        "zh-CN": "自动模式会跟随系统默认麦克风；固定模式适合多麦克风或虚拟声卡环境。",
        "en": "Auto follows the system default microphone. Fixed mode is better for multi-mic or virtual audio setups.",
        "ja": "自動はシステム既定マイクを追従します。固定は複数マイクや仮想オーディオ環境向けです。",
    },
    "input_device": {
        "zh-CN": "选择用于识别你说话的麦克风。",
        "en": "Select the microphone used to recognize your speech.",
        "ja": "自分の声を認識するマイクを選びます。",
    },
    "asr_listen": {
        "zh-CN": "这是用来听 VRChat 里其他玩家声音的。新手保持“跟随麦克风”即可；如果你想单独用在线听写服务，也可以在这里选。",
        "en": "Used for reverse translation of other players' VRChat audio. It can follow the main ASR or use a separate online model.",
        "ja": "VRChat 内の他プレイヤー音声を聞く逆翻訳用です。メイン ASR に追従、または別モデルを指定できます。",
    },
    "vrc_listen_device": {
        "zh-CN": "选择 VRChat 声音输出设备。找不到时请确认系统播放设备或 VRChat 音频输出。",
        "en": "Select the device that plays VRChat audio. If missing, check system playback and VRChat output settings.",
        "ja": "VRChat 音声が再生されるデバイスを選びます。見つからない場合は OS と VRChat の出力設定を確認してください。",
    },
    "tts_engine": {
        "zh-CN": "选择 Mio 用哪个声音来朗读。MiMo/Qwen 需要 API Key 和服务区域；VOICEVOX、AivisSpeech 或自定义音色需要先启动本地程序或准备音色文件。",
        "en": "Select the interpretation voice engine. MiMo/Qwen need an API key and service region; VOICEVOX/AivisSpeech/custom voices need local services or models.",
        "ja": "同時通訳の読み上げエンジンです。MiMo/Qwen は API Key とサービス地域、VOICEVOX/AivisSpeech/カスタム音声はローカルサービスやモデルが必要です。",
    },
    "tts_device": {
        "zh-CN": "默认用 CPU，兼容大多数电脑。自定义音色如果想用 NVIDIA 显卡加速，建议 RTX 4060 或更高、8GB 显存以上。6GB 显存也能试，但同时开 VRChat 和麦克风听写时可能不稳。",
        "en": "CPU is the default for lower-end PCs. Style-Bert-VITS2 GPU support is recommended for an NVIDIA RTX 4060 / laptop 4060 or better with at least 8 GB VRAM. 6 GB VRAM can try it, but VRChat and local ASR may make it unstable.",
        "ja": "既定は低スペック環境向けの CPU です。Style-Bert-VITS2 の GPUサポートは NVIDIA RTX 4060 / Laptop 4060 以上、VRAM 8GB 以上がおすすめです。VRAM 6GB でも試せますが、VRChat やローカル ASR と同時実行すると不安定になる場合があります。",
    },
    "tts_bert_language": {
        "zh-CN": "自定义音色需要对应语言的辅助文件。缺少时可以在这里下载。",
        "en": "Style-Bert-VITS2 needs a BERT model for the selected language. Download it here when missing.",
        "ja": "Style-Bert-VITS2 は選択言語に対応する BERT モデルが必要です。未導入ならここでダウンロードできます。",
    },
    "tts_output_vrchat": {
        "zh-CN": "把 Mio 朗读的声音送进 VRChat 麦克风，让房间里的其他人也能听到。通常需要 MixLine 虚拟麦克风。",
        "en": "Routes interpretation voice into the VRChat microphone chain. Usually requires the MixLine virtual device.",
        "ja": "同時通訳音声を VRChat のマイク経路へ送ります。通常は MixLine 仮想デバイスが必要です。",
    },
}

FIELD_HINTS.update({
    "chatbox_template": {
        "zh-CN": "可选：{translatedText}、{translatedText2}、{text}。留空时使用输出格式。",
        "en": "Optional: {translatedText}, {translatedText2}, {text}. Leave empty to use Output Format.",
        "ja": "Optional: {translatedText}, {translatedText2}, {text}. 空欄なら出力形式を使います。",
        "ru": "Optional: {translatedText}, {translatedText2}, {text}. Leave empty to use Output Format.",
        "ko": "Optional: {translatedText}, {translatedText2}, {text}. 비워 두면 출력 형식을 사용합니다.",
    },
    "fallback_backends": {
        "zh-CN": "可选，用英文逗号分隔备用服务代号；主翻译服务失败时才会尝试。",
        "en": "Optional comma-separated backend ids. They are tried only after the primary backend fails.",
        "ja": "Optional comma-separated backend ids. They are tried only after the primary backend fails.",
        "ru": "Optional comma-separated backend ids. They are tried only after the primary backend fails.",
        "ko": "Optional comma-separated backend ids. They are tried only after the primary backend fails.",
    },
    "target_language_3": {
        "zh-CN": "只有“自定义聊天框内容”里写了 {translatedText3} 时，才会翻译第三种语言。",
        "en": "Translated only when the Chatbox Template contains {translatedText3}.",
        "ja": "Translated only when the Chatbox Template contains {translatedText3}.",
        "ru": "Translated only when the Chatbox Template contains {translatedText3}.",
        "ko": "Chatbox 템플릿에 {translatedText3}가 있을 때만 번역합니다.",
    },
    "qwen_translation_region": {
        "zh-CN": "大陆玩家选中国大陆，海外玩家选国际站；只有在你有自己的转发地址或兼容服务地址时，才选自定义。",
        "en": "Mainland players should use Mainland China; overseas players should use International. Custom is for proxies, gateways, or compatible services.",
        "ja": "中国本土のプレイヤーは中国本土、海外では国際版を選びます。カスタムはプロキシや互換サービス向けです。",
        "ru": "В Китае используйте материковый регион; за рубежом - международный. Custom подходит для прокси, шлюзов и совместимых сервисов.",
        "ko": "중국 본토 사용자는 중국 본토를, 해외 사용자는 국제 서비스를 선택하세요. 사용자 지정은 프록시, 게이트웨이, 호환 서비스에 적합합니다.",
    },
    "deepseek_translation_region": {
        "zh-CN": "DeepSeek 官方目前没有大陆/海外分站。普通玩家选官方；只有使用第三方转发地址或兼容账号时，才选自定义并填写对方给你的服务地址。",
        "en": "DeepSeek does not currently expose separate mainland/overseas official endpoints. Use Official for DeepSeek keys; choose Custom for proxy, relay, or third-party compatible keys.",
        "ja": "DeepSeek 公式には現在、中国本土/海外の別エンドポイントはありません。DeepSeek のキーは公式、proxy・relay・第三者互換キーはカスタムを選んでください。",
        "ru": "DeepSeek currently has no separate mainland/overseas official endpoint. Use Official for DeepSeek keys; use Custom for proxy, relay, or third-party compatible keys.",
        "ko": "DeepSeek 공식은 현재 중국 본토/해외 별도 엔드포인트를 제공하지 않습니다. DeepSeek 키는 공식, 프록시/중계/타사 호환 키는 사용자 지정을 선택하세요.",
    },
    "xiaomi_translation_region": {
        "zh-CN": "按量付费通常使用 sk-xxxxx API Key；Token Plan 集群通常使用 tp-xxxxx API Key。区域和 Key 类型要匹配。",
        "en": "Pay-as-you-go usually uses sk-xxxxx keys; Token Plan clusters usually use tp-xxxxx keys. Match the region to the key type.",
        "ja": "従量課金は通常 sk-xxxxx、Token Plan クラスターは通常 tp-xxxxx の API Key を使います。Key 種別に合う地域を選んでください。",
        "ru": "Pay-as-you-go обычно использует ключи sk-xxxxx, а Token Plan - tp-xxxxx. Регион должен соответствовать типу ключа.",
        "ko": "종량제는 보통 sk-xxxxx 키를, Token Plan 클러스터는 보통 tp-xxxxx 키를 사용합니다. 키 유형과 지역을 맞춰 주세요.",
    },
    "nvidia_translation_region": {
        "zh-CN": "普通玩家选 NVIDIA 官方在线服务；如果你用的是自己搭建、公司提供或第三方给的 NVIDIA 兼容地址，再选自定义。",
        "en": "Use the hosted NVIDIA API Catalog endpoint, or choose Custom for self-hosted NIM, proxies, or enterprise gateways.",
        "ja": "ホスト API は NVIDIA API Catalog のエンドポイントです。自前の NIM、プロキシ、企業ゲートウェイはカスタムを選びます。",
        "ru": "Hosted API использует NVIDIA API Catalog. Для собственного NIM, proxy или корпоративного шлюза выберите Custom.",
        "ko": "호스팅 API는 NVIDIA API Catalog 주소를 사용합니다. 자체 NIM, 프록시, 엔터프라이즈 게이트웨이는 사용자 지정을 선택하세요.",
    },
})

FIELD_HINTS["asr_backend"].update({
    "ru": "Определяет, как речь с микрофона превращается в текст. Для китайского/кантонского используйте SenseVoice, для иностранных языков - Whisper; без локальной модели выберите Qwen3-ASR или Gemini.",
    "ko": "마이크 음성을 텍스트로 변환하는 방식을 정합니다. 중국어/광둥어는 SenseVoice, 외국어 듣기는 Whisper를 권장하며, 로컬 모델을 원하지 않으면 Qwen3-ASR 또는 Gemini를 선택하세요.",
})
FIELD_HINTS["asr_device"].update({
    "ru": "По умолчанию CPU для слабых ПК. Локальные SenseVoice/Whisper могут использовать GPU support; рекомендуется NVIDIA RTX 4060 / laptop 4060 или лучше и минимум 8 GB VRAM.",
    "ko": "기본값은 저사양 PC에 맞춘 CPU입니다. 로컬 SenseVoice/Whisper는 GPU 지원을 사용할 수 있으며, NVIDIA RTX 4060 / 노트북 4060 이상과 VRAM 8GB 이상을 권장합니다.",
})
FIELD_HINTS["translation_provider"].update({
    "ru": "Переводит распознанный текст в целевой язык. Онлайн-бэкендам Qwen/GPT нужен API Key.",
    "ko": "인식된 텍스트를 대상 언어로 번역합니다. Qwen/GPT 같은 온라인 백엔드는 API Key가 필요합니다.",
})
FIELD_HINTS["output_format"].update({
    "ru": "Определяет, что отправляется в VRChat Chatbox: только перевод, оригинал с переводом и т. п.",
    "ko": "VRChat Chatbox로 보낼 내용을 정합니다. 번역문만 보내거나 원문을 함께 보낼 수 있습니다.",
})
FIELD_HINTS["tts_output_vrchat"].update({
    "ru": "Направляет озвучивание в микрофонную цепочку VRChat. Обычно требуется виртуальное устройство MixLine.",
    "ko": "동시통역 음성을 VRChat 마이크 경로로 보냅니다. 보통 MixLine 가상 장치가 필요합니다.",
})
_complete_localized_table(FIELD_HINTS)


def _normalize_theme_preference(theme: object) -> str:
    return normalize_theme_preference(theme)


def _resolve_theme(theme_preference: object) -> str:
    return resolve_theme(theme_preference)


class CapsuleSwitch(QCheckBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors = {
            "accent": "#0a84ff",
            "track": "#d7dde7",
            "border": "#c8d0dc",
            "thumb": "#ffffff",
            "thumb_shadow": "#7d8796",
        }
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(56, 30)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._progress = 1.0 if self.isChecked() else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(170)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_progress)
        self.toggled.connect(self._animate_to_state)

    def set_colors(self, *, accent: str, track: str, border: str, thumb: str = "#ffffff") -> None:
        self._colors.update({"accent": accent, "track": track, "border": border, "thumb": thumb})
        self.update()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def sync_progress_to_state(self) -> None:
        self._animation.stop()
        self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def _set_progress(self, value: object) -> None:
        try:
            self._progress = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def _animate_to_state(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(float(self._progress))
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    @staticmethod
    def _blend_color(left: QColor, right: QColor, progress: float) -> QColor:
        p = max(0.0, min(1.0, progress))
        return QColor(
            round(left.red() + (right.red() - left.red()) * p),
            round(left.green() + (right.green() - left.green()) * p),
            round(left.blue() + (right.blue() - left.blue()) * p),
            round(left.alpha() + (right.alpha() - left.alpha()) * p),
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 3, -1, -3)
        progress = float(self._progress)
        enabled = self.isEnabled()
        track_color = self._blend_color(QColor(self._colors["track"]), QColor(self._colors["accent"]), progress)
        border_color = self._blend_color(QColor(self._colors["border"]), QColor(self._colors["accent"]), progress)
        if not enabled:
            track_color.setAlpha(120)
            border_color.setAlpha(110)

        painter.setPen(border_color)
        painter.setBrush(track_color)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        margin = 3
        diameter = rect.height() - margin * 2
        left_x = rect.left() + margin
        right_x = rect.right() - margin - diameter
        x = round(left_x + (right_x - left_x) * progress)
        thumb_rect = rect
        thumb_rect.setLeft(x)
        thumb_rect.setWidth(diameter)
        thumb_rect.setTop(rect.top() + margin)
        thumb_rect.setHeight(diameter)

        shadow = QColor(self._colors["thumb_shadow"])
        shadow.setAlpha(42 if enabled else 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(thumb_rect.translated(0, 1))
        painter.setBrush(QColor(self._colors["thumb"]))
        painter.drawEllipse(thumb_rect)


class SettingsBackgroundWidget(QWidget):
    def __init__(self, background_path: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._theme = "dark"
        self.setAutoFillBackground(False)
        self.set_background_path(background_path)

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def set_background_path(self, background_path: str) -> None:
        path = Path(background_path).expanduser() if background_path else None
        if path and path.is_file():
            self._pixmap = QPixmap(str(path))
        else:
            self._pixmap = QPixmap()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        tokens = theme_tokens(self._theme)
        painter.fillRect(rect, QColor(str(tokens["APP_BG"])))
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            overlay = QColor(5, 7, 12, 124) if self._theme == "dark" else QColor(247, 250, 255, 64)
            painter.fillRect(rect, overlay)
        super().paintEvent(event)


class SettingsWindow(QDialog):
    saved = Signal()
    saving_started = Signal()  # emitted when save begins (before async write)
    bert_refresh_requested = Signal()
    dictionary_update_finished = Signal(dict)
    dictionary_update_failed = Signal(str)
    update_check_available = Signal(object)
    update_check_no_update = Signal()
    update_check_failed = Signal(str)
    tts_test_finished = Signal(int, bool, str)
    ui_callback_requested = Signal()

    def __init__(
        self,
        parent: QWidget,
        config: dict,
        on_save=None,
        on_close=None,
        preload=False,
        on_listen_state_changed=None,
        on_theme_changed: Callable[[str], None] | None = None,
        on_audio_diagnostics_requested: Callable[[str], None] | None = None,
        on_vad_calibration_requested: Callable[[str], None] | None = None,
        on_mode_wizard_requested: Callable[[], None] | None = None,
        defer_initial_page: bool = False,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_save = on_save
        self._on_close = on_close
        self._on_listen_state_changed = on_listen_state_changed
        self._on_theme_changed_callback = on_theme_changed
        self._on_audio_diagnostics_requested = on_audio_diagnostics_requested
        self._on_vad_calibration_requested = on_vad_calibration_requested
        self._on_mode_wizard_requested = on_mode_wizard_requested
        self._preloaded = preload
        self._defer_initial_page = bool(defer_initial_page)

        self._ui_lang = get_ui_language(config)
        self.setWindowTitle(tr(self._ui_lang, "settings_window_title"))
        self.resize(SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT)
        self.setMinimumSize(980, 620)

        self._nav_list: QListWidget | None = None
        self._page_stack: QStackedWidget | None = None
        self._pages: dict[str, QWidget] = {}
        self._built_pages: set[str] = set()
        self._page_prebuild_queue: list[str] = []
        self._page_fade_animation: QPropertyAnimation | None = None
        self._page_effect_widget: QWidget | None = None
        self._theme_fade_animation: QPropertyAnimation | None = None
        self._theme_effect_widget: QWidget | None = None
        self._header_title_label: QLabel | None = None
        self._header_subtitle_label: QLabel | None = None
        self._nav_title_label: QLabel | None = None
        self._cancel_btn: QPushButton | None = None
        self._save_btn: QPushButton | None = None
        self._theme_btn: QPushButton | None = None
        self._check_update_btn: QPushButton | None = None
        self._check_update_buttons: list[QPushButton] = []
        self._update_checking = False
        self._update_win = None
        self._background_widget: SettingsBackgroundWidget | None = None
        self._active_theme = "dark"

        # Field value holders
        self._ui_lang_var = _StrVar()
        self._theme_var = _StrVar()
        self._backend_var = _StrVar()
        self._backend_api_key_var = _StrVar()
        self._backend_base_url_var = _StrVar()
        self._backend_model_var = _StrVar()
        self._backend_timeout_var = _StrVar()
        self._backend_retries_var = _StrVar()
        self._fallback_backends_var = _StrVar()
        self._qwen_translation_region_var = _StrVar()
        self._src_lang_var = _StrVar()
        self._target_lang_var = _StrVar()
        self._target_lang2_var = _StrVar()
        self._target_lang3_var = _StrVar()
        self._asr_engine_var = _StrVar()
        self._asr_device_var = _StrVar()
        self._qwen_api_key_var = _StrVar()
        self._qwen_region_var = _StrVar()
        self._qwen_base_url_var = _StrVar()
        self._qwen_model_var = _StrVar()
        self._gemini_api_key_var = _StrVar()
        self._gemini_model_var = _StrVar()
        self._input_device_mode_var = _StrVar()
        self._input_device_var = _StrVar()
        self._vad_var = _StrVar()
        self._chunk_interval_var = _StrVar()
        self._chunk_window_var = _StrVar()
        self._partial_hits_var = _StrVar()
        self._denoise_var = _StrVar()
        self._vad_sensitivity_var = _StrVar()
        self._vad_speech_ratio_var = _StrVar()
        self._vad_activation_threshold_var = _StrVar()
        self._vad_min_rms_var = _StrVar()
        self._min_segment_var = _StrVar()
        self._max_segment_var = _StrVar()
        self._partial_min_speech_var = _StrVar()
        self._loopback_device_var = _StrVar()
        self._listen_asr_engine_var = _StrVar()
        self._listen_self_suppress_var = _BoolVar(False)
        self._listen_self_suppress_seconds_var = _StrVar()
        self._listen_segment_duration_var = _StrVar()
        self._listen_tail_silence_var = _StrVar()
        self._listen_vad_min_rms_var = _StrVar()
        self._tts_enabled_var = _BoolVar(False)
        self._tts_engine_var = _StrVar()
        self._tts_voice_var = _StrVar()
        self._tts_api_key_var = _StrVar()
        self._tts_api_region_var = _StrVar()
        self._tts_api_base_url_var = _StrVar()
        self._tts_api_model_var = _StrVar()
        self._tts_output_to_vrchat_var = _BoolVar(False)
        self._tts_auto_read_var = _BoolVar(True)
        self._tts_monitor_var = _BoolVar(False)
        self._tts_device_var = _StrVar()
        self._tts_bert_language_var = _StrVar()
        self._tts_rate_var = _FloatVar(1.0)
        self._tts_volume_var = _FloatVar(0.8)
        self._vrc_listen_enabled_var = _BoolVar(False)
        self._vrc_listen_overlay_var = _BoolVar(False)
        self._vrc_listen_send_var = _BoolVar(True)
        self._vrc_listen_src_var = _StrVar()
        self._vrc_listen_tgt_var = _StrVar()
        self._mic_send_to_chatbox_var = _BoolVar(True)
        self._text_input_hotkey_var = _StrVar()
        self._mic_mute_hotkey_var = _StrVar()
        self._output_format_var = _StrVar()
        self._chatbox_template_var = _StrVar()
        self._roleplay_enabled_var = _BoolVar(False)
        self._roleplay_preset_var = _StrVar()
        self._persona_name_var = _StrVar()
        self._roleplay_prompt_var = _StrVar()
        self._roleplay_glossary_var = _StrVar()
        self._avatar_sync_enabled_var = _BoolVar(False)
        self._osc_listener_enabled_var = _BoolVar(False)
        self._osc_receive_host_var = _StrVar()
        self._osc_receive_port_var = _StrVar()
        self._osc_sync_mute_self_var = _BoolVar(True)
        self._osc_allow_avatar_control_var = _BoolVar(False)
        self._osc_control_prefix_var = _StrVar()
        self._osc_toggle_mic_var = _StrVar()
        self._osc_toggle_listen_var = _StrVar()
        self._osc_toggle_tts_var = _StrVar()
        self._osc_toggle_overlay_var = _StrVar()
        self._avatar_translating_var = _StrVar()
        self._avatar_speaking_var = _StrVar()
        self._avatar_muted_var = _StrVar()
        self._avatar_error_var = _StrVar()
        self._avatar_target_language_var = _StrVar()
        self._dictionary_custom_replacement_var = _StrVar()
        self._dictionary_custom_patterns_var = _StrVar()

        self._lang_codes: dict[str, str] = {}
        self._lang3_codes: dict[str, str] = {}
        self._src_codes: dict[str, str] = {}
        self._listen_src_codes: dict[str, str] = {}
        self._listen_lang_codes: dict[str, str] = {}
        self._backend_codes: dict[str, str] = {}
        self._asr_codes: dict[str, str] = {}
        self._asr_device_codes: dict[str, str] = {}
        self._listen_asr_engine_codes: dict[str, str] = {}
        self._ui_lang_codes: dict[str, str] = {}
        self._fmt_codes: dict[str, str] = {}
        self._input_mode_codes: dict[str, str] = {}
        self._denoise_codes: dict[str, float] = {}
        self._qwen_translation_region_codes: dict[str, str] = {}
        self._qwen_region_codes: dict[str, str] = {}
        self._qwen_model_codes: dict[str, str] = {}
        self._tts_engine_codes: dict[str, str] = {}
        self._tts_device_codes: dict[str, str] = {}
        self._tts_bert_language_codes: dict[str, str] = {}
        self._tts_api_region_codes: dict[str, str] = {}
        self._roleplay_preset_codes: dict[str, str] = {}

        self._tts_test_manager: TTSManager | None = None
        self._tts_testing = False
        self._tts_test_btn: QPushButton | None = None
        self._tts_stop_btn: QPushButton | None = None
        self._tts_test_generation = 0
        self._tts_test_timeout_ms = TTS_TEST_TIMEOUT_MS
        self._active_tts_test_timeout_ms = TTS_TEST_TIMEOUT_MS
        self._tts_test_timeout_timer = QTimer(self)
        self._tts_test_timeout_timer.setSingleShot(True)
        self._tts_test_timeout_timer.timeout.connect(self._on_tts_test_timeout)
        self._tts_voices_loaded: dict[str, list] = {}
        self._tts_voices_loading = False
        self._tts_voices_loading_engine: str | None = None
        self._tts_voice_load_generation = 0
        self._tts_voice_display_to_id: dict[str, str] = {}
        self._local_tts_availability: dict[str, bool] = {}
        self._local_tts_checking: set[str] = set()
        self._tts_runtime_button_action: Callable[[], None] | None = None
        self._background_image_path = ""
        self._tts_virtual_device_id: int | None = None
        self._tts_virtual_device_name: str | None = None
        self._tts_api_frame: QFrame | None = None
        self._tts_api_key_entry: QLineEdit | None = None
        self._tts_api_region_combo: QComboBox | None = None
        self._tts_api_base_url_entry: QLineEdit | None = None
        self._tts_api_model_entry: QLineEdit | None = None
        self._tts_device_combo: QComboBox | None = None
        self._asr_device_combo: QComboBox | None = None
        self._dictionary_status_label: QLabel | None = None
        self._dictionary_update_button: QPushButton | None = None
        self._dictionary_custom_patterns_edit: QTextEdit | None = None
        self._dictionary_custom_save_button: QPushButton | None = None
        self._dictionary_custom_status_label: QLabel | None = None
        self._dictionary_updating = False
        self._sbv2_options_frame: QFrame | None = None
        self._asr_model_action_frame: QFrame | None = None
        self._asr_model_action_label: QLabel | None = None
        self._asr_model_download_btn: QPushButton | None = None
        self._asr_model_download_engine = DEFAULT_ASR_ENGINE
        self._bert_download_window: QDialog | None = None
        self._pytorch_cuda_install_dialog: QDialog | None = None
        self._backend_model_info_frame: QFrame | None = None
        self._backend_model_info_title_label: QLabel | None = None
        self._backend_model_info_note_label: QLabel | None = None
        self._backend_model_badge_labels: dict[str, QLabel] = {}
        self._backend_base_url_entry: QLineEdit | None = None
        self._qwen_model_hint_label: QLabel | None = None
        self._missing_model_prompted = False
        self._save_thread: QThread | None = None
        self._closing = False
        self._saving = False
        self._pending_save_rollback_config: dict | None = None
        self._ui_thread_id = threading.get_ident()
        self._ui_callback_queue: queue.Queue[tuple[int, object]] = queue.Queue()
        self._applied_settings_stylesheet = ""
        self._applied_settings_theme = ""
        self._applied_background_path = self._background_image_path

        self._init_from_config()
        self._fmt_codes = {l: c for l, c in get_output_format_options(self._ui_lang)}
        self._build_ui()
        self.bert_refresh_requested.connect(self._refresh_bert_model_prompt)
        self.dictionary_update_finished.connect(self._on_dictionary_update_finished)
        self.dictionary_update_failed.connect(self._on_dictionary_update_failed)
        self.update_check_available.connect(self._on_update_check_available)
        self.update_check_no_update.connect(self._on_update_check_no_update)
        self.update_check_failed.connect(self._on_update_check_failed)
        self.tts_test_finished.connect(self._finish_tts_test)
        self.ui_callback_requested.connect(self._drain_ui_callback_queue)
        if self._preloaded:
            QTimer.singleShot(120, self._prebuild_next_page)
        else:
            QTimer.singleShot(150, self._load_tts_voices_deferred)
        if not self._preloaded:
            QTimer.singleShot(300, self._maybe_prompt_missing_model_download)

    @staticmethod
    def _is_child_of(widget: QWidget | None, ancestor: QWidget | None) -> bool:
        current = widget
        while current is not None and ancestor is not None:
            if current is ancestor:
                return True
            current = current.parentWidget()
        return False

    def _rebuild_page_if_built(self, page_id: str) -> None:
        if self._page_stack is None or page_id not in self._built_pages:
            return
        index = next((i for i, item in enumerate(NAV_ITEMS) if item[0] == page_id), -1)
        if index < 0:
            return
        current_index = self._page_stack.currentIndex()
        old_page = self._page_stack.widget(index)
        self._check_update_buttons = [
            button
            for button in self._check_update_buttons
            if button is not None
            and (button is self._check_update_btn or not self._is_child_of(button, old_page))
        ]
        page = self._build_page(page_id)
        self._pages[page_id] = page
        self._page_stack.removeWidget(old_page)
        old_page.deleteLater()
        self._page_stack.insertWidget(index, page)
        if 0 <= current_index < self._page_stack.count():
            self._page_stack.setCurrentIndex(current_index)
        self._apply_style()

    def _refresh_model_download_pages(self) -> None:
        self._refresh_asr_model_action_card()
        self._rebuild_page_if_built("model")

    def _init_from_config(self) -> None:
        cfg = self._config
        ui_cfg = cfg.get("ui", {}) if isinstance(cfg.get("ui", {}), dict) else {}
        trans_cfg = cfg.get("translation", {}) if isinstance(cfg.get("translation", {}), dict) else {}
        asr_cfg = cfg.get("asr", {}) if isinstance(cfg.get("asr", {}), dict) else {}
        audio_cfg = cfg.get("audio", {}) if isinstance(cfg.get("audio", {}), dict) else {}
        streaming_cfg = asr_cfg.get("streaming", {}) if isinstance(asr_cfg.get("streaming", {}), dict) else {}
        tts_cfg = cfg.get("tts", {}) if isinstance(cfg.get("tts", {}), dict) else {}
        vrc_cfg = cfg.get("vrc_listen", {}) if isinstance(cfg.get("vrc_listen", {}), dict) else {}
        hotkey_cfg = cfg.get("hotkeys", {}) if isinstance(cfg.get("hotkeys", {}), dict) else {}
        text_input_cfg = cfg.get("text_input_window", {}) if isinstance(cfg.get("text_input_window", {}), dict) else {}
        social_cfg = trans_cfg.get("social", {}) if isinstance(trans_cfg.get("social", {}), dict) else {}
        osc_cfg = cfg.get("osc", {}) if isinstance(cfg.get("osc", {}), dict) else {}
        avatar_cfg = osc_cfg.get("avatar_sync", {}) if isinstance(osc_cfg.get("avatar_sync", {}), dict) else {}
        avatar_params = avatar_cfg.get("params", {}) if isinstance(avatar_cfg.get("params", {}), dict) else {}

        self._ui_lang = get_ui_language(cfg)
        theme_labels = self._theme_labels()
        theme = _normalize_theme_preference(ui_cfg.get(MAIN_THEME_CONFIG_KEY, "system"))
        self._theme_var.set(theme_labels.get(theme, theme_labels["system"]))
        self._background_image_path = str(ui_cfg.get("background_image_path") or "")

        ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_var.set(ui_lang_reverse.get(self._ui_lang, UI_LANGUAGE_OPTIONS[0][0]))
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}

        target_opts = get_target_language_options(ui_language=self._ui_lang)
        self._lang_codes = {label: code for label, code in target_opts}
        tgt_code = str(trans_cfg.get("target_language", "ja"))
        self._target_lang_var.set(next((l for l, c in target_opts if c == tgt_code), target_opts[0][0]))
        tgt2_code = str(trans_cfg.get("target_language_2", "en"))
        self._target_lang2_var.set(next((l for l, c in target_opts if c == tgt2_code), target_opts[0][0]))
        target3_label = self._copy("target_language_disabled")
        target3_opts = [(target3_label, ""), *target_opts]
        self._lang3_codes = {label: code for label, code in target3_opts}
        tgt3_code = str(trans_cfg.get("target_language_3", "") or "")
        self._target_lang3_var.set(next((l for l, c in target3_opts if c == tgt3_code), target3_opts[0][0]))

        src_opts = get_manual_source_language_options(ui_language=self._ui_lang)
        self._src_codes = {label: code for label, code in src_opts}
        src_code = str(trans_cfg.get("source_language", "auto"))
        self._src_lang_var.set(next((l for l, c in src_opts if c == src_code), src_opts[0][0]))

        self._output_format_var.set(self._format_label_from_code(normalize_output_format(trans_cfg.get("output_format"))))
        self._mic_send_to_chatbox_var.set(bool(trans_cfg.get("send_to_chatbox", True)))
        self._chatbox_template_var.set(str(trans_cfg.get("chatbox_template", "") or ""))
        fallback_backends = trans_cfg.get("fallback_backends", [])
        if isinstance(fallback_backends, str):
            fallback_text = fallback_backends
        elif isinstance(fallback_backends, (list, tuple)):
            fallback_text = ", ".join(str(item).strip() for item in fallback_backends if str(item).strip())
        else:
            fallback_text = ""
        self._fallback_backends_var.set(fallback_text)

        backend = normalize_backend(trans_cfg.get("backend", "openai"))
        self._backend_var.set(get_backend_label(backend))
        self._backend_codes = {get_backend_label(b): b for b in get_backend_order()}
        self._set_backend_field_vars(backend)

        asr_opts = self._asr_engine_options()
        self._asr_codes = {label: code for label, code in asr_opts}
        asr_code = str(asr_cfg.get("engine", DEFAULT_ASR_ENGINE))
        asr_label = next((l for l, c in asr_opts if c == asr_code), asr_opts[0][0])
        self._asr_engine_var.set(asr_label)
        self._init_asr_device_vars(asr_cfg)
        self._init_asr_provider_vars(asr_cfg)

        mode_auto = self._copy("input_device_mode_auto")
        mode_fixed = self._copy("input_device_mode_fixed")
        self._input_mode_codes = {mode_auto: "auto", mode_fixed: "fixed"}
        input_mode = str(audio_cfg.get("input_device_mode", "") or "").strip().lower()
        if input_mode not in {"auto", "fixed"}:
            input_mode = "fixed" if str(audio_cfg.get("input_device") or "").strip() else "auto"
        self._input_device_mode_var.set(mode_fixed if input_mode == "fixed" else mode_auto)
        self._input_device_var.set(str(audio_cfg.get("input_device") or ""))

        self._vad_var.set(str(audio_cfg.get("vad_silence_threshold", 0.65)))
        self._chunk_interval_var.set(str(streaming_cfg.get("chunk_interval_ms", 250)))
        self._chunk_window_var.set(str(streaming_cfg.get("chunk_window_s", 1.6)))
        self._partial_hits_var.set(str(streaming_cfg.get("partial_stability_hits", 2)))
        self._vad_sensitivity_var.set(str(audio_cfg.get("vad_sensitivity", 2)))
        self._vad_speech_ratio_var.set(str(audio_cfg.get("vad_speech_ratio", 0.6)))
        self._vad_activation_threshold_var.set(str(audio_cfg.get("vad_activation_threshold_s", 0.2)))
        self._vad_min_rms_var.set(str(audio_cfg.get("vad_min_rms", 0.012)))
        self._min_segment_var.set(str(audio_cfg.get("min_segment_s", 0.45)))
        self._max_segment_var.set(str(audio_cfg.get("max_segment_s", 6.0)))
        self._partial_min_speech_var.set(str(audio_cfg.get("partial_min_speech_s", 0.45)))
        self._init_denoise_var(audio_cfg)

        tts_engine = str(tts_cfg.get("engine", "edge")).strip() or "edge"
        if tts_engine not in TTS_ENGINE_IDS:
            tts_engine = "edge"
        self._tts_engine_codes = {_tts_engine_label(engine, self._ui_lang): engine for engine in TTS_ENGINE_IDS}
        self._tts_engine_var.set(_tts_engine_label(tts_engine, self._ui_lang))
        engine_cfg = tts_cfg.get(tts_engine, {}) if isinstance(tts_cfg.get(tts_engine, {}), dict) else {}
        self._tts_enabled_var.set(bool(tts_cfg.get("enabled", False)))
        self._tts_auto_read_var.set(bool(tts_cfg.get("auto_read", True)))
        self._tts_monitor_var.set(bool(tts_cfg.get("monitor_enabled", False)))
        self._tts_output_to_vrchat_var.set(bool(tts_cfg.get("output_to_vrchat", tts_cfg.get("output_device") not in (None, -1))))
        self._tts_voice_var.set(str(engine_cfg.get("voice") or ""))
        tts_rate = float(engine_cfg.get("rate", tts_cfg.get("rate", 1.0)) or 1.0)
        self._tts_rate_var.set(tts_rate)
        tts_vol = float(engine_cfg.get("volume", tts_cfg.get("volume", 0.8)) or 0.8)
        self._tts_volume_var.set(tts_vol)
        self._init_tts_device_vars(tts_cfg)
        self._init_tts_api_vars(tts_cfg, tts_engine)

        self._vrc_listen_enabled_var.set(bool(vrc_cfg.get("enabled", False)))
        self._vrc_listen_overlay_var.set(bool(vrc_cfg.get("show_overlay", False)))
        self._vrc_listen_send_var.set(bool(vrc_cfg.get("send_to_chatbox", True)))
        self._listen_self_suppress_var.set(bool(vrc_cfg.get("self_suppress", False)))
        self._listen_self_suppress_seconds_var.set(str(vrc_cfg.get("self_suppress_seconds", 0.65)))
        self._listen_segment_duration_var.set(str(vrc_cfg.get("segment_duration_s", 2.0)))
        self._listen_tail_silence_var.set(str(vrc_cfg.get("tail_silence_s", 0.65)))
        self._listen_vad_min_rms_var.set(str(vrc_cfg.get("vad_min_rms", 0.02)))
        listen_src = str(vrc_cfg.get("source_language", "auto"))
        listen_tgt = str(vrc_cfg.get("target_language", "zh"))
        self._listen_src_codes = {label: code for label, code in src_opts}
        self._listen_lang_codes = {label: code for label, code in target_opts}
        self._vrc_listen_src_var.set(next((l for l, c in src_opts if c == listen_src), src_opts[0][0]))
        self._vrc_listen_tgt_var.set(next((l for l, c in target_opts if c == listen_tgt), target_opts[0][0]))
        self._loopback_device_var.set(str(vrc_cfg.get("loopback_device") or ""))
        listen_asr_opts = self._listen_asr_engine_options()
        self._listen_asr_engine_codes = {label: code for label, code in listen_asr_opts}
        listen_asr_code = str(vrc_cfg.get("asr_engine", ASR_ENGINE_FOLLOW_MAIN) or ASR_ENGINE_FOLLOW_MAIN)
        self._listen_asr_engine_var.set(next((l for l, c in listen_asr_opts if c == listen_asr_code), listen_asr_opts[0][0]))

        from src.utils.global_hotkey import DEFAULT_MIC_MUTE_HOTKEY, DEFAULT_TEXT_INPUT_HOTKEY
        self._text_input_hotkey_var.set(str(text_input_cfg.get("hotkey", DEFAULT_TEXT_INPUT_HOTKEY) or ""))
        self._mic_mute_hotkey_var.set(str(hotkey_cfg.get("mic_mute", DEFAULT_MIC_MUTE_HOTKEY) or ""))
        self._roleplay_enabled_var.set(str(social_cfg.get("mode", "standard")) == "roleplay")
        preset = str(social_cfg.get("persona_preset", "custom") or "custom")
        if preset not in ROLEPLAY_PRESETS:
            preset = "custom"
        roleplay_preset_options = _roleplay_preset_options(self._ui_lang)
        self._roleplay_preset_codes = {label: code for label, code in roleplay_preset_options}
        self._roleplay_preset_reverse = {code: label for label, code in roleplay_preset_options}
        default_preset_label = self._roleplay_preset_reverse.get(preset, roleplay_preset_options[0][0])
        self._roleplay_preset_var = _StrVar(default_preset_label)
        self._persona_name_var = _StrVar(str(social_cfg.get("persona_name", "") or ROLEPLAY_PRESETS[preset].get("persona_name", "")))
        self._roleplay_prompt_var.set(str(social_cfg.get("persona_prompt", "") or ""))
        self._roleplay_glossary_var.set(str(social_cfg.get("persona_glossary", "") or ""))

        control_params = osc_cfg.get("control_params", {}) if isinstance(osc_cfg.get("control_params", {}), dict) else {}
        prefix = str(osc_cfg.get("control_prefix", "Mio") or "Mio")
        self._osc_listener_enabled_var.set(bool(osc_cfg.get("listener_enabled", False)))
        self._osc_receive_host_var.set(str(osc_cfg.get("receive_host", "127.0.0.1") or "127.0.0.1"))
        self._osc_receive_port_var.set(str(osc_cfg.get("receive_port", 9001) or 9001))
        self._osc_sync_mute_self_var.set(bool(osc_cfg.get("sync_mute_self", True)))
        self._osc_allow_avatar_control_var.set(bool(osc_cfg.get("allow_avatar_control", False)))
        self._osc_control_prefix_var.set(prefix)
        self._osc_toggle_mic_var.set(str(control_params.get("mic", f"{prefix}ToggleMic") or f"{prefix}ToggleMic"))
        self._osc_toggle_listen_var.set(str(control_params.get("listen", f"{prefix}ToggleListen") or f"{prefix}ToggleListen"))
        self._osc_toggle_tts_var.set(str(control_params.get("tts", f"{prefix}ToggleTts") or f"{prefix}ToggleTts"))
        self._osc_toggle_overlay_var.set(str(control_params.get("overlay", f"{prefix}ToggleOverlay") or f"{prefix}ToggleOverlay"))

        self._avatar_sync_enabled_var.set(bool(avatar_cfg.get("enabled", False)))
        self._avatar_translating_var.set(str(avatar_params.get("translating", "MioTranslating") or "MioTranslating"))
        self._avatar_speaking_var.set(str(avatar_params.get("speaking", "MioSpeaking") or "MioSpeaking"))
        self._avatar_muted_var.set(str(avatar_params.get("muted", "MioMuted") or "MioMuted"))
        self._avatar_error_var.set(str(avatar_params.get("error", "MioError") or "MioError"))
        self._avatar_target_language_var.set(str(avatar_params.get("target_language", "MioTargetLanguage") or "MioTargetLanguage"))

    def _format_label_from_code(self, fmt_code: str) -> str:
        options = get_output_format_options(self._ui_lang)
        label = next((l for l, c in options if c == fmt_code), options[0][0])
        return label

    def _copy(self, key: str, **kwargs) -> str:
        table = QT_SETTINGS_COPY.get(key)
        text = ""
        if table:
            text = (
                table.get(self._ui_lang)
                or table.get(self._ui_lang.split("-", 1)[0])
                or table.get("en")
                or table.get("zh-CN")
                or next(iter(table.values()))
            )
        else:
            text = tr(self._ui_lang, key, **kwargs)
            kwargs = {}
        return text.format(**kwargs) if kwargs else text

    def _theme_labels(self) -> dict[str, str]:
        return (
            THEME_LABELS.get(self._ui_lang)
            or THEME_LABELS.get(self._ui_lang.split("-", 1)[0])
            or THEME_LABELS["en"]
        )

    @staticmethod
    def _theme_code_from_value(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"system", "auto", "follow", "follow-system", "default"}:
            return "system"
        if normalized in {"light", "white", "day"}:
            return "light"
        if normalized in {"dark", "black", "night"}:
            return "dark"
        for labels in THEME_LABELS.values():
            if value == labels.get("system"):
                return "system"
            if value == labels.get("light"):
                return "light"
            if value == labels.get("dark"):
                return "dark"
        return "system"

    @staticmethod
    def _label_for_code(options: list[tuple[str, str]] | tuple[tuple[str, str], ...], code: str, fallback: str = "") -> str:
        return next((label for label, option_code in options if option_code == code), options[0][0] if options else fallback)

    def _nav_item_label(self, page_id: str) -> str:
        copy_keys = {
            "common": "appearance_section",
            "voice": "recognition_section",
            "vrc_listen": "vrc_listen_section",
            "translation": "translation_domain_section",
            "tts": "tts_section",
            "vr_integration": "vr_integration_section",
            "roleplay": "roleplay_section",
            "avatar": "vr_integration_section",
            "hotkeys": "hotkey_voice_section",
            "model": "updates_models_section",
            "dictionary": "settings_dictionary",
            "advanced": "advanced_section",
        }
        return self._copy(copy_keys.get(page_id, page_id))

    def _capture_language_option_codes(self) -> dict[str, object]:
        if hasattr(self, "_roleplay_prompt_edit"):
            self._roleplay_prompt_var.set(self._roleplay_prompt_edit.toPlainText())
        if hasattr(self, "_roleplay_glossary_edit"):
            self._roleplay_glossary_var.set(self._roleplay_glossary_edit.toPlainText())
        patterns_edit = getattr(self, "_dictionary_custom_patterns_edit", None)
        if patterns_edit is not None:
            self._dictionary_custom_patterns_var.set(patterns_edit.toPlainText())
        return {
            "theme": self._theme_code_from_value(self._theme_var.value()),
            "target": self._lang_codes.get(self._target_lang_var.value(), "ja"),
            "target_2": self._lang_codes.get(self._target_lang2_var.value(), "en"),
            "target_3": self._lang3_codes.get(self._target_lang3_var.value(), ""),
            "source": self._src_codes.get(self._src_lang_var.value(), "auto"),
            "listen_source": self._listen_src_codes.get(self._vrc_listen_src_var.value(), "auto"),
            "listen_target": self._listen_lang_codes.get(self._vrc_listen_tgt_var.value(), "zh"),
            "output_format": self._fmt_codes.get(self._output_format_var.value(), OUTPUT_FORMAT_OPTIONS[0][1]),
            "input_mode": self._input_mode_codes.get(self._input_device_mode_var.value(), "auto"),
            "denoise": self._denoise_codes.get(self._denoise_var.value(), 0.0),
            "tts_engine": self._selected_tts_engine(),
            "tts_voice": self._selected_tts_voice_id(),
            "tts_device": self._tts_device_codes.get(self._tts_device_var.value(), "cpu"),
            "tts_bert_language": self._tts_bert_language_codes.get(self._tts_bert_language_var.value(), "jp"),
            "roleplay_preset": self._roleplay_preset_codes.get(self._roleplay_preset_var.value(), "custom"),
            "asr_engine": self._asr_codes.get(self._asr_engine_var.value(), DEFAULT_ASR_ENGINE),
            "asr_device": self._asr_device_codes.get(self._asr_device_var.value(), "cpu"),
            "listen_asr_engine": self._listen_asr_engine_codes.get(self._listen_asr_engine_var.value(), ASR_ENGINE_FOLLOW_MAIN),
            "backend": self._backend_code(),
        }

    def _apply_language_option_codes(self, codes: dict[str, object], ui_language: str) -> None:
        self._ui_lang = ui_language
        self._ui_lang_var.set(self._label_for_code(UI_LANGUAGE_OPTIONS, ui_language, UI_LANGUAGE_OPTIONS[0][0]))

        theme_labels = self._theme_labels()
        theme_code = _normalize_theme_preference(codes.get("theme", "system"))
        self._theme_var.set(theme_labels.get(theme_code, theme_labels["system"]))

        target_opts = list(get_target_language_options(ui_language=self._ui_lang))
        target_code = str(codes.get("target", "ja"))
        self._lang_codes = {label: code for label, code in target_opts}
        self._target_lang_var.set(self._label_for_code(target_opts, target_code))
        self._target_lang2_var.set(self._label_for_code(target_opts, str(codes.get("target_2", "en"))))
        target3_opts = [(self._copy("target_language_disabled"), ""), *target_opts]
        self._lang3_codes = {label: code for label, code in target3_opts}
        self._target_lang3_var.set(self._label_for_code(target3_opts, str(codes.get("target_3", "")), ""))

        src_opts = list(get_manual_source_language_options(ui_language=self._ui_lang))
        source_code = str(codes.get("source", "auto"))
        self._src_codes = {label: code for label, code in src_opts}
        self._src_lang_var.set(self._label_for_code(src_opts, source_code))

        self._listen_src_codes = {label: code for label, code in src_opts}
        self._listen_lang_codes = {label: code for label, code in target_opts}
        self._vrc_listen_src_var.set(self._label_for_code(src_opts, str(codes.get("listen_source", source_code))))
        self._vrc_listen_tgt_var.set(self._label_for_code(target_opts, str(codes.get("listen_target", target_code))))
        self._output_format_var.set(self._format_label_from_code(normalize_output_format(codes.get("output_format", "plain"))))

        mode_auto = self._copy("input_device_mode_auto")
        mode_fixed = self._copy("input_device_mode_fixed")
        input_mode = str(codes.get("input_mode", "auto"))
        self._input_mode_codes = {mode_auto: "auto", mode_fixed: "fixed"}
        self._input_device_mode_var.set(mode_fixed if input_mode == "fixed" else mode_auto)

        denoise_labels = DENOISE_LABELS.get(self._ui_lang) or DENOISE_LABELS.get(self._ui_lang.split("-", 1)[0]) or DENOISE_LABELS["en"]
        self._denoise_codes = {denoise_labels[key]: value for key, value in DENOISE_PRESETS}
        denoise_value = float(codes.get("denoise", 0.0) or 0.0)
        denoise_key, _value = min(DENOISE_PRESETS, key=lambda item: abs(item[1] - denoise_value))
        self._denoise_var.set(denoise_labels[denoise_key])

        backend = str(codes.get("backend", normalize_backend(self._config.get("translation", {}).get("backend", "openai"))))
        self._backend_codes = {get_backend_label(b): b for b in get_backend_order()}
        self._backend_var.set(get_backend_label(backend))

        asr_opts = self._asr_engine_options()
        self._asr_codes = {label: code for label, code in asr_opts}
        self._asr_engine_var.set(self._label_for_code(asr_opts, str(codes.get("asr_engine", DEFAULT_ASR_ENGINE))))

        asr_device_options = self._device_options()
        self._asr_device_codes = {label: code for label, code in asr_device_options}
        self._asr_device_var.set(self._label_for_code(asr_device_options, str(codes.get("asr_device", "cpu"))))

        listen_asr_opts = self._listen_asr_engine_options()
        self._listen_asr_engine_codes = {label: code for label, code in listen_asr_opts}
        self._listen_asr_engine_var.set(self._label_for_code(listen_asr_opts, str(codes.get("listen_asr_engine", ASR_ENGINE_FOLLOW_MAIN))))

        tts_engine = str(codes.get("tts_engine", DEFAULT_TTS_ENGINE))
        self._tts_engine_codes = {_tts_engine_label(engine, self._ui_lang): engine for engine in TTS_ENGINE_IDS}
        self._tts_engine_var.set(_tts_engine_label(tts_engine, self._ui_lang))
        self._tts_voice_var.set(str(codes.get("tts_voice", "")))

        device_options = self._tts_device_options()
        self._tts_device_codes = {label: code for label, code in device_options}
        self._tts_device_var.set(self._label_for_code(device_options, str(codes.get("tts_device", "cpu"))))

        bert_options = [
            (tr(self._ui_lang, "tts_bert_language_jp"), "jp"),
            (tr(self._ui_lang, "tts_bert_language_zh"), "zh"),
            (tr(self._ui_lang, "tts_bert_language_en"), "en"),
        ]
        self._tts_bert_language_codes = {label: code for label, code in bert_options}
        self._tts_bert_language_var.set(self._label_for_code(bert_options, str(codes.get("tts_bert_language", "jp"))))

        roleplay_options = _roleplay_preset_options(self._ui_lang)
        self._roleplay_preset_codes = {label: code for label, code in roleplay_options}
        self._roleplay_preset_reverse = {code: label for label, code in roleplay_options}
        self._roleplay_preset_var.set(self._label_for_code(roleplay_options, str(codes.get("roleplay_preset", "custom"))))

    def _refresh_language_widgets(self) -> None:
        self.setWindowTitle(tr(self._ui_lang, "settings_window_title"))
        if self._header_title_label is not None:
            self._header_title_label.setText(self._copy("header_title"))
        if self._header_subtitle_label is not None:
            self._header_subtitle_label.setText(self._copy("header_subtitle"))
        if self._nav_title_label is not None:
            self._nav_title_label.setText(self._copy("header_title"))
        for button in getattr(self, "_check_update_buttons", []):
            if button is None:
                continue
            button.setText(self._copy("settings_update_checking" if self._update_checking else "settings_check_update"))
            self._fit_button_to_text(
                button,
                min_width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
                height=38,
                padding=SETTINGS_UPDATE_BUTTON_PADDING,
            )
        self._refresh_theme_button()
        if self._cancel_btn is not None:
            self._cancel_btn.setText(tr(self._ui_lang, "cancel"))
            self._fit_button_to_text(self._cancel_btn, min_width=96, height=34, padding=30)
        if self._save_btn is not None:
            self._save_btn.setText(tr(self._ui_lang, "save"))
            self._fit_button_to_text(self._save_btn, min_width=96, height=34, padding=30)
        if self._nav_list is not None:
            for row in range(self._nav_list.count()):
                item = self._nav_list.item(row)
                page_id = str(item.data(1))
                item.setText(self._nav_item_label(page_id))
        self._rebuild_pages()
        self._apply_style()

    def _rebuild_pages(self) -> None:
        if self._page_stack is None:
            return
        current_index = self._page_stack.currentIndex()
        current_page_id = NAV_ITEMS[current_index][0] if 0 <= current_index < len(NAV_ITEMS) else "common"
        while self._page_stack.count():
            widget = self._page_stack.widget(0)
            self._page_stack.removeWidget(widget)
            widget.deleteLater()
        self._pages.clear()
        self._built_pages.clear()
        self._page_prebuild_queue.clear()
        self._check_update_buttons = [self._check_update_btn] if self._check_update_btn is not None else []
        for page_id, _label in NAV_ITEMS:
            page = self._build_page(page_id) if page_id == "common" else self._build_placeholder_page()
            if page_id == "common":
                self._built_pages.add(page_id)
            self._pages[page_id] = page
            self._page_stack.addWidget(page)
        self._page_prebuild_queue = [page_id for page_id, _label in NAV_ITEMS if page_id != "common"]
        if self._page_stack.count():
            self._page_stack.setCurrentIndex(max(0, min(current_index, self._page_stack.count() - 1)))
            if current_page_id in self._pages:
                self._ensure_page_built(current_page_id)
                current_row = next((i for i, item in enumerate(NAV_ITEMS) if item[0] == current_page_id), 0)
                self._page_stack.setCurrentIndex(current_row)
        if self._preloaded:
            QTimer.singleShot(80, self._prebuild_next_page)

    def _set_backend_field_vars(self, backend: str) -> None:
        trans_cfg = self._config.get("translation", {})
        if not isinstance(trans_cfg, dict):
            trans_cfg = {}
        self._backend_api_key_var.set(get_backend_config_value(trans_cfg, backend, "api_key"))
        self._backend_base_url_var.set(get_backend_config_value(trans_cfg, backend, "base_url"))
        self._backend_model_var.set(get_backend_config_value(trans_cfg, backend, "model"))
        self._backend_timeout_var.set(get_backend_config_value(trans_cfg, backend, "timeout_s"))
        self._backend_retries_var.set(get_backend_config_value(trans_cfg, backend, "max_retries"))
        if backend_has_service_regions(backend):
            self._set_backend_region_vars(backend, trans_cfg)
        else:
            self._qwen_translation_region_codes = {}
            self._qwen_translation_region_var.set("")

    def _qwen_translation_region_options(self) -> list[tuple[str, str]]:
        return self._backend_region_options("qianwen")

    def _backend_region_options(self, backend: str) -> list[tuple[str, str]]:
        return [
            (self._copy(label_key), code)
            for label_key, code in get_backend_region_options(backend)
        ]

    def _set_qwen_translation_region_vars(self, trans_cfg: dict) -> None:
        self._set_backend_region_vars("qianwen", trans_cfg)

    def _set_backend_region_vars(self, backend: str, trans_cfg: dict) -> None:
        backend_cfg = (
            trans_cfg.get(backend, {})
            if isinstance(trans_cfg.get(backend, {}), dict)
            else {}
        )
        base_url = str(backend_cfg.get("base_url", "") or "").strip().rstrip("/")
        raw_region = str(backend_cfg.get("region", "") or "").strip()
        default_region = backend_region_for_ui_language(backend, self._ui_lang)
        if raw_region:
            region = normalize_backend_region(
                backend,
                raw_region,
                default_region=default_region,
            )
        else:
            region = backend_region_from_base_url(backend, base_url) or default_region
        options = self._backend_region_options(backend)
        if not options:
            self._qwen_translation_region_codes = {}
            self._qwen_translation_region_var.set("")
            return
        self._qwen_translation_region_codes = {label: code for label, code in options}
        self._qwen_translation_region_var.set(
            next((label for label, code in options if code == region), options[0][0])
        )
        if region != "custom":
            self._backend_base_url_var.set(get_backend_region_base_url(backend, region))
        else:
            self._backend_base_url_var.set(base_url)

    def _init_asr_provider_vars(self, asr_cfg: dict) -> None:
        qwen_cfg = asr_cfg.get("qwen3_asr", {}) if isinstance(asr_cfg.get("qwen3_asr", {}), dict) else {}
        gemini_cfg = asr_cfg.get("gemini_live", {}) if isinstance(asr_cfg.get("gemini_live", {}), dict) else {}
        qwen_region = normalize_qwen3_asr_region(qwen_cfg.get("region", QWEN3_ASR_DEFAULT_REGION))
        qwen_model = str(qwen_cfg.get("model", QWEN3_ASR_DEFAULT_MODEL) or QWEN3_ASR_DEFAULT_MODEL)
        if qwen_model not in QWEN3_ASR_MODEL_CHOICES:
            qwen_model = QWEN3_ASR_DEFAULT_MODEL

        region_options = self._qwen_translation_region_options()
        self._qwen_region_codes = {label: code for label, code in region_options}
        self._qwen_region_var.set(next((label for label, code in region_options if code == qwen_region), region_options[0][0]))
        base_url = str(qwen_cfg.get("base_url", "") or "").strip().rstrip("/")
        if qwen_region != "custom":
            base_url = get_qwen3_asr_base_url(qwen_region)
        self._qwen_api_key_var.set(str(qwen_cfg.get("api_key", "") or ""))
        self._qwen_base_url_var.set(base_url)

        model_options = [
            (
                f"qwen3-asr-flash-2026-02-10 ({self._copy('recommended')})",
                "qwen3-asr-flash-2026-02-10",
            ),
            ("qwen3-asr-flash", "qwen3-asr-flash"),
        ]
        self._qwen_model_codes = {label: code for label, code in model_options}
        self._qwen_model_var.set(next((label for label, code in model_options if code == qwen_model), model_options[0][0]))
        self._gemini_api_key_var.set(str(gemini_cfg.get("api_key", "") or ""))
        self._gemini_model_var.set(str(gemini_cfg.get("model", "gemini-3.1-flash-live-preview") or "gemini-3.1-flash-live-preview"))

    def _init_asr_device_vars(self, asr_cfg: dict) -> None:
        device_options = self._device_options()
        self._asr_device_codes = {label: code for label, code in device_options}
        current_device = str(asr_cfg.get("device", "cpu") or "cpu").strip().lower()
        self._asr_device_var.set(
            next((label for label, code in device_options if code == current_device), device_options[0][0])
        )

    def _init_denoise_var(self, audio_cfg: dict) -> None:
        labels = DENOISE_LABELS.get(self._ui_lang) or DENOISE_LABELS.get(self._ui_lang.split("-", 1)[0]) or DENOISE_LABELS["en"]
        self._denoise_codes = {labels[key]: value for key, value in DENOISE_PRESETS}
        try:
            current = float(audio_cfg.get("denoise_strength", 0.0))
        except (TypeError, ValueError):
            current = 0.0
        key, _value = min(DENOISE_PRESETS, key=lambda item: abs(item[1] - current))
        self._denoise_var.set(labels[key])

    def _init_tts_device_vars(self, tts_cfg: dict) -> None:
        style_cfg = tts_cfg.get("style_bert_vits2", {}) if isinstance(tts_cfg.get("style_bert_vits2", {}), dict) else {}
        device_options = self._tts_device_options()
        self._tts_device_codes = {label: code for label, code in device_options}
        current_device = str(style_cfg.get("device", "cpu") or "cpu")
        self._tts_device_var.set(next((label for label, code in device_options if code == current_device), device_options[0][0]))

        bert_options = [
            (tr(self._ui_lang, "tts_bert_language_jp"), "jp"),
            (tr(self._ui_lang, "tts_bert_language_zh"), "zh"),
            (tr(self._ui_lang, "tts_bert_language_en"), "en"),
        ]
        self._tts_bert_language_codes = {label: code for label, code in bert_options}
        current_bert = normalize_style_bert_bert_language(style_cfg.get("bert_language", "jp"))
        self._tts_bert_language_var.set(next((label for label, code in bert_options if code == current_bert), bert_options[0][0]))

    def _init_tts_api_vars(self, tts_cfg: dict, engine: str) -> None:
        self._tts_api_region_codes = {}
        if engine not in TTS_API_ENGINE_IDS:
            self._tts_api_key_var.set("")
            self._tts_api_region_var.set("")
            self._tts_api_base_url_var.set("")
            self._tts_api_model_var.set("")
            return

        engine_cfg = tts_cfg.get(engine, {}) if isinstance(tts_cfg.get(engine, {}), dict) else {}
        resolved = resolve_tts_api_config(engine, engine_cfg)
        self._tts_api_key_var.set(str(resolved.get("api_key", "") or ""))
        region_options = self._tts_api_region_options(engine)
        self._tts_api_region_codes = {label: code for label, code in region_options}
        current_region = str(resolved.get("region", "") or "")
        self._tts_api_region_var.set(self._label_for_code(region_options, current_region))
        self._tts_api_base_url_var.set(str(resolved.get("base_url", "") or ""))
        self._tts_api_model_var.set(str(resolved.get("model", "") or ""))

    def _tts_api_region_options(self, engine: str) -> list[tuple[str, str]]:
        return [
            (self._copy(label_key), code)
            for label_key, code in get_tts_api_region_options(engine)
        ]

    def _selected_tts_api_engine(self) -> str:
        engine = self._selected_tts_engine()
        return engine if engine in TTS_API_ENGINE_IDS else ""

    def _selected_tts_api_region(self) -> str:
        engine = self._selected_tts_api_engine()
        if not engine:
            return ""
        return self._tts_api_region_codes.get(
            self._tts_api_region_var.value(),
            normalize_tts_api_region(engine, self._config.get("tts", {}).get(engine, {}).get("region")),
        )

    def _on_tts_api_region_changed(self, _label: str) -> None:
        engine = self._selected_tts_api_engine()
        if not engine:
            return
        region = self._selected_tts_api_region()
        if region != "custom":
            self._tts_api_base_url_var.set(get_tts_api_base_url(engine, region))
        entry = getattr(self, "_tts_api_base_url_entry", None)
        if entry is not None:
            entry.setText(self._tts_api_base_url_var.value())
            entry.setReadOnly(region != "custom")

    def _refresh_tts_api_visibility(self) -> None:
        frame = getattr(self, "_tts_api_frame", None)
        if frame is None:
            return
        visible = self._selected_tts_api_engine() in TTS_API_ENGINE_IDS
        frame.setVisible(visible)
        if visible:
            api_entry = getattr(self, "_tts_api_key_entry", None)
            if api_entry is not None:
                api_entry.setText(self._tts_api_key_var.value())
            model_entry = getattr(self, "_tts_api_model_entry", None)
            if model_entry is not None:
                model_entry.setText(self._tts_api_model_var.value())
            combo = getattr(self, "_tts_api_region_combo", None)
            if combo is not None:
                items = list(self._tts_api_region_codes.keys()) or [""]
                current = self._tts_api_region_var.value()
                combo.blockSignals(True)
                try:
                    combo.clear()
                    combo.addItems(items)
                    if current in items:
                        combo.setCurrentText(current)
                    elif items:
                        combo.setCurrentIndex(0)
                        self._tts_api_region_var.set(combo.currentText())
                finally:
                    combo.blockSignals(False)
            self._on_tts_api_region_changed(self._tts_api_region_var.value())

    def _tts_device_options(self) -> list[tuple[str, str]]:
        return self._device_options()

    def _device_options(self) -> list[tuple[str, str]]:
        return [
            (tr(self._ui_lang, "tts_device_cpu"), "cpu"),
            (tr(self._ui_lang, "tts_device_gpu"), "cuda"),
        ]

    def _asr_engine_options(self) -> tuple[tuple[str, str], ...]:
        labels_by_language = {
            "zh-CN": {
                "webspeech": "Web Speech（在线 / 浏览器）",
                "qwen3-asr": "Qwen3-ASR（在线）",
                "gemini-live": "Gemini Live（在线）",
                "whisper-large-v3-turbo": "Whisper Small（本地 / 英语快速）",
                "sensevoice-small": "SenseVoice Small（中文 / 粤语）",
            },
            "en": {
                "webspeech": "Web Speech (online / browser)",
                "qwen3-asr": "Qwen3-ASR (online)",
                "gemini-live": "Gemini Live (online)",
                "whisper-large-v3-turbo": "Whisper Small (local / fast English)",
                "sensevoice-small": "SenseVoice Small (Chinese / Cantonese)",
            },
            "ja": {
                "webspeech": "Web Speech（オンライン / ブラウザ）",
                "qwen3-asr": "Qwen3-ASR（オンライン）",
                "gemini-live": "Gemini Live（オンライン）",
                "whisper-large-v3-turbo": "Whisper Small（ローカル / 英語高速）",
                "sensevoice-small": "SenseVoice Small（中国語 / 広東語）",
            },
            "ru": {
                "webspeech": "Web Speech (онлайн / браузер)",
                "qwen3-asr": "Qwen3-ASR (онлайн)",
                "gemini-live": "Gemini Live (онлайн)",
                "whisper-large-v3-turbo": "Whisper Small (локально / быстрый английский)",
                "sensevoice-small": "SenseVoice Small (китайский / кантонский)",
            },
            "ko": {
                "webspeech": "Web Speech(온라인 / 브라우저)",
                "qwen3-asr": "Qwen3-ASR(온라인)",
                "gemini-live": "Gemini Live(온라인)",
                "whisper-large-v3-turbo": "Whisper Small(로컬 / 영어 빠름)",
                "sensevoice-small": "SenseVoice Small(중국어 / 광둥어)",
            },
        }
        labels = labels_by_language.get(self._ui_lang) or labels_by_language["en"]
        return tuple((labels.get(engine, engine), engine) for engine in USER_SELECTABLE_ASR_ENGINES)

    def _listen_asr_engine_options(self) -> tuple[tuple[str, str], ...]:
        follow_labels = {
            "zh-CN": "跟随我的麦克风设置（推荐）",
            "en": "Follow microphone ASR (reuse automatically)",
            "ja": "マイク音声モデルに追従（自動再利用）",
            "ru": "Следовать ASR микрофона (автоповтор)",
            "ko": "마이크 음성 모델 따르기(자동 재사용)",
        }
        options = [(follow_labels.get(self._ui_lang, follow_labels["en"]), ASR_ENGINE_FOLLOW_MAIN)]
        labels = {code: label for label, code in self._asr_engine_options()}
        options.extend((labels.get(engine, engine), engine) for engine in LISTEN_SELECTABLE_ASR_ENGINES)
        return tuple(options)

    # ----------------------------------------------------------------
    # UI Construction
    # ----------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        background = SettingsBackgroundWidget(self._background_image_path, self)
        background.setObjectName("settingsBackground")
        background.set_theme(_resolve_theme(self._theme_code_from_value(self._theme_var.value())))
        self._background_widget = background
        self._applied_background_path = self._background_image_path
        root.addWidget(background, 1)

        background_layout = QVBoxLayout(background)
        background_layout.setContentsMargins(0, 0, 0, 0)
        background_layout.setSpacing(0)

        chrome = QFrame()
        chrome.setObjectName("settingsChrome")
        background_layout.addWidget(chrome, 1)

        chrome_layout = QVBoxLayout(chrome)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(6)

        header = QFrame()
        header.setObjectName("headerCard")
        header.setFixedHeight(76)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        title = QLabel(self._copy("header_title"))
        title.setObjectName("headerTitle")
        subtitle = QLabel(self._copy("header_subtitle"))
        subtitle.setObjectName("headerSubtitle")
        subtitle.setWordWrap(True)
        self._header_title_label = title
        self._header_subtitle_label = subtitle
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header_layout.addLayout(title_col, 1)
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("versionLabel")
        header_layout.addWidget(version)
        self._theme_btn = QPushButton("")
        self._theme_btn.setObjectName("themeIconButton")
        self._theme_btn.setFixedSize(40, 40)
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.clicked.connect(self._on_theme_toggle)
        header_layout.addWidget(self._theme_btn)
        self._check_update_btn = QPushButton(self._copy("settings_check_update"))
        self._check_update_btn.setObjectName("headerButton")
        self._fit_button_to_text(
            self._check_update_btn,
            min_width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
            height=38,
            padding=SETTINGS_UPDATE_BUTTON_PADDING,
        )
        self._check_update_btn.clicked.connect(self._on_check_update)
        self._check_update_buttons.append(self._check_update_btn)
        header_layout.addWidget(self._check_update_btn)
        chrome_layout.addWidget(header)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(6)

        nav_widget = QFrame()
        nav_widget.setObjectName("navPanel")
        nav_widget.setFixedWidth(SETTINGS_NAV_WIDTH)
        nav_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(8, 8, 8, 8)
        nav_layout.setSpacing(0)

        nav_title = QLabel(self._copy("header_title"))
        nav_title.setObjectName("navTitle")
        nav_title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        nav_title.setFixedHeight(64)
        self._nav_title_label = nav_title
        nav_layout.addWidget(nav_title)

        self._nav_list = QListWidget()
        self._nav_list.setObjectName("navList")
        self._nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._nav_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._nav_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._nav_list.setCurrentRow(0)

        for page_id, _label in NAV_ITEMS:
            item = QListWidgetItem(self._nav_item_label(page_id))
            item.setData(1, page_id)
            item.setSizeHint(QSize(SETTINGS_NAV_WIDTH, 48))
            self._nav_list.addItem(item)

        self._nav_list.currentRowChanged.connect(self._on_nav_changed)
        nav_layout.addWidget(self._nav_list, 1)

        self._page_stack = QStackedWidget()
        self._page_stack.setObjectName("pageStack")

        for page_id, _label in NAV_ITEMS:
            build_now = page_id == "common" and not self._defer_initial_page
            page = self._build_page(page_id) if build_now else self._build_placeholder_page()
            if page_id == "common":
                if build_now:
                    self._built_pages.add(page_id)
            self._pages[page_id] = page
            self._page_stack.addWidget(page)
        self._page_prebuild_queue = [page_id for page_id, _label in NAV_ITEMS if page_id != "common"]

        body_layout.addWidget(nav_widget)
        body_layout.addWidget(self._page_stack, 1)
        chrome_layout.addLayout(body_layout, 1)

        footer_frame = QFrame()
        footer_frame.setObjectName("settingsFooter")
        footer_frame.setFixedHeight(48)
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(12, 6, 16, 6)
        footer.setSpacing(8)
        footer.addStretch(1)
        cancel_btn = QPushButton(tr(self._ui_lang, "cancel"))
        cancel_btn.setObjectName("footerSecondaryButton")
        self._fit_button_to_text(cancel_btn, min_width=96, height=34, padding=30)
        cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn = cancel_btn
        footer.addWidget(cancel_btn)
        save_btn = QPushButton(tr(self._ui_lang, "save"))
        save_btn.setObjectName("footerPrimaryButton")
        self._fit_button_to_text(save_btn, min_width=96, height=34, padding=30)
        save_btn.clicked.connect(self._save)
        self._save_btn = save_btn
        footer.addWidget(save_btn)
        chrome_layout.addWidget(footer_frame)

        self._apply_style()

    def _build_placeholder_page(self) -> QWidget:
        page = QScrollArea()
        page.setObjectName("settingsPageScroll")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.viewport().setObjectName("qt_scrollarea_viewport")
        content = QFrame()
        content.setObjectName("settingsPageContent")
        page.setWidget(content)
        return page

    def _ensure_page_built(self, page_id: str, *, refresh_style: bool = False) -> None:
        if self._page_stack is None or page_id in self._built_pages:
            return
        index = next((i for i, item in enumerate(NAV_ITEMS) if item[0] == page_id), -1)
        if index < 0:
            return
        self._page_stack.setUpdatesEnabled(False)
        try:
            old_page = self._page_stack.widget(index)
            page = self._build_page(page_id)
            self._pages[page_id] = page
            self._page_stack.removeWidget(old_page)
            old_page.deleteLater()
            self._page_stack.insertWidget(index, page)
            self._built_pages.add(page_id)
        finally:
            self._page_stack.setUpdatesEnabled(True)
        if refresh_style:
            self._apply_style()

    def select_page(self, page_id: str) -> None:
        row = next((i for i, item in enumerate(NAV_ITEMS) if item[0] == page_id), -1)
        if row < 0 or self._page_stack is None:
            return
        self._defer_initial_page = False
        self._ensure_page_built(page_id, refresh_style=False)
        if self._nav_list is not None and self._nav_list.currentRow() != row:
            self._nav_list.setCurrentRow(row)
        elif self._page_stack.currentIndex() != row:
            previous_row = self._page_stack.currentIndex()
            self._page_stack.setCurrentIndex(row)
            self._animate_page_switch(previous_row, row)
        else:
            self._page_stack.setCurrentIndex(row)

    def _finish_deferred_initial_page(self) -> None:
        if self._page_stack is None:
            return
        self._ensure_page_built("common", refresh_style=False)
        if self._page_stack.count():
            self._page_stack.setCurrentIndex(0)
        QTimer.singleShot(180, self._prebuild_next_page)

    def _prebuild_next_page(self) -> None:
        if self._page_stack is None:
            return
        while self._page_prebuild_queue:
            page_id = self._page_prebuild_queue.pop(0)
            if page_id in self._built_pages:
                continue
            current_index = self._page_stack.currentIndex()
            self._ensure_page_built(page_id, refresh_style=False)
            if 0 <= current_index < self._page_stack.count():
                self._page_stack.setCurrentIndex(current_index)
            QTimer.singleShot(35, self._prebuild_next_page)
            return
        # All pages built — apply style once at the end
        self._apply_style()

    def _build_page(self, page_id: str) -> QWidget:
        page = QScrollArea()
        page.setObjectName("settingsPageScroll")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.viewport().setObjectName("qt_scrollarea_viewport")

        content = QFrame()
        content.setObjectName("settingsPageContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        surface = QFrame()
        surface.setObjectName("settingsPageSurface")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(24, 22, 24, 22)
        surface_layout.setSpacing(16)

        title = QLabel(self._nav_item_label(page_id))
        title.setObjectName("pageTitle")
        surface_layout.addWidget(title)

        hint = QLabel(self._copy("header_subtitle"))
        hint.setObjectName("pageHint")
        hint.setWordWrap(True)
        surface_layout.addWidget(hint)

        if page_id == "common":
            self._build_common_page(surface_layout)
        elif page_id == "voice":
            self._build_voice_page(surface_layout)
        elif page_id == "vrc_listen":
            self._build_vrc_listen_page(surface_layout)
        elif page_id == "translation":
            self._build_translation_page(surface_layout)
        elif page_id == "tts":
            self._build_tts_page(surface_layout)
        elif page_id == "vr_integration":
            self._build_avatar_page(surface_layout)
        elif page_id == "roleplay":
            self._build_roleplay_page(surface_layout)
        elif page_id == "avatar":
            self._build_avatar_page(surface_layout)
        elif page_id == "hotkeys":
            self._build_hotkey_page(surface_layout)
        elif page_id == "model":
            self._build_updates_models_page(surface_layout)
        elif page_id == "advanced":
            self._build_advanced_page(surface_layout)

        surface_layout.addStretch(1)
        content_layout.addWidget(surface)
        page.setWidget(content)
        return page

    def _section_title(self, parent: QVBoxLayout, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        parent.addSpacing(6)
        parent.addWidget(label)
        parent.addSpacing(2)

    def _row_layout(self, parent: QVBoxLayout, label_text: str, *widgets, spacing: int = 10) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(spacing)
        lbl = QLabel(label_text)
        lbl.setObjectName("fieldLabel")
        lbl.setMinimumWidth(170)
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl)
        for w in widgets:
            row.addWidget(w, 1)
        parent.addLayout(row)
        return row

    def _field_hint_text(self, key: str) -> str:
        table = FIELD_HINTS.get(key, {})
        return (
            table.get(self._ui_lang)
            or table.get(self._ui_lang.split("-", 1)[0])
            or table.get("en")
            or ""
        )

    def _field_hint(self, parent: QVBoxLayout, key: str) -> QLabel | None:
        text = self._field_hint_text(key)
        if not text:
            return None
        hint = QLabel(text)
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        parent.addWidget(hint)
        return hint

    @staticmethod
    def _fit_button_to_text(
        btn: QPushButton | None,
        *,
        min_width: int,
        height: int,
        padding: int = 30,
        max_width: int | None = None,
    ) -> None:
        if btn is None:
            return
        width = max(min_width, btn.fontMetrics().horizontalAdvance(btn.text()) + padding, btn.sizeHint().width())
        if max_width is not None:
            width = min(width, max_width)
        btn.setMinimumWidth(width)
        btn.setFixedHeight(height)
        btn.setMaximumWidth(width if max_width is not None else 16777215)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        btn.updateGeometry()

    def _icon_button(
        self,
        text: str,
        icon_name: str,
        clicked,
        *,
        object_name: str = "secondaryButton",
        width: int = 112,
    ) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        self._fit_button_to_text(btn, min_width=width, height=36)
        btn.clicked.connect(clicked)
        return btn

    def _build_asr_model_action_card(self, parent: QVBoxLayout) -> None:
        self._asr_model_action_frame = QFrame()
        self._asr_model_action_frame.setObjectName("runtimeNotice")
        row = QHBoxLayout(self._asr_model_action_frame)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)

        self._asr_model_action_label = QLabel("")
        self._asr_model_action_label.setObjectName("hintLabel")
        self._asr_model_action_label.setWordWrap(True)
        row.addWidget(self._asr_model_action_label, 1)

        self._asr_model_download_btn = QPushButton(self._copy("model_download"))
        self._asr_model_download_btn.setObjectName("primaryButton")
        self._fit_button_to_text(self._asr_model_download_btn, min_width=112, height=34, padding=32)
        self._asr_model_download_btn.clicked.connect(self._open_selected_asr_model_download)
        row.addWidget(self._asr_model_download_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        parent.addWidget(self._asr_model_action_frame)
        self._refresh_asr_model_action_card()

    def _refresh_asr_model_action_card(self) -> None:
        frame = getattr(self, "_asr_model_action_frame", None)
        label = getattr(self, "_asr_model_action_label", None)
        button = getattr(self, "_asr_model_download_btn", None)
        if frame is None or label is None or button is None:
            return
        missing = self._missing_asr_model()
        if missing is None:
            frame.hide()
            return
        engine, spec = missing
        self._asr_model_download_engine = engine
        frame.show()
        label.setText(self._copy("model_pending", model_id=getattr(spec, "model_id", "")))
        button.setText(self._copy("model_download"))
        self._fit_button_to_text(button, min_width=112, height=34, padding=32)

    def _open_selected_asr_model_download(self) -> None:
        self._open_asr_model_download(self._asr_model_download_engine)

    def _build_asr_recognition_fields(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("asr_backend"))
        asr_opts = list(self._asr_codes.keys())
        asr_combo = self._combo("asr_engine", self._asr_engine_var, asr_opts, self._on_asr_engine_changed)
        self._row_layout(layout, self._copy("asr_backend"), asr_combo)
        self._field_hint(layout, "asr_backend")
        self._asr_device_combo = self._combo(
            "asr_device",
            self._asr_device_var,
            list(self._asr_device_codes.keys()),
            self._on_asr_device_changed,
        )
        self._asr_device_row = self._row_layout(layout, tr(self._ui_lang, "asr_device"), self._asr_device_combo)
        self._asr_device_hint_label = self._field_hint(layout, "asr_device")
        engine = self._asr_codes.get(self._asr_engine_var.value(), DEFAULT_ASR_ENGINE)
        self._asr_recommendation_label = QLabel(self._asr_recommendation_text(engine))
        self._asr_recommendation_label.setObjectName("recommendationPill")
        layout.addWidget(self._asr_recommendation_label, 0, Qt.AlignmentFlag.AlignLeft)
        self._asr_hint_label = QLabel(self._asr_hint_text(engine))
        self._asr_hint_label.setObjectName("hintLabel")
        self._asr_hint_label.setWordWrap(True)
        layout.addWidget(self._asr_hint_label)
        self._build_asr_model_action_card(layout)
        self._asr_provider_frame = QFrame()
        self._asr_provider_frame.setObjectName("subCard")
        self._asr_provider_layout = QVBoxLayout(self._asr_provider_frame)
        self._asr_provider_layout.setContentsMargins(16, 14, 16, 14)
        self._asr_provider_layout.setSpacing(8)
        layout.addWidget(self._asr_provider_frame)
        self._render_asr_provider_fields(engine)
        self._refresh_asr_device_visibility()

    def _build_common_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("mode_wizard_section"))
        wizard_hint = QLabel(self._copy("mode_wizard_hint"))
        wizard_hint.setObjectName("hintLabel")
        wizard_hint.setWordWrap(True)
        layout.addWidget(wizard_hint)
        wizard_row = QHBoxLayout()
        wizard_row.setSpacing(8)
        wizard_btn = self._icon_button(
            self._copy("open_mode_wizard"),
            "settings.svg",
            self._request_mode_wizard,
            object_name="primaryButton",
            width=150,
        )
        wizard_row.addWidget(wizard_btn)
        wizard_row.addStretch(1)
        layout.addLayout(wizard_row)

        self._section_title(layout, self._copy("settings_app_language"))
        self._row_layout(layout, self._copy("settings_app_language"), self._combo("ui_lang", self._ui_lang_var, list(self._ui_lang_codes.keys()), self._on_ui_lang_changed))
        self._field_hint(layout, "settings_app_language")

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_row.addWidget(QLabel(self._copy("settings_background")))
        self._bg_path_label = QLabel(self._background_image_path or self._copy("settings_background_none"))
        self._bg_path_label.setObjectName("fieldLabel")
        self._bg_path_label.setMinimumWidth(200)
        bg_row.addWidget(self._bg_path_label, 1)
        bg_browse_btn = self._icon_button(self._copy("browse"), "folder-open.svg", self._on_browse_background, width=104)
        bg_clear_btn = self._icon_button(tr(self._ui_lang, "clear"), "trash.svg", self._on_clear_background, width=88)
        bg_row.addWidget(bg_browse_btn)
        bg_row.addWidget(bg_clear_btn)
        layout.addLayout(bg_row)
        self._field_hint(layout, "settings_background")

    def _request_mode_wizard(self) -> None:
        if callable(self._on_mode_wizard_requested):
            self._on_mode_wizard_requested()

    def _build_voice_page(self, layout: QVBoxLayout) -> None:
        self._build_asr_recognition_fields(layout)

        self._section_title(layout, self._copy("input_device_mode"))
        self._row_layout(layout, self._copy("input_device_mode"), self._combo("input_mode", self._input_device_mode_var, list(self._input_mode_codes.keys()), self._on_input_device_mode_changed))
        self._field_hint(layout, "input_device_mode")
        self._row_layout(layout, self._copy("input_device"), self._combo("input_device", self._input_device_var, self._input_device_choices(), self._on_input_device_changed))
        self._field_hint(layout, "input_device")

        self._section_title(layout, self._copy("streaming"))
        self._row_layout(layout, self._copy("partial_interval"), self._line_edit("chunk_interval", self._chunk_interval_var, 160))
        self._row_layout(layout, self._copy("recognition_window"), self._line_edit("chunk_window", self._chunk_window_var, 160))
        self._row_layout(layout, self._copy("partial_hits"), self._line_edit("partial_hits", self._partial_hits_var, 160))

        self._section_title(layout, self._copy("vad"))
        self._row_layout(layout, self._copy("vad_seconds"), self._line_edit("vad", self._vad_var, 160))
        self._row_layout(layout, self._copy("vad_sensitivity"), self._line_edit("vad_sensitivity", self._vad_sensitivity_var, 160))
        self._row_layout(layout, self._copy("vad_speech_ratio"), self._line_edit("vad_speech_ratio", self._vad_speech_ratio_var, 160))
        self._row_layout(layout, self._copy("vad_activation"), self._line_edit("vad_activation", self._vad_activation_threshold_var, 160))
        self._row_layout(layout, self._copy("vad_min_rms"), self._line_edit("vad_min_rms", self._vad_min_rms_var, 160))
        self._row_layout(layout, self._copy("min_segment"), self._line_edit("min_segment", self._min_segment_var, 160))
        self._row_layout(layout, self._copy("max_segment"), self._line_edit("max_segment", self._max_segment_var, 160))
        self._row_layout(layout, self._copy("partial_min_speech"), self._line_edit("partial_min_speech", self._partial_min_speech_var, 160))
        diag_row = QHBoxLayout()
        diag_row.setSpacing(8)
        diag_row.addWidget(self._icon_button(self._copy("open_mic_diagnostics"), "activity.svg", lambda: self._request_audio_diagnostics("mic"), width=168))
        diag_row.addWidget(self._icon_button(self._copy("open_mic_calibration"), "settings.svg", lambda: self._request_vad_calibration("mic"), width=168))
        diag_row.addStretch(1)
        layout.addLayout(diag_row)

        denoise_labels = DENOISE_LABELS.get(self._ui_lang) or DENOISE_LABELS.get(self._ui_lang.split("-", 1)[0]) or DENOISE_LABELS["en"]
        self._section_title(layout, denoise_labels["title"])
        self._row_layout(layout, denoise_labels["title"], self._combo("denoise", self._denoise_var, list(self._denoise_codes.keys())))
        hint = QLabel(denoise_labels["hint"])
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._build_model_page(layout)
        self._build_dictionary_page(layout)

    def _build_vrc_listen_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("vrc_listen_section"))

        enabled_row = QHBoxLayout()
        enabled_row.setSpacing(10)
        enabled_row.addWidget(QLabel(self._copy("vrc_listen_enabled")))
        self._vrc_listen_enabled_check = self._check("vrc_enabled", self._vrc_listen_enabled_var)
        self._vrc_listen_enabled_check.toggled.connect(
            lambda value: self._emit_listen_state(enabled=value)
        )
        enabled_row.addWidget(self._vrc_listen_enabled_check)
        enabled_row.addStretch(1)
        layout.addLayout(enabled_row)

        self._vrc_listen_overlay_check = self._check("vrc_overlay", self._vrc_listen_overlay_var)
        self._vrc_listen_overlay_check.toggled.connect(lambda value: self._emit_listen_state(show_overlay=value))
        self._row_layout(layout, self._copy("vrc_listen_overlay"), self._vrc_listen_overlay_check)

        self._vrc_listen_send_check = self._check("vrc_send", self._vrc_listen_send_var)
        self._vrc_listen_send_check.toggled.connect(lambda value: self._emit_listen_state(send_to_chatbox=value))
        self._row_layout(layout, self._copy("vrc_listen_send"), self._vrc_listen_send_check)

        self._row_layout(layout, self._copy("asr_listen"), self._combo("listen_asr", self._listen_asr_engine_var, list(self._listen_asr_engine_codes.keys())))
        self._field_hint(layout, "asr_listen")
        self._row_layout(layout, self._copy("vrc_listen_device"), self._combo("loopback_device", self._loopback_device_var, self._loopback_device_choices()))
        self._field_hint(layout, "vrc_listen_device")

        self._row_layout(layout, self._copy("source_language"), self._combo("vrc_src", self._vrc_listen_src_var, list(self._listen_src_codes.keys())))
        self._row_layout(layout, tr(self._ui_lang, "target_language"), self._combo("vrc_tgt", self._vrc_listen_tgt_var, list(self._listen_lang_codes.keys())))
        self._build_switch_row(layout, self._copy("self_suppress"), self._listen_self_suppress_var)
        self._row_layout(layout, self._copy("self_suppress_seconds"), self._line_edit("listen_self_suppress_seconds", self._listen_self_suppress_seconds_var, 160))
        self._row_layout(layout, self._copy("segment_duration"), self._line_edit("listen_segment_duration", self._listen_segment_duration_var, 160))
        self._row_layout(layout, self._copy("tail_silence"), self._line_edit("listen_tail_silence", self._listen_tail_silence_var, 160))
        self._row_layout(layout, self._copy("listen_vad_min_rms"), self._line_edit("listen_vad_min_rms", self._listen_vad_min_rms_var, 160))
        diag_row = QHBoxLayout()
        diag_row.setSpacing(8)
        diag_row.addWidget(self._icon_button(self._copy("open_listen_diagnostics"), "activity.svg", lambda: self._request_audio_diagnostics("vrc_listen"), width=168))
        diag_row.addWidget(self._icon_button(self._copy("open_listen_calibration"), "settings.svg", lambda: self._request_vad_calibration("vrc_listen"), width=168))
        diag_row.addStretch(1)
        layout.addLayout(diag_row)

    def _build_translation_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("translation_provider"))
        backend_combo = self._combo("backend", self._backend_var, list(self._backend_codes.keys()), self._on_backend_changed)
        self._row_layout(layout, self._copy("translation_provider"), backend_combo)
        self._field_hint(layout, "translation_provider")
        self._backend_fields_frame = QFrame()
        self._backend_fields_frame.setObjectName("subCard")
        self._backend_fields_layout = QVBoxLayout(self._backend_fields_frame)
        self._backend_fields_layout.setContentsMargins(16, 14, 16, 14)
        self._backend_fields_layout.setSpacing(8)
        layout.addWidget(self._backend_fields_frame)
        self._render_backend_fields()
        self._row_layout(layout, self._copy("fallback_backends"), self._line_edit("fallback_backends", self._fallback_backends_var, 420))
        self._field_hint(layout, "fallback_backends")

        self._section_title(layout, self._copy("translation_domain_section"))
        self._row_layout(layout, self._copy("source_language"), self._combo("src_lang", self._src_lang_var, list(self._src_codes.keys())))
        self._field_hint(layout, "source_language")
        self._row_layout(layout, tr(self._ui_lang, "target_language"), self._combo("target_lang", self._target_lang_var, list(self._lang_codes.keys())))
        self._field_hint(layout, "target_language")
        self._row_layout(layout, self._copy("target_language_2"), self._combo("target_lang2", self._target_lang2_var, list(self._lang_codes.keys())))
        self._field_hint(layout, "target_language_2")
        self._row_layout(layout, self._copy("target_language_3"), self._combo("target_lang3", self._target_lang3_var, list(self._lang3_codes.keys())))
        self._field_hint(layout, "target_language_3")
        self._row_layout(layout, self._copy("output_format"), self._combo("output_fmt", self._output_format_var, list(self._fmt_codes.keys()), self._on_output_format_changed))
        self._row_layout(layout, self._copy("chatbox_template"), self._line_edit("chatbox_template", self._chatbox_template_var, 420))
        self._field_hint(layout, "chatbox_template")
        self._build_switch_row(layout, self._copy("send_to_chatbox"), self._mic_send_to_chatbox_var)
        self._field_hint(layout, "send_to_chatbox")

        self._build_dictionary_page(layout)

    def _build_tts_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("tts_section"))
        self._build_switch_row(layout, self._copy("tts_enable"), self._tts_enabled_var)
        tts_engine_combo = self._combo(
            "tts_engine", self._tts_engine_var,
            list(self._tts_engine_codes.keys()),
            self._on_tts_engine_changed,
        )
        self._row_layout(layout, tr(self._ui_lang, "tts_engine"), tts_engine_combo)
        self._field_hint(layout, "tts_engine")
        self._build_tts_runtime_card(layout)
        self._build_tts_api_config(layout)

        self._tts_voice_combo = self._combo("tts_voice", self._tts_voice_var, [tr(self._ui_lang, "tts_voice_loading")], self._on_tts_voice_changed)
        self._tts_voices_loading = False
        self._row_layout(layout, tr(self._ui_lang, "tts_voice"), self._tts_voice_combo)
        if not self._preloaded:
            QTimer.singleShot(0, self._load_tts_voices_async)

        self._sbv2_options_frame = QFrame()
        self._sbv2_options_frame.setObjectName("sbv2OptionsFrame")
        sbv2_layout = QVBoxLayout(self._sbv2_options_frame)
        sbv2_layout.setContentsMargins(0, 0, 0, 0)
        sbv2_layout.setSpacing(8)

        self._tts_device_combo = self._combo("tts_device", self._tts_device_var, list(self._tts_device_codes.keys()), self._on_tts_device_changed)
        self._row_layout(sbv2_layout, tr(self._ui_lang, "tts_device"), self._tts_device_combo)
        self._field_hint(sbv2_layout, "tts_device")
        self._row_layout(sbv2_layout, tr(self._ui_lang, "tts_bert_language"), self._combo("tts_bert_lang", self._tts_bert_language_var, list(self._tts_bert_language_codes.keys()), self._on_tts_bert_language_changed))
        self._field_hint(sbv2_layout, "tts_bert_language")
        self._bert_info_frame = QFrame()
        self._bert_info_frame.setObjectName("runtimeNotice")
        bert_layout = QHBoxLayout(self._bert_info_frame)
        bert_layout.setContentsMargins(12, 9, 12, 9)
        bert_layout.setSpacing(10)
        self._bert_info_label = QLabel("")
        self._bert_info_label.setObjectName("hintLabel")
        self._bert_info_label.setWordWrap(True)
        bert_layout.addWidget(self._bert_info_label, 1)
        self._bert_download_btn = QPushButton(tr(self._ui_lang, "tts_bert_download_btn"))
        self._bert_download_btn.setObjectName("primaryButton")
        self._fit_button_to_text(self._bert_download_btn, min_width=132, height=34, padding=32)
        self._bert_download_btn.clicked.connect(self._download_bert_model)
        bert_layout.addWidget(self._bert_download_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        sbv2_layout.addWidget(self._bert_info_frame)
        layout.addWidget(self._sbv2_options_frame)
        self._refresh_sbv2_options_visibility()

        rate_row = QHBoxLayout()
        rate_row.setSpacing(10)
        rate_row.addWidget(QLabel(tr(self._ui_lang, "tts_speed")))
        rate_slider = QSlider()
        rate_slider.setOrientation(Qt.Orientation.Horizontal)
        rate_slider.setRange(50, 200)
        rate_slider.setValue(int(self._tts_rate_var.value() * 100))
        rate_slider.valueChanged.connect(self._on_tts_rate_changed)
        rate_row.addWidget(rate_slider, 1)
        self._tts_rate_label = QLabel(f"{self._tts_rate_var.value():.1f}x")
        rate_row.addWidget(self._tts_rate_label)
        layout.addLayout(rate_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)
        vol_row.addWidget(QLabel(tr(self._ui_lang, "tts_volume")))
        vol_slider = QSlider()
        vol_slider.setOrientation(Qt.Orientation.Horizontal)
        vol_slider.setRange(0, 100)
        vol_slider.setValue(int(self._tts_volume_var.value() * 100))
        vol_slider.valueChanged.connect(self._on_tts_volume_changed)
        vol_row.addWidget(vol_slider, 1)
        self._tts_vol_label = QLabel(f"{int(self._tts_volume_var.value() * 100)}%")
        vol_row.addWidget(self._tts_vol_label)
        layout.addLayout(vol_row)

        self._build_switch_row(layout, self._copy("tts_output_vrchat"), self._tts_output_to_vrchat_var)
        self._tts_device_status_label = QLabel(self._tts_output_device_status_text())
        self._tts_device_status_label.setObjectName("hintLabel")
        self._tts_device_status_label.setWordWrap(True)
        layout.addWidget(self._tts_device_status_label)
        if self._tts_virtual_device_id is None:
            warning = QLabel(self._copy("tts_no_virtual_device"))
            warning.setObjectName("warningLabel")
            warning.setWordWrap(True)
            layout.addWidget(warning)
            mixline_row = QHBoxLayout()
            mixline_row.setSpacing(8)
            mixline_row.addWidget(self._icon_button(self._copy("tts_download_mixline"), "external-link.svg", lambda: self._open_external_url(MIXLINE_DOWNLOAD_URL), width=142))
            mixline_row.addStretch(1)
            layout.addLayout(mixline_row)
        self._field_hint(layout, "tts_output_vrchat")
        self._build_switch_row(layout, self._copy("tts_monitor"), self._tts_monitor_var)
        self._build_switch_row(layout, self._copy("tts_auto_read"), self._tts_auto_read_var)

        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        test_btn = QPushButton(tr(self._ui_lang, "tts_test"))
        self._tts_test_btn = test_btn
        test_btn.clicked.connect(self._on_tts_test)
        self._refresh_tts_test_button()
        test_row.addWidget(test_btn)
        stop_btn = QPushButton(self._copy("tts_stop"))
        self._tts_stop_btn = stop_btn
        stop_btn.clicked.connect(self._on_tts_stop)
        test_row.addWidget(stop_btn)
        test_row.addStretch(1)
        layout.addLayout(test_row)

        hint = QLabel(tr(self._ui_lang, "tts_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_avatar_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("avatar_section"))
        subtitle = QLabel(self._copy("avatar_subtitle"))
        subtitle.setObjectName("hintLabel")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._section_title(layout, self._copy("osc_listener_section"))
        self._build_switch_row(layout, self._copy("osc_listener_enabled"), self._osc_listener_enabled_var)
        self._row_layout(layout, self._copy("osc_receive_host"), self._line_edit("osc_receive_host", self._osc_receive_host_var, 180))
        self._row_layout(layout, self._copy("osc_receive_port"), self._line_edit("osc_receive_port", self._osc_receive_port_var, 120))
        self._build_switch_row(layout, self._copy("osc_sync_mute_self"), self._osc_sync_mute_self_var)
        self._build_switch_row(layout, self._copy("osc_allow_avatar_control"), self._osc_allow_avatar_control_var)
        self._row_layout(layout, self._copy("osc_control_prefix"), self._line_edit("osc_control_prefix", self._osc_control_prefix_var, 180))
        self._row_layout(layout, self._copy("osc_param_toggle_mic"), self._line_edit("osc_toggle_mic", self._osc_toggle_mic_var, 260))
        self._row_layout(layout, self._copy("osc_param_toggle_listen"), self._line_edit("osc_toggle_listen", self._osc_toggle_listen_var, 260))
        self._row_layout(layout, self._copy("osc_param_toggle_tts"), self._line_edit("osc_toggle_tts", self._osc_toggle_tts_var, 260))
        self._row_layout(layout, self._copy("osc_param_toggle_overlay"), self._line_edit("osc_toggle_overlay", self._osc_toggle_overlay_var, 260))

        self._section_title(layout, self._copy("avatar_section"))
        self._build_switch_row(layout, self._copy("avatar_sync_enabled"), self._avatar_sync_enabled_var)
        hint = QLabel(self._copy("avatar_sync_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._row_layout(layout, self._copy("avatar_param_translating"), self._line_edit("avatar_translating", self._avatar_translating_var, 260))
        self._row_layout(layout, self._copy("avatar_param_speaking"), self._line_edit("avatar_speaking", self._avatar_speaking_var, 260))
        self._row_layout(layout, self._copy("avatar_param_muted"), self._line_edit("avatar_muted", self._avatar_muted_var, 260))
        self._row_layout(layout, self._copy("avatar_param_error"), self._line_edit("avatar_error", self._avatar_error_var, 260))
        self._row_layout(layout, self._copy("avatar_param_target_language"), self._line_edit("avatar_target_language", self._avatar_target_language_var, 260))

    def _build_hotkey_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("hotkey_section"))
        self._row_layout(layout, self._copy("text_input_hotkey"), self._line_edit("text_input_hk", self._text_input_hotkey_var, 220))
        self._row_layout(layout, self._copy("mic_mute_hotkey"), self._line_edit("mic_mute_hk", self._mic_mute_hotkey_var, 220))
        hint = QLabel(self._copy("voice_control_placeholder"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_updates_models_page(self, layout: QVBoxLayout) -> None:
        self._build_model_page(layout, show_all=True)
        self._build_dictionary_page(layout)

    def _build_model_page(self, layout: QVBoxLayout, *, show_all: bool = False) -> None:
        self._section_title(layout, self._copy("model_section"))

        from src.asr.model_manager import model_exists
        from src.asr.model_registry import get_asr_runtime_spec

        if show_all:
            hint = QLabel(self._copy("model_picker_hint"))
            hint.setObjectName("hintLabel")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            for engine in USER_SELECTABLE_ASR_ENGINES:
                spec = get_asr_runtime_spec(self._config, engine)
                if not getattr(spec, "requires_local_model", False):
                    continue
                ready = model_exists(spec)
                card = QFrame()
                card.setObjectName("subCard")
                card_layout = QHBoxLayout(card)
                card_layout.setContentsMargins(16, 12, 16, 12)
                card_layout.setSpacing(12)

                text_col = QVBoxLayout()
                text_col.setSpacing(5)
                title_label = QLabel(str(getattr(spec, "label", engine)))
                title_label.setObjectName("fieldLabel")
                text_col.addWidget(title_label)
                desc_key = (
                    "model_download_desc_whisper"
                    if engine == "whisper-large-v3-turbo"
                    else "model_download_desc_sensevoice"
                )
                desc_label = QLabel(self._copy(desc_key))
                desc_label.setObjectName("hintLabel")
                desc_label.setWordWrap(True)
                text_col.addWidget(desc_label)
                model_id_label = QLabel(self._copy("model_file_id", model_id=getattr(spec, "model_id", "")))
                model_id_label.setObjectName("hintLabel")
                model_id_label.setWordWrap(True)
                text_col.addWidget(model_id_label)
                card_layout.addLayout(text_col, 1)

                status_label = QLabel(self._copy("model_status_ready" if ready else "model_status_pending"))
                status_label.setObjectName("successLabel" if ready else "hintLabel")
                card_layout.addWidget(status_label, 0, Qt.AlignmentFlag.AlignVCenter)

                if ready:
                    done_btn = QPushButton(self._copy("model_status_ready"))
                    done_btn.setObjectName("secondaryButton")
                    done_btn.setEnabled(False)
                    self._fit_button_to_text(done_btn, min_width=96, height=34, padding=30)
                    card_layout.addWidget(done_btn, 0, Qt.AlignmentFlag.AlignVCenter)
                else:
                    download_btn = self._icon_button(
                        self._copy("model_download"),
                        "download.svg",
                        lambda _checked=False, model_engine=engine: self._open_asr_model_download(model_engine),
                        object_name="primaryButton",
                        width=112,
                    )
                    card_layout.addWidget(download_btn, 0, Qt.AlignmentFlag.AlignVCenter)
                layout.addWidget(card)

            update_row = QHBoxLayout()
            update_row.setSpacing(8)
            check_btn = self._icon_button(
                self._copy("settings_check_update"),
                "refresh-cw.svg",
                self._on_check_update,
                width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
            )
            self._fit_button_to_text(
                check_btn,
                min_width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
                height=38,
                padding=SETTINGS_UPDATE_BUTTON_PADDING,
            )
            self._check_update_buttons.append(check_btn)
            update_row.addWidget(check_btn)
            update_row.addStretch(1)
            layout.addLayout(update_row)
            return

        self._section_title(layout, self._copy("model_current_section"))
        engine = self._asr_codes.get(self._asr_engine_var.value(), DEFAULT_ASR_ENGINE)
        spec = get_asr_runtime_spec(self._config, engine)

        status_label = QLabel()
        status_label.setObjectName("hintLabel")
        status_label.setWordWrap(True)
        if model_exists(spec):
            status_label.setText(self._copy("model_ready", model_id=spec.model_id))
            self._set_label_role(status_label, "successLabel")
        else:
            status_label.setText(self._copy("model_pending", model_id=spec.model_id))
        layout.addWidget(status_label)
        hint = QLabel(self._copy("model_check_hint"))
        hint.setObjectName("fieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        update_row = QHBoxLayout()
        update_row.setSpacing(8)
        if spec.requires_local_model and not model_exists(spec):
            download_btn = self._icon_button(self._copy("model_download"), "download.svg", lambda: self._open_asr_model_download(engine), object_name="primaryButton", width=128)
            update_row.addWidget(download_btn)
        check_btn = self._icon_button(
            self._copy("settings_check_update"),
            "refresh-cw.svg",
            self._on_check_update,
            width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
        )
        self._fit_button_to_text(
            check_btn,
            min_width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
            height=38,
            padding=SETTINGS_UPDATE_BUTTON_PADDING,
        )
        self._check_update_buttons.append(check_btn)
        update_row.addWidget(check_btn)
        update_row.addStretch(1)
        layout.addLayout(update_row)

    def _missing_asr_model(self) -> tuple[str, object] | None:
        from src.asr.model_manager import model_exists
        from src.asr.model_registry import get_asr_runtime_spec

        engine = self._asr_codes.get(self._asr_engine_var.value(), DEFAULT_ASR_ENGINE)
        spec = get_asr_runtime_spec(self._config, engine)
        if not getattr(spec, "requires_local_model", False) or model_exists(spec):
            return None
        return engine, spec

    def _maybe_prompt_missing_model_download(self) -> None:
        if self._missing_model_prompted:
            return
        missing = self._missing_asr_model()
        if missing is None:
            return
        engine, spec = missing
        self._missing_model_prompted = True
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle(self._copy("missing_model_prompt_title"))
        dialog.setText(
            self._copy(
                "missing_model_prompt_body",
                engine_label=getattr(spec, "label", engine),
                model_id=getattr(spec, "model_id", ""),
            )
        )
        download_btn = dialog.addButton(self._copy("open_downloader"), QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(self._copy("later"), QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        try:
            clicked_button = dialog.clickedButton()
        except RuntimeError:
            clicked_button = None
        if clicked_button is download_btn:
            self._open_asr_model_download(engine)

    def _build_dictionary_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("settings_dictionary"))
        hint = QLabel(self._copy("settings_dictionary_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        status_card = QFrame()
        status_card.setObjectName("subCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 14, 16, 14)
        status_layout.setSpacing(8)
        self._dictionary_status_label = QLabel("")
        self._dictionary_status_label.setObjectName("hintLabel")
        self._dictionary_status_label.setWordWrap(True)
        status_layout.addWidget(self._dictionary_status_label)
        self._dictionary_update_button = QPushButton(self._copy("settings_dictionary_update"))
        self._dictionary_update_button.clicked.connect(self._start_dictionary_update)
        status_layout.addWidget(self._dictionary_update_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(status_card)
        self._refresh_dictionary_status()

        self._section_title(layout, self._copy("settings_dictionary_custom"))
        custom_hint = QLabel(self._copy("settings_dictionary_custom_hint"))
        custom_hint.setObjectName("hintLabel")
        custom_hint.setWordWrap(True)
        layout.addWidget(custom_hint)
        self._row_layout(layout, self._copy("settings_dictionary_custom_replacement"), self._line_edit("dictionary_replacement", self._dictionary_custom_replacement_var, 280))
        patterns_label = QLabel(self._copy("settings_dictionary_custom_patterns"))
        patterns_label.setObjectName("fieldLabel")
        layout.addWidget(patterns_label)
        self._dictionary_custom_patterns_edit = QTextEdit()
        self._dictionary_custom_patterns_edit.setPlainText(self._dictionary_custom_patterns_var.value())
        self._dictionary_custom_patterns_edit.textChanged.connect(
            lambda: self._dictionary_custom_patterns_var.set(self._dictionary_custom_patterns_edit.toPlainText())
        )
        self._dictionary_custom_patterns_edit.setMinimumHeight(74)
        layout.addWidget(self._dictionary_custom_patterns_edit)
        patterns_hint = QLabel(self._copy("settings_dictionary_custom_patterns_hint"))
        patterns_hint.setObjectName("hintLabel")
        patterns_hint.setWordWrap(True)
        layout.addWidget(patterns_hint)
        save_row = QHBoxLayout()
        self._dictionary_custom_save_button = QPushButton(self._copy("settings_dictionary_custom_save"))
        self._dictionary_custom_save_button.setObjectName("primaryButton")
        self._dictionary_custom_save_button.clicked.connect(self._save_dictionary_custom_entry)
        save_row.addWidget(self._dictionary_custom_save_button)
        save_row.addStretch(1)
        layout.addLayout(save_row)
        self._dictionary_custom_status_label = QLabel("")
        self._dictionary_custom_status_label.setObjectName("hintLabel")
        self._dictionary_custom_status_label.setWordWrap(True)
        layout.addWidget(self._dictionary_custom_status_label)

    def _build_advanced_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("advanced_section"))
        hint = QLabel(self._copy("advanced_runtime_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._section_title(layout, self._copy("mode_wizard_section"))
        wizard_hint = QLabel(self._copy("mode_wizard_hint"))
        wizard_hint.setObjectName("hintLabel")
        wizard_hint.setWordWrap(True)
        layout.addWidget(wizard_hint)
        wizard_row = QHBoxLayout()
        wizard_row.addWidget(self._icon_button(self._copy("open_mode_wizard"), "settings.svg", self._request_mode_wizard, width=160))
        wizard_row.addStretch(1)
        layout.addLayout(wizard_row)

        self._section_title(layout, self._copy("hotkey_section"))
        self._row_layout(layout, self._copy("text_input_hotkey"), self._line_edit("text_input_hk_advanced", self._text_input_hotkey_var, 220))
        self._row_layout(layout, self._copy("mic_mute_hotkey"), self._line_edit("mic_mute_hk_advanced", self._mic_mute_hotkey_var, 220))

        self._section_title(layout, self._copy("logs_section"))
        logs_hint = QLabel(self._copy("logs_folder_hint"))
        logs_hint.setObjectName("hintLabel")
        logs_hint.setWordWrap(True)
        layout.addWidget(logs_hint)
        logs_row = QHBoxLayout()
        logs_row.addWidget(self._icon_button(self._copy("open_logs_folder"), "folder-open.svg", self._open_logs_folder, width=160))
        logs_row.addStretch(1)
        layout.addLayout(logs_row)

    def _build_roleplay_page(self, layout: QVBoxLayout) -> None:
        self._section_title(layout, self._copy("roleplay_section"))
        self._build_switch_row(layout, self._copy("roleplay_enabled"), self._roleplay_enabled_var)
        self._row_layout(layout, self._copy("roleplay_preset"), self._combo("roleplay_preset", self._roleplay_preset_var, list(self._roleplay_preset_codes.keys()), self._on_roleplay_preset_changed))
        self._row_layout(layout, self._copy("persona_name"), self._line_edit("persona_name", self._persona_name_var, 260))

        prompt_label = QLabel(self._copy("persona_prompt"))
        prompt_label.setObjectName("fieldLabel")
        layout.addWidget(prompt_label)
        self._roleplay_prompt_edit = QTextEdit()
        self._roleplay_prompt_edit.setPlainText(self._roleplay_prompt_var.value())
        self._roleplay_prompt_edit.textChanged.connect(
            lambda: self._roleplay_prompt_var.set(self._roleplay_prompt_edit.toPlainText())
        )
        self._roleplay_prompt_edit.setMinimumHeight(140)
        layout.addWidget(self._roleplay_prompt_edit)

        glossary_label = QLabel(self._copy("persona_glossary"))
        glossary_label.setObjectName("fieldLabel")
        layout.addWidget(glossary_label)
        self._roleplay_glossary_edit = QTextEdit()
        self._roleplay_glossary_edit.setPlainText(self._roleplay_glossary_var.value())
        self._roleplay_glossary_edit.textChanged.connect(
            lambda: self._roleplay_glossary_var.set(self._roleplay_glossary_edit.toPlainText())
        )
        self._roleplay_glossary_edit.setMinimumHeight(120)
        layout.addWidget(self._roleplay_glossary_edit)

    # ----------------------------------------------------------------
    # Widget factories
    # ----------------------------------------------------------------
    def _combo(self, name: str, var: _StrVar, items: list[str], changed_cb=None):
        combo = NoWheelComboBox()
        combo.addItems(items)
        combo.setMinimumHeight(32)
        combo.setMaximumHeight(32)
        current = var.value()
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.currentTextChanged.connect(var.set)
        if changed_cb:
            combo.currentTextChanged.connect(changed_cb)
        return combo

    def _line_edit(self, name: str, var: _StrVar, max_width: int = 0):
        le = QLineEdit()
        le.setText(var.value())
        le.setMinimumHeight(34)
        le.setMaximumHeight(34)
        le.textChanged.connect(var.set)
        if max_width > 0:
            le.setMaximumWidth(max_width)
        return le

    def _check(self, name: str, var: _BoolVar):
        cb = CapsuleSwitch()
        cb.setObjectName("capsuleSwitch")
        cb.setChecked(var.value())
        cb.sync_progress_to_state()
        cb.toggled.connect(var.set)
        return cb

    def _build_switch_row(self, parent: QVBoxLayout, label_text: str, var: _BoolVar) -> None:
        check = self._check(label_text, var)
        self._row_layout(parent, label_text, check)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                SettingsWindow._clear_layout(child_layout)

    def _backend_code(self) -> str:
        return self._backend_codes.get(self._backend_var.value(), normalize_backend(self._config.get("translation", {}).get("backend", "openai")))

    def _render_backend_fields(self) -> None:
        backend = self._backend_code()
        self._set_backend_field_vars(backend)
        self._clear_layout(self._backend_fields_layout)
        self._backend_base_url_entry = None

        hint = get_backend_api_key_hint(backend)
        api = self._line_edit("backend_api_key", self._backend_api_key_var)
        api.setEchoMode(QLineEdit.EchoMode.Password)
        self._row_layout(self._backend_fields_layout, self._copy("api_key"), api)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("hintLabel")
            hint_label.setWordWrap(True)
            self._backend_fields_layout.addWidget(hint_label)

        if backend_has_service_regions(backend):
            self._row_layout(
                self._backend_fields_layout,
                self._copy("translation_service_region"),
                self._combo(
                    f"{backend}_translation_region",
                    self._qwen_translation_region_var,
                    list(self._qwen_translation_region_codes.keys()),
                    self._on_qwen_translation_region_changed,
                ),
            )
            hint_key = {
                "qianwen": "qwen_translation_region",
                "deepseek": "deepseek_translation_region",
                "xiaomi": "xiaomi_translation_region",
                "nvidia": "nvidia_translation_region",
            }.get(backend, f"{backend}_translation_region")
            if hint_key in FIELD_HINTS:
                self._field_hint(self._backend_fields_layout, hint_key)

        base = self._line_edit("backend_base_url", self._backend_base_url_var)
        base.setReadOnly(
            not backend_base_url_is_editable(backend)
            or (backend_has_service_regions(backend) and self._selected_qwen_translation_region() != "custom")
        )
        self._backend_base_url_entry = base
        self._row_layout(self._backend_fields_layout, self._copy("base_url"), base)

        model_options = list(get_backend_model_options(backend, self._backend_model_var.value()))
        if backend_model_is_selectable(backend) and model_options:
            model_widget = self._combo("backend_model", self._backend_model_var, model_options, self._on_backend_model_changed)
            self._row_layout(self._backend_fields_layout, tr(self._ui_lang, "model"), model_widget)
        else:
            model_widget = self._line_edit("backend_model", self._backend_model_var)
            model_widget.textChanged.connect(lambda _text: self._refresh_backend_model_info())
            self._row_layout(self._backend_fields_layout, tr(self._ui_lang, "model"), model_widget)

        model_hint = get_backend_model_hint(backend)
        if model_hint:
            model_hint_label = QLabel(model_hint)
            model_hint_label.setObjectName("hintLabel")
            model_hint_label.setWordWrap(True)
            self._backend_fields_layout.addWidget(model_hint_label)
        self._row_layout(self._backend_fields_layout, self._copy("request_timeout"), self._line_edit("backend_timeout", self._backend_timeout_var, 120))
        self._row_layout(self._backend_fields_layout, self._copy("request_retries"), self._line_edit("backend_retries", self._backend_retries_var, 120))
        self._build_backend_model_info_card(self._backend_fields_layout)

    def _build_backend_model_info_card(self, parent: QVBoxLayout) -> None:
        self._backend_model_info_frame = QFrame()
        self._backend_model_info_frame.setObjectName("modelInfoCard")
        layout = QVBoxLayout(self._backend_model_info_frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._backend_model_info_title_label = QLabel("")
        self._backend_model_info_title_label.setObjectName("modelInfoTitle")
        layout.addWidget(self._backend_model_info_title_label)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        self._backend_model_badge_labels = {}
        for key in ("speed", "quality", "fit"):
            badge = QLabel("")
            badge.setObjectName("modelBadge")
            badge.setProperty("category", key)
            badge_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
            self._backend_model_badge_labels[key] = badge
        badge_row.addStretch(1)
        layout.addLayout(badge_row)

        self._backend_model_info_note_label = QLabel("")
        self._backend_model_info_note_label.setObjectName("hintLabel")
        self._backend_model_info_note_label.setWordWrap(True)
        layout.addWidget(self._backend_model_info_note_label)
        parent.addWidget(self._backend_model_info_frame)
        self._refresh_backend_model_info()

    def _profile_copy(self, key: str) -> str:
        return self._copy(key) if key in QT_SETTINGS_COPY else str(key)

    def _refresh_backend_model_info(self) -> None:
        if self._backend_model_info_title_label is None or self._backend_model_info_note_label is None:
            return
        profile = get_backend_model_profile(self._backend_code(), self._backend_model_var.value())
        self._backend_model_info_title_label.setText(
            f"{self._copy('model_title')} · {profile['model']} · "
            f"{self._copy('model_score')} {profile.get('score', '6.5')}/10"
        )
        for key, label in self._backend_model_badge_labels.items():
            value = profile.get(key, "balanced")
            label.setText(f"{self._copy(key)} · {self._profile_copy(value)}")
            label.setProperty("tone", value)
            label.style().unpolish(label)
            label.style().polish(label)
        self._backend_model_info_note_label.setText(self._profile_copy(profile.get("note", "custom")))

    def _on_backend_model_changed(self, _label: str) -> None:
        self._refresh_backend_model_info()

    def _on_backend_changed(self, _label: str) -> None:
        self._render_backend_fields()

    def _on_output_format_changed(self, _label: str) -> None:
        pass

    def _selected_qwen_translation_region(self) -> str:
        backend = self._backend_code()
        return self._qwen_translation_region_codes.get(
            self._qwen_translation_region_var.value(),
            backend_region_for_ui_language(backend, self._ui_lang),
        )

    def _on_qwen_translation_region_changed(self, _label: str) -> None:
        backend = self._backend_code()
        region = self._selected_qwen_translation_region()
        if region != "custom":
            self._backend_base_url_var.set(get_backend_region_base_url(backend, region))
        entry = getattr(self, "_backend_base_url_entry", None)
        if entry is not None:
            entry.setText(self._backend_base_url_var.value())
            entry.setReadOnly(not backend_base_url_is_editable(backend) or region != "custom")

    def _asr_hint_text(self, engine: str) -> str:
        if engine == "webspeech":
            return tr(self._ui_lang, "asr_hint_webspeech")
        if engine == "qwen3-asr":
            return tr(self._ui_lang, "asr_hint_qwen3")
        if engine == "gemini-live":
            return tr(self._ui_lang, "asr_hint_gemini")
        if engine == "whisper-large-v3-turbo":
            return tr(self._ui_lang, "asr_hint_whisper")
        return tr(self._ui_lang, "asr_hint_sensevoice")

    def _asr_recommendation_text(self, engine: str) -> str:
        if engine == "sensevoice-small":
            level = self._copy("recommended_high")
        elif engine in {"qwen3-asr", "gemini-live", "whisper-large-v3-turbo"}:
            level = self._copy("recommended_medium")
        else:
            level = self._copy("recommended_low")
        return f"{self._copy('recommendation')} · {level}"

    def _selected_asr_engine(self) -> str:
        value = self._asr_engine_var.value()
        return self._asr_codes.get(
            value,
            value if value in set(self._asr_codes.values()) else DEFAULT_ASR_ENGINE,
        )

    @staticmethod
    def _is_local_asr_engine(engine: str) -> bool:
        return engine in {"sensevoice-small", "whisper-large-v3-turbo"}

    @staticmethod
    def _set_layout_visible(layout: QHBoxLayout | None, visible: bool) -> None:
        if layout is None:
            return
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setVisible(visible)

    def _refresh_asr_device_visibility(self) -> None:
        visible = self._is_local_asr_engine(self._selected_asr_engine())
        self._set_layout_visible(getattr(self, "_asr_device_row", None), visible)
        hint = getattr(self, "_asr_device_hint_label", None)
        if hint is not None:
            hint.setVisible(visible)
        if not visible:
            self._set_asr_device_code("cpu")

    def _on_asr_engine_changed(self, _label: str) -> None:
        engine = self._selected_asr_engine()
        if hasattr(self, "_asr_hint_label"):
            self._asr_hint_label.setText(self._asr_hint_text(engine))
        if hasattr(self, "_asr_recommendation_label"):
            self._asr_recommendation_label.setText(self._asr_recommendation_text(engine))
        self._refresh_asr_device_visibility()
        self._refresh_asr_model_action_card()
        if hasattr(self, "_asr_provider_layout"):
            self._render_asr_provider_fields(engine)
        self._maybe_prompt_missing_model_download()

    def _render_asr_provider_fields(self, engine: str) -> None:
        self._clear_layout(self._asr_provider_layout)
        self._qwen_model_hint_label = None
        if engine in {"sensevoice-small", "whisper-large-v3-turbo"}:
            self._asr_provider_frame.hide()
            return
        self._asr_provider_frame.show()
        title = QLabel(self._copy("asr_provider_config"))
        title.setObjectName("fieldLabel")
        self._asr_provider_layout.addWidget(title)
        if engine == "qwen3-asr":
            api = self._line_edit("qwen_api_key", self._qwen_api_key_var)
            api.setEchoMode(QLineEdit.EchoMode.Password)
            self._row_layout(self._asr_provider_layout, self._copy("asr_api_key"), api)
            self._row_layout(self._asr_provider_layout, self._copy("asr_region"), self._combo("qwen_region", self._qwen_region_var, list(self._qwen_region_codes.keys()), self._on_qwen_region_changed))
            base = self._line_edit("qwen_base", self._qwen_base_url_var)
            base.setReadOnly(self._selected_qwen_region() != "custom")
            self._qwen_base_url_entry = base
            self._row_layout(self._asr_provider_layout, self._copy("base_url"), base)
            self._row_layout(
                self._asr_provider_layout,
                tr(self._ui_lang, "model"),
                self._combo("qwen_model", self._qwen_model_var, list(self._qwen_model_codes.keys()), self._refresh_qwen_model_hint),
            )
            self._qwen_model_hint_label = QLabel("")
            self._qwen_model_hint_label.setObjectName("hintLabel")
            self._qwen_model_hint_label.setWordWrap(True)
            self._asr_provider_layout.addWidget(self._qwen_model_hint_label)
            self._refresh_qwen_model_hint()
            return
        if engine == "gemini-live":
            api = self._line_edit("gemini_api_key", self._gemini_api_key_var)
            api.setEchoMode(QLineEdit.EchoMode.Password)
            self._row_layout(self._asr_provider_layout, self._copy("asr_api_key"), api)
            self._row_layout(self._asr_provider_layout, tr(self._ui_lang, "model"), self._line_edit("gemini_model", self._gemini_model_var))
            gemini_hint = QLabel(self._copy("gemini_model_recommendation"))
            gemini_hint.setObjectName("hintLabel")
            gemini_hint.setWordWrap(True)
            self._asr_provider_layout.addWidget(gemini_hint)
            return
        note = QLabel(tr(self._ui_lang, "asr_hint_webspeech"))
        note.setObjectName("hintLabel")
        note.setWordWrap(True)
        self._asr_provider_layout.addWidget(note)

    def _refresh_qwen_model_hint(self, _label: str | None = None) -> None:
        if self._qwen_model_hint_label is None:
            return
        self._qwen_model_hint_label.setText(self._copy("qwen_model_recommendation"))

    def _selected_qwen_region(self) -> str:
        return self._qwen_region_codes.get(self._qwen_region_var.value(), QWEN3_ASR_DEFAULT_REGION)

    def _selected_qwen_model(self) -> str:
        return self._qwen_model_codes.get(self._qwen_model_var.value(), QWEN3_ASR_DEFAULT_MODEL)

    def _on_qwen_region_changed(self, _label: str) -> None:
        if self._selected_qwen_region() != "custom":
            self._qwen_base_url_var.set(get_qwen3_asr_base_url(self._selected_qwen_region()))
        entry = getattr(self, "_qwen_base_url_entry", None)
        if entry is not None:
            entry.setText(self._qwen_base_url_var.value())
            entry.setReadOnly(self._selected_qwen_region() != "custom")

    def _input_device_choices(self) -> list[str]:
        default = self._copy("input_device_default")
        missing = self._copy("input_device_missing")
        names = [default]
        try:
            names.extend(str(device.get("name", "")).strip() for device in AudioRecorder.list_devices() if str(device.get("name", "")).strip())
        except Exception:
            logger.debug("Failed to enumerate input devices", exc_info=True)
        configured = self._input_device_var.value().strip()
        if configured and configured not in names:
            names.append(configured)
        if len(names) == 1:
            names.append(missing)
        if not configured:
            self._input_device_var.set(default)
        return names

    def _loopback_device_choices(self) -> list[str]:
        default = self._copy("vrc_listen_device_default")
        missing = self._copy("vrc_listen_device_missing")
        names = [default]
        try:
            names.extend(str(device.get("name", "")).strip() for device in _list_desktop_output_devices() if str(device.get("name", "")).strip())
        except Exception:
            logger.debug("Failed to enumerate output devices", exc_info=True)
        configured = self._loopback_device_var.value().strip()
        if configured and configured not in names:
            names.append(configured)
        if len(names) == 1:
            names.append(missing)
        if not configured:
            self._loopback_device_var.set(default)
        return names

    def _on_input_device_mode_changed(self, _label: str) -> None:
        if self._input_mode_codes.get(self._input_device_mode_var.value()) == "auto":
            self._input_device_var.set(self._copy("input_device_default"))

    def _on_input_device_changed(self, value: str) -> None:
        if value and value not in {self._copy("input_device_default"), self._copy("input_device_missing")}:
            self._input_device_mode_var.set(self._copy("input_device_mode_fixed"))

    def _selected_tts_engine(self) -> str:
        value = self._tts_engine_var.value()
        return self._tts_engine_codes.get(value, value if value in TTS_ENGINE_IDS else "edge")

    def _set_tts_device_code(self, device: str) -> None:
        label = next((label for label, code in self._tts_device_codes.items() if code == device), "")
        if not label:
            return
        self._tts_device_var.set(label)
        combo = getattr(self, "_tts_device_combo", None)
        if combo is not None and combo.currentText() != label:
            combo.blockSignals(True)
            try:
                combo.setCurrentText(label)
            finally:
                combo.blockSignals(False)

    def _set_asr_device_code(self, device: str) -> None:
        label = next((label for label, code in self._asr_device_codes.items() if code == device), "")
        if not label:
            return
        self._asr_device_var.set(label)
        combo = getattr(self, "_asr_device_combo", None)
        if combo is not None and combo.currentText() != label:
            combo.blockSignals(True)
            try:
                combo.setCurrentText(label)
            finally:
                combo.blockSignals(False)

    def _on_tts_device_changed(self, _label: str) -> None:
        if self._tts_device_codes.get(self._tts_device_var.value(), "cpu") != "cuda":
            return
        if gpu_runtime_available() or cuda_pytorch_installed():
            return
        self._set_tts_device_code("cpu")
        self._show_tts_gpu_unavailable_dialog()

    def _on_asr_device_changed(self, _label: str) -> None:
        if self._asr_device_codes.get(self._asr_device_var.value(), "cpu") != "cuda":
            return
        if not self._is_local_asr_engine(self._selected_asr_engine()):
            self._set_asr_device_code("cpu")
            return
        if gpu_runtime_available() or cuda_pytorch_installed():
            return
        self._set_asr_device_code("cpu")
        self._show_tts_gpu_unavailable_dialog()

    def _show_tts_gpu_unavailable_dialog(self) -> None:
        nvidia_status = detect_nvidia_driver()
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle(tr(self._ui_lang, "tts_gpu_unavailable_title"))
        body_key = "tts_gpu_unavailable_body_driver_ready" if nvidia_status.available else "tts_gpu_unavailable_body_driver_missing"
        dialog.setText(tr(self._ui_lang, body_key))
        driver_btn = None
        if not nvidia_status.available:
            driver_btn = dialog.addButton(tr(self._ui_lang, "tts_gpu_open_driver"), QMessageBox.ButtonRole.ActionRole)
        pytorch_btn = dialog.addButton(tr(self._ui_lang, "tts_gpu_open_pytorch"), QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(tr(self._ui_lang, "tts_gpu_keep_cpu"), QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        try:
            clicked_button = dialog.clickedButton()
        except RuntimeError:
            clicked_button = None
        if driver_btn is not None and clicked_button is driver_btn:
            self._open_external_url(NVIDIA_DRIVER_DOWNLOAD_URL)
        elif clicked_button is pytorch_btn:
            self._open_pytorch_cuda_install_dialog()

    def _open_pytorch_cuda_install_dialog(self) -> None:
        dialog = PytorchCudaInstallDialog(self._ui_lang, self)
        self._pytorch_cuda_install_dialog = dialog
        dialog.show()

    def _on_tts_rate_changed(self, value: int) -> None:
        self._tts_rate_var.set(value / 100.0)
        if hasattr(self, "_tts_rate_label"):
            self._tts_rate_label.setText(f"{self._tts_rate_var.value():.1f}x")

    def _on_tts_volume_changed(self, value: int) -> None:
        self._tts_volume_var.set(value / 100.0)
        if hasattr(self, "_tts_vol_label"):
            self._tts_vol_label.setText(f"{int(self._tts_volume_var.value() * 100)}%")

    def _tts_output_device_status_text(self) -> str:
        virtual = find_best_virtual_output_device()
        if virtual:
            self._tts_virtual_device_id, self._tts_virtual_device_name = virtual
            return self._copy("tts_output_device_detected", device=virtual[1])
        self._tts_virtual_device_id = None
        self._tts_virtual_device_name = None
        return self._copy("tts_no_virtual_device")

    def _selected_tts_bert_language(self) -> str:
        return self._tts_bert_language_codes.get(self._tts_bert_language_var.value(), "jp")

    def _selected_tts_test_language(self) -> str:
        engine = self._selected_tts_engine()
        if engine == "style_bert_vits2":
            return self._selected_tts_bert_language()
        if engine in {"voicevox", "aivis_speech"}:
            return "jp"
        if engine == "qwen_tts":
            target = str(self._config.get("translation", {}).get("target_language", "") or "").strip().lower()
            if target in {"ja", "jp"}:
                return "jp"
            if target in {"zh", "zh-cn", "cn", "chinese"}:
                return "zh"
            if target in {"en", "en-us", "en-gb", "english"}:
                return "en"
            ui_language = str(self._ui_lang or "").lower()
            if ui_language.startswith(("ja", "jp")):
                return "jp"
            if ui_language.startswith("zh"):
                return "zh"
            if ui_language.startswith("en"):
                return "en"
            return "en"
        voice_id = str(self._selected_tts_voice_id() or "").strip().lower()
        api_voice_languages = {
            "mimo_default": "zh",
            "冰糖": "zh",
            "茉莉": "zh",
            "苏打": "zh",
            "白桦": "zh",
            "mia": "en",
            "chloe": "en",
            "milo": "en",
            "dean": "en",
        }
        if voice_id in api_voice_languages:
            return api_voice_languages[voice_id]
        if voice_id.startswith(("ja", "jp")):
            return "jp"
        if voice_id.startswith("zh"):
            return "zh"
        if voice_id.startswith("en"):
            return "en"
        ui_language = str(self._ui_lang or "").lower()
        if ui_language.startswith(("ja", "jp")):
            return "jp"
        if ui_language.startswith("zh"):
            return "zh"
        if ui_language.startswith("en"):
            return "en"
        return "en"

    def _selected_tts_test_text(self) -> str:
        language = self._selected_tts_test_language()
        return TTS_TEST_TEXT_BY_LANGUAGE.get(language, self._copy("tts_test_text"))

    def _current_tts_test_timeout_ms(self, engine: str) -> int:
        configured_timeout = int(self._tts_test_timeout_ms)
        if configured_timeout != TTS_TEST_TIMEOUT_MS:
            return configured_timeout
        if engine == "style_bert_vits2":
            return STYLE_BERT_TTS_TEST_TIMEOUT_MS
        return configured_timeout

    def _style_bert_language_label(self, language_code: str) -> str:
        return next(
            (
                label
                for label, code in self._tts_bert_language_codes.items()
                if code == language_code
            ),
            language_code,
        )

    def _is_tts_placeholder(self, value: str) -> bool:
        placeholder_values = {
            self._copy("tts_voice_loading"),
            self._copy("tts_voice_none"),
            self._copy("tts_voice_local_service_unavailable"),
            self._copy("tts_voice_style_runtime_missing"),
            self._copy("tts_voice_custom_missing"),
            TTS_PLACEHOLDER_VOICE,
        }
        return str(value or "").strip() in placeholder_values

    def _selected_tts_voice_id(self) -> str:
        display = self._tts_voice_var.value()
        return self._tts_voice_display_to_id.get(display, display) if hasattr(self, "_tts_voice_display_to_id") else display

    def _current_tts_engine_config(self, engine: str) -> dict[str, object]:
        tts_cfg = self._config.get("tts", {}) if isinstance(self._config.get("tts", {}), dict) else {}
        source = tts_cfg.get(engine, {}) if isinstance(tts_cfg.get(engine, {}), dict) else {}
        engine_cfg = dict(source)
        voice = self._selected_tts_voice_id()
        if self._is_tts_placeholder(voice):
            voice = self._default_tts_voice(engine)
        engine_cfg["voice"] = voice or None
        engine_cfg["rate"] = self._safe_tts_rate(self._tts_rate_var.value(), engine=engine)
        engine_cfg["volume"] = self._safe_tts_volume(self._tts_volume_var.value())
        if engine in TTS_API_ENGINE_IDS:
            region = self._selected_tts_api_region()
            engine_cfg["api_key"] = self._tts_api_key_var.value().strip()
            engine_cfg["region"] = region
            engine_cfg["base_url"] = (
                get_tts_api_base_url(engine, region)
                if region != "custom"
                else self._tts_api_base_url_var.value().strip().rstrip("/")
            )
            engine_cfg["model"] = self._tts_api_model_var.value().strip() or str(
                get_tts_api_default_value(engine, "model") or ""
            )
            if engine == "qwen_tts":
                engine_cfg["language_type"] = self._qwen_tts_language_type_from_code(
                    self._selected_tts_test_language()
                )
        return engine_cfg

    @staticmethod
    def _qwen_tts_language_type_from_code(language: object) -> str:
        code = str(language or "").strip().lower().replace("_", "-")
        if code in {"jp", "ja"}:
            return "Japanese"
        if code == "zh":
            return "Chinese"
        if code == "ko":
            return "Korean"
        if code == "en":
            return "English"
        return ""

    @staticmethod
    def _safe_tts_rate(value: object, *, engine: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 1.0
        if engine == "pyttsx3" and parsed > 10.0:
            parsed = parsed / 150.0
        return max(0.5, min(2.0, parsed))

    @staticmethod
    def _safe_tts_volume(value: object) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.8
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _default_tts_voice(engine: str) -> str:
        voices = TTS_DEFAULT_VOICES.get(engine, ())
        return voices[0] if voices else ""

    def _refresh_bert_model_prompt(self) -> None:
        label = getattr(self, "_bert_info_label", None)
        button = getattr(self, "_bert_download_btn", None)
        frame = getattr(self, "_bert_info_frame", None)
        if label is None or button is None or frame is None:
            return
        if self._selected_tts_engine() != "style_bert_vits2":
            frame.hide()
            return
        language_code = self._selected_tts_bert_language()
        model_id = style_bert_bert_model_id(language_code)
        language_label = self._style_bert_language_label(language_code)
        ready = model_is_complete(model_id)
        frame.show()
        if ready:
            label.setObjectName("successLabel")
            label.setText(f"{tr(self._ui_lang, 'tts_bert_already_downloaded').format(language=language_label)}  {model_id}")
            label.style().unpolish(label)
            label.style().polish(label)
            button.hide()
            return
        try:
            download_state = get_downloader(model_id).state
        except Exception:
            download_state = DownloadState.IDLE
        if download_state in (DownloadState.DOWNLOADING, DownloadState.PAUSED):
            label.setObjectName("hintLabel")
            label.style().unpolish(label)
            label.style().polish(label)
            label.setText(tr(self._ui_lang, "tts_bert_model_info").format(language=language_label, model_id=model_id))
            button.setText(tr(self._ui_lang, "tts_bert_downloading").format(language=language_label))
            self._fit_button_to_text(button, min_width=132, height=34, padding=32, max_width=220)
            button.setEnabled(True)
            button.show()
            return
        label.setObjectName("hintLabel")
        label.style().unpolish(label)
        label.style().polish(label)
        label.setText(tr(self._ui_lang, "tts_bert_model_info").format(language=language_label, model_id=model_id))
        button.setText(tr(self._ui_lang, "tts_bert_download_btn"))
        self._fit_button_to_text(button, min_width=132, height=34, padding=32)
        button.setEnabled(True)
        button.show()

    def _download_bert_model(self) -> None:
        language_code = self._selected_tts_bert_language()
        model_id = style_bert_bert_model_id(language_code)
        language_label = self._tts_bert_language_var.value()
        button = getattr(self, "_bert_download_btn", None)
        if button is not None:
            button.setEnabled(False)
            button.setText(tr(self._ui_lang, "tts_bert_downloading").format(language=language_label))
            self._fit_button_to_text(button, min_width=132, height=34, padding=32, max_width=220)

        try:
            self._open_bert_download_window(model_id, language_label)
        except Exception as exc:
            if button is not None:
                button.setEnabled(True)
                button.setText(tr(self._ui_lang, "tts_bert_download_btn"))
                self._fit_button_to_text(button, min_width=132, height=34, padding=32)
            QMessageBox.warning(self, self._copy("save_failed"), str(exc))

    def _open_bert_download_window(self, model_id: str, language_label: str) -> None:
        from src.ui_qt.model_download_dialog import DownloadProgressWidget

        downloader = get_downloader(model_id)
        title = tr(self._ui_lang, "tts_bert_downloading").format(language=language_label)
        win = QDialog(self)
        win.setWindowTitle(title)
        win.setWindowModality(Qt.WindowModality.ApplicationModal)
        win.resize(520, 230)

        root = QVBoxLayout(win)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        header = QLabel(title)
        header.setObjectName("pageTitle")
        root.addWidget(header)

        detail = QLabel(tr(self._ui_lang, "tts_bert_model_info").format(language=language_label, model_id=model_id))
        detail.setObjectName("hintLabel")
        detail.setWordWrap(True)
        root.addWidget(detail)

        def on_completed() -> None:
            self.bert_refresh_requested.emit()
            QTimer.singleShot(1200, win.accept)

        def on_cancelled() -> None:
            self.bert_refresh_requested.emit()

        progress = DownloadProgressWidget(
            win,
            "style_bert_vits2",
            downloader=downloader,
            model_id=model_id,
            on_completed=on_completed,
            on_cancelled=on_cancelled,
            ui_lang=self._ui_lang,
        )
        root.addWidget(progress)

        self._bert_download_window = win

        def on_finished(_result: int) -> None:
            if self._bert_download_window is win:
                self._bert_download_window = None
            self.bert_refresh_requested.emit()

        win.finished.connect(on_finished)
        win.show()
        win.raise_()
        win.activateWindow()
        downloader.start()

    def _refresh_sbv2_options_visibility(self) -> None:
        frame = getattr(self, "_sbv2_options_frame", None)
        if frame is None:
            return
        visible = self._selected_tts_engine() == "style_bert_vits2"
        frame.setVisible(visible)
        if visible:
            self._refresh_bert_model_prompt()

    def _open_asr_model_download(self, engine: str) -> None:
        try:
            from src.ui_qt.model_download_dialog import SetupWindow
            win = SetupWindow(engine, ui_lang=self._ui_lang)
            win.setWindowModality(Qt.WindowModality.ApplicationModal)
            self._asr_model_download_window = win
            win.finished.connect(lambda _result: self._refresh_model_download_pages())
            win.show()
            win.raise_()
            win.activateWindow()
        except Exception as exc:
            QMessageBox.warning(self, self._copy("save_failed"), str(exc))

    def _open_logs_folder(self) -> None:
        path = logs_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_roleplay_preset_changed(self, label: str) -> None:
        preset_id = self._roleplay_preset_codes.get(label, "custom")
        if preset_id == "custom":
            return
        profile = ROLEPLAY_PRESETS.get(preset_id, ROLEPLAY_PRESETS["custom"])
        self._persona_name_var.set(profile.get("persona_name", ""))
        if hasattr(self, "_roleplay_prompt_edit"):
            self._roleplay_prompt_edit.setPlainText(profile.get("persona_prompt", ""))
        if hasattr(self, "_roleplay_glossary_edit"):
            self._roleplay_glossary_edit.setPlainText(profile.get("persona_glossary", ""))

    def _parse_positive_float(self, value: str, field_name: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(f"{field_name}: must be a number") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name}: must be positive")
        return parsed

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name}: must be an integer") from exc
        if parsed <= 0:
            raise ValueError(f"{field_name}: must be positive")
        return parsed

    def _parse_float_range(self, value: str, field_name: str, min_val: float, max_val: float) -> float:
        parsed = self._parse_positive_float(value, field_name) if min_val > 0 else float(value)
        if parsed < min_val or parsed > max_val:
            raise ValueError(f"{field_name}: must be between {min_val} and {max_val}")
        return parsed

    def sync_vrc_listen_state(
        self,
        *,
        enabled: bool | None = None,
        show_overlay: bool | None = None,
        send_to_chatbox: bool | None = None,
    ) -> None:
        if enabled is not None:
            self._vrc_listen_enabled_var.set(bool(enabled))
            check = getattr(self, "_vrc_listen_enabled_check", None)
            if check is not None:
                blocked = check.blockSignals(True)
                check.setChecked(bool(enabled))
                check.blockSignals(blocked)
        if show_overlay is not None:
            self._vrc_listen_overlay_var.set(bool(show_overlay))
            check = getattr(self, "_vrc_listen_overlay_check", None)
            if check is not None:
                blocked = check.blockSignals(True)
                check.setChecked(bool(show_overlay))
                check.blockSignals(blocked)
        if send_to_chatbox is not None:
            self._vrc_listen_send_var.set(bool(send_to_chatbox))
            check = getattr(self, "_vrc_listen_send_check", None)
            if check is not None:
                blocked = check.blockSignals(True)
                check.setChecked(bool(send_to_chatbox))
                check.blockSignals(blocked)

    def sync_theme(self, theme_preference: object, *, smooth: bool = False) -> None:
        self._apply_theme_preference(theme_preference, notify=False, smooth=smooth)

    def _apply_theme_preference(self, theme_preference: object, *, notify: bool, smooth: bool = False) -> None:
        preference = _normalize_theme_preference(theme_preference)
        labels = self._theme_labels()
        current_preference = self._theme_code_from_value(self._theme_var.value())
        if preference == current_preference and not notify and getattr(self, "_applied_settings_theme", "") == _resolve_theme(preference):
            return

        def apply_theme() -> None:
            self._config.setdefault("ui", {})[MAIN_THEME_CONFIG_KEY] = preference
            self._theme_var.set(labels.get(preference, labels["system"]))
            self._apply_style(immediate=True)

        if smooth:
            play_theme_fade(
                self._background_widget or self,
                update=apply_theme,
                duration_ms=180,
                start_opacity=0.96,
            )
        else:
            apply_theme()
        if notify and callable(self._on_theme_changed_callback):
            self._on_theme_changed_callback(preference)

    def _emit_listen_state(
        self,
        *,
        enabled: bool | None = None,
        show_overlay: bool | None = None,
        send_to_chatbox: bool | None = None,
    ) -> None:
        if callable(self._on_listen_state_changed):
            self._on_listen_state_changed(enabled, show_overlay, send_to_chatbox)

    def _request_audio_diagnostics(self, target: str) -> None:
        if callable(self._on_audio_diagnostics_requested):
            self._on_audio_diagnostics_requested(target)

    def _request_vad_calibration(self, target: str) -> None:
        if callable(self._on_vad_calibration_requested):
            self._on_vad_calibration_requested(target)

    # ----------------------------------------------------------------
    # Event handlers
    # ----------------------------------------------------------------
    def _on_nav_changed(self, row: int) -> None:
        if self._page_stack and row >= 0:
            previous_row = self._page_stack.currentIndex()
            if previous_row == row:
                return
            if row < len(NAV_ITEMS):
                self._ensure_page_built(NAV_ITEMS[row][0])
            self._page_stack.setCurrentIndex(row)
            self._animate_page_switch(previous_row, row)

    def _animate_page_switch(self, previous_row: int, row: int) -> None:
        if self._page_stack is None:
            return
        widget = self._page_stack.currentWidget()
        if widget is None:
            return
        if self._page_fade_animation is not None:
            self._page_fade_animation.stop()
        if self._page_effect_widget is not None:
            try:
                self._page_effect_widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.72 if abs(row - previous_row) > 1 else 0.80)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(150)
        animation.setStartValue(effect.opacity())
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._page_fade_animation = animation
        self._page_effect_widget = widget

        def finish() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if self._page_fade_animation is animation:
                self._page_fade_animation = None
            if self._page_effect_widget is widget:
                self._page_effect_widget = None

        animation.finished.connect(finish)
        animation.start()

    def _animate_theme_refresh(self) -> None:
        widget = self._background_widget or (self._page_stack.currentWidget() if self._page_stack is not None else None)
        if widget is None:
            return
        if self._theme_fade_animation is not None:
            self._theme_fade_animation.stop()
        if self._theme_effect_widget is not None:
            try:
                self._theme_effect_widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.92)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(120)
        animation.setStartValue(0.92)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._theme_fade_animation = animation
        self._theme_effect_widget = widget

        def finish() -> None:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass
            if self._theme_fade_animation is animation:
                self._theme_fade_animation = None
            if self._theme_effect_widget is widget:
                self._theme_effect_widget = None

        animation.finished.connect(finish)
        animation.start()

    def _on_ui_lang_changed(self, text: str) -> None:
        code = self._ui_lang_codes.get(text)
        if not code or code == self._ui_lang:
            return
        current_codes = self._capture_language_option_codes()
        self._apply_language_option_codes(current_codes, code)
        self._fmt_codes = {l: c for l, c in get_output_format_options(self._ui_lang)}
        self._refresh_language_widgets()

    def _on_theme_changed(self, _text: str) -> None:
        self._apply_theme_preference(self._theme_code_from_value(_text), notify=True, smooth=True)

    def _on_theme_toggle(self) -> None:
        next_code = "light" if _resolve_theme(self._theme_code_from_value(self._theme_var.value())) == "dark" else "dark"
        if self._theme_btn is not None:
            self._theme_btn.setEnabled(False)
        self._apply_theme_preference(next_code, notify=True, smooth=True)
        if self._theme_btn is not None:
            self._theme_btn.setEnabled(True)

    def _refresh_theme_button(self) -> None:
        if self._theme_btn is None:
            return
        active_theme = getattr(self, "_active_theme", _resolve_theme(self._theme_code_from_value(self._theme_var.value())))
        icon_name = "sun.svg" if active_theme == "dark" else "moon.svg"
        icon = ui_icon(icon_name, 17, icon_tint(active_theme, strong=True))
        self._theme_btn.setIcon(icon)
        self._theme_btn.setText("" if not icon.isNull() else active_theme[:1].upper())
        self._theme_btn.setToolTip(self._copy("settings_theme"))

    def _set_label_role(self, label: QLabel | None, role: str) -> None:
        if label is None:
            return
        label.setObjectName(role)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _on_browse_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._copy("settings_background"),
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            dest = backgrounds_dir() / Path(path).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            self._background_image_path = str(dest)
            self._bg_path_label.setText(str(dest))
            ui_cfg = self._config.setdefault("ui", {})
            ui_cfg["background_image_path"] = str(dest)
            if self._background_widget is not None:
                self._background_widget.set_background_path(str(dest))
                self._applied_background_path = str(dest)

    def _on_clear_background(self) -> None:
        self._background_image_path = ""
        self._bg_path_label.setText(self._copy("settings_background_none"))
        ui_cfg = self._config.setdefault("ui", {})
        ui_cfg["background_image_path"] = ""
        if self._background_widget is not None:
            self._background_widget.set_background_path("")
            self._applied_background_path = ""

    def _on_tts_engine_changed(self, text: str) -> None:
        engine = self._tts_engine_codes.get(text, text)
        self._tts_engine_var.set(engine)
        tts_cfg = self._config.get("tts", {}) if isinstance(self._config.get("tts", {}), dict) else {}
        engine_cfg = tts_cfg.get(engine, {}) if isinstance(tts_cfg.get(engine, {}), dict) else {}
        self._tts_voice_var.set(str(engine_cfg.get("voice") or ""))
        self._tts_rate_var.set(float(engine_cfg.get("rate", tts_cfg.get("rate", 1.0)) or 1.0))
        self._tts_volume_var.set(float(engine_cfg.get("volume", tts_cfg.get("volume", 0.8)) or 0.8))
        self._init_tts_api_vars(tts_cfg, engine)
        self._load_tts_voices()
        self._refresh_tts_runtime_card()
        self._refresh_tts_api_visibility()
        self._refresh_sbv2_options_visibility()

    def _on_tts_voice_changed(self, _text: str) -> None:
        self._refresh_bert_model_prompt()

    def _on_tts_bert_language_changed(self, _text: str) -> None:
        self._tts_voices_loaded.pop("style_bert_vits2", None)
        if self._selected_tts_engine() == "style_bert_vits2":
            self._load_tts_voices()
        self._refresh_bert_model_prompt()

    def _build_tts_runtime_card(self, layout: QVBoxLayout) -> None:
        self._tts_runtime_frame = QFrame()
        self._tts_runtime_frame.setObjectName("runtimeNotice")
        frame_layout = QHBoxLayout(self._tts_runtime_frame)
        frame_layout.setContentsMargins(12, 9, 12, 9)
        frame_layout.setSpacing(10)
        self._tts_runtime_status_label = QLabel("")
        self._tts_runtime_status_label.setObjectName("hintLabel")
        self._tts_runtime_status_label.setWordWrap(True)
        frame_layout.addWidget(self._tts_runtime_status_label, 1)
        self._tts_runtime_action_btn = QPushButton("")
        self._tts_runtime_action_btn.setObjectName("secondaryButton")
        self._tts_runtime_action_btn.setFixedSize(112, 34)
        frame_layout.addWidget(self._tts_runtime_action_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._tts_runtime_frame)
        self._refresh_tts_runtime_card()

    def _build_tts_api_config(self, layout: QVBoxLayout) -> None:
        self._tts_api_frame = QFrame()
        self._tts_api_frame.setObjectName("runtimeNotice")
        frame_layout = QVBoxLayout(self._tts_api_frame)
        frame_layout.setContentsMargins(12, 10, 12, 10)
        frame_layout.setSpacing(8)

        title = QLabel(self._copy("tts_api_section"))
        title.setObjectName("fieldLabel")
        frame_layout.addWidget(title)

        hint = QLabel(self._copy("tts_api_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        frame_layout.addWidget(hint)

        api = self._line_edit("tts_api_key", self._tts_api_key_var)
        api.setEchoMode(QLineEdit.EchoMode.Password)
        self._tts_api_key_entry = api
        self._row_layout(frame_layout, self._copy("api_key"), api)

        region_items = list(self._tts_api_region_codes.keys()) or [""]
        region_combo = self._combo(
            "tts_api_region",
            self._tts_api_region_var,
            region_items,
            self._on_tts_api_region_changed,
        )
        self._tts_api_region_combo = region_combo
        self._row_layout(frame_layout, self._copy("tts_service_region"), region_combo)

        base = self._line_edit("tts_api_base_url", self._tts_api_base_url_var)
        base.setReadOnly(self._selected_tts_api_region() != "custom")
        self._tts_api_base_url_entry = base
        self._row_layout(frame_layout, self._copy("base_url"), base)

        model = self._line_edit("tts_api_model", self._tts_api_model_var)
        self._tts_api_model_entry = model
        self._row_layout(frame_layout, self._copy("tts_api_model"), model)

        layout.addWidget(self._tts_api_frame)
        self._refresh_tts_api_visibility()

    def _refresh_tts_runtime_card(self) -> None:
        frame = getattr(self, "_tts_runtime_frame", None)
        label = getattr(self, "_tts_runtime_status_label", None)
        button = getattr(self, "_tts_runtime_action_btn", None)
        if frame is None or label is None or button is None:
            return
        engine = self._selected_tts_engine()
        if engine not in {"voicevox", "aivis_speech", "style_bert_vits2"}:
            frame.hide()
            return
        frame.show()
        frame.setObjectName("voiceDropZone" if engine == "style_bert_vits2" else "runtimeNotice")
        frame.style().unpolish(frame)
        frame.style().polish(frame)
        if engine == "voicevox":
            ready = self._local_tts_availability.get(engine)
            if ready is None:
                label.setText(self._copy("tts_voice_loading"))
                button.setVisible(False)
                self._check_local_tts_engine_async(engine)
            else:
                label.setText(self._copy("local_tts_ready" if ready else "local_tts_unavailable", engine="VOICEVOX"))
                button.setVisible(not ready)
            button.setText(self._copy("download_voicevox"))
            self._set_tts_runtime_button_action(button, self._open_voicevox_download)
        elif engine == "aivis_speech":
            ready = self._local_tts_availability.get(engine)
            if ready is None:
                label.setText(self._copy("tts_voice_loading"))
                button.setVisible(False)
                self._check_local_tts_engine_async(engine)
            else:
                label.setText(self._copy("local_tts_ready" if ready else "local_tts_unavailable", engine="AivisSpeech"))
                button.setVisible(not ready)
            button.setText(self._copy("download_aivis"))
            self._set_tts_runtime_button_action(button, self._open_aivis_download)
        else:
            button.setVisible(True)
            label.setText(self._copy("tts_custom_voice_picker"))
            button.setText(self._copy("tts_choose_voice_folder"))
            self._set_tts_runtime_button_action(button, self._import_style_bert_voice)
        text_width = button.fontMetrics().horizontalAdvance(button.text()) + 30
        button.setFixedWidth(max(104, text_width))

    def _check_local_tts_engine_async(self, engine: str) -> None:
        if engine in self._local_tts_checking:
            return
        self._local_tts_checking.add(engine)

        def worker() -> None:
            ready = self._local_tts_engine_available(engine)
            self._call_in_ui(lambda eng=engine, value=ready: self._on_local_tts_engine_checked(eng, value))

        threading.Thread(target=worker, daemon=True, name=f"tts-runtime-check-{engine}").start()

    def _on_local_tts_engine_checked(self, engine: str, ready: bool) -> None:
        self._local_tts_checking.discard(engine)
        self._local_tts_availability[engine] = bool(ready)
        if engine == self._selected_tts_engine():
            self._refresh_tts_runtime_card()

    def _local_tts_engine_available(self, engine: str) -> bool:
        try:
            tts = create_tts_engine(engine)
            available = getattr(tts, "is_available", None)
            return bool(available()) if callable(available) else bool(tts)
        except Exception:
            return False

    def _open_external_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _open_voicevox_download(self) -> None:
        self._open_external_url(VOICEVOX_DOWNLOAD_URL)

    def _open_aivis_download(self) -> None:
        self._open_external_url(AIVIS_SPEECH_DOWNLOAD_URL)

    def _set_tts_runtime_button_action(self, button: QPushButton, action: Callable[[], None] | None) -> None:
        previous = self._tts_runtime_button_action
        if previous is not None:
            try:
                button.clicked.disconnect(previous)
            except (RuntimeError, TypeError):
                pass
        self._tts_runtime_button_action = action
        if action is not None:
            button.clicked.connect(action)

    def _call_in_ui(self, callback, delay_ms: int = 0) -> bool:
        if getattr(self, "_closing", False):
            return False
        try:
            delay = max(0, int(delay_ms or 0))
        except (TypeError, ValueError):
            delay = 0
        if threading.get_ident() != getattr(self, "_ui_thread_id", threading.get_ident()):
            try:
                self._ui_callback_queue.put_nowait((delay, callback))
                self.ui_callback_requested.emit()
                return True
            except Exception:
                logger.debug("Failed to queue settings UI callback", exc_info=True)
                return False
        QTimer.singleShot(delay, lambda cb=callback: self._run_ui_callback(cb))
        return True

    def _run_ui_callback(self, callback) -> None:
        if getattr(self, "_closing", False):
            return
        try:
            callback()
        except Exception:
            logger.exception("Settings UI callback failed")

    def _drain_ui_callback_queue(self) -> None:
        if getattr(self, "_closing", False):
            return
        processed = 0
        while processed < 128:
            try:
                delay_ms, callback = self._ui_callback_queue.get_nowait()
            except queue.Empty:
                break
            QTimer.singleShot(delay_ms, lambda cb=callback: self._run_ui_callback(cb))
            processed += 1
        if processed == 128 and not self._ui_callback_queue.empty():
            QTimer.singleShot(0, self._drain_ui_callback_queue)

    def _import_style_bert_voice(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self._copy("tts_import_custom_voice"))
        if not path:
            return
        try:
            imported = import_style_bert_model_path(path)
            count = len(imported) if isinstance(imported, list) else 1
            QMessageBox.information(
                self,
                self._copy("tts_import_custom_voice"),
                self._copy("tts_custom_voice_import_done", count=count),
            )
            self._tts_voices_loaded.pop("style_bert_vits2", None)
            self._load_tts_voices()
            self._refresh_tts_runtime_card()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._copy("tts_import_custom_voice"),
                self._copy("tts_custom_voice_import_failed", message=str(exc)),
            )

    def _load_tts_voices_deferred(self) -> None:
        """Deferred TTS voice loading - safe to call after window is shown."""
        self._load_tts_voices_async()

    def _load_tts_voices(self) -> None:
        self._load_tts_voices_async()

    def _load_tts_voices_async(self) -> None:
        engine = self._selected_tts_engine()
        combo = getattr(self, "_tts_voice_combo", None)
        if engine in self._tts_voices_loaded:
            self._apply_tts_voices(engine, self._tts_voices_loaded[engine])
            return
        if self._tts_voices_loading and self._tts_voices_loading_engine == engine:
            return
        if engine == "style_bert_vits2":
            try:
                available = list_style_bert_vits2_voices(self._selected_tts_bert_language())
                entries = self._tts_voice_entries(available)
            except Exception:
                entries = []
            self._tts_voices_loaded[engine] = entries
            self._apply_tts_voices(engine, entries)
            return
        self._tts_voice_load_generation += 1
        generation = self._tts_voice_load_generation
        self._tts_voices_loading = True
        self._tts_voices_loading_engine = engine
        if combo is not None:
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItem(self._copy("tts_voice_loading"))
                combo.setCurrentIndex(0)
            finally:
                combo.blockSignals(False)
        bert_language = self._selected_tts_bert_language() if engine == "style_bert_vits2" else "jp"
        threading.Thread(
            target=self._load_tts_voices_worker,
            args=(engine, generation, bert_language),
            daemon=True,
            name=f"tts-voice-load-{engine}",
        ).start()

    def _load_tts_voices_worker(self, engine: str, generation: int, bert_language: str = "jp") -> None:
        try:
            if engine == "style_bert_vits2":
                available = list_style_bert_vits2_voices(bert_language)
            else:
                tts = create_tts_engine(engine)
                available = tts.get_available_voices() or []
            entries = self._tts_voice_entries(available)
        except Exception:
            entries = []
        self._call_in_ui(
            lambda e=entries, eng=engine, gen=generation: self._on_tts_voices_loaded(eng, e, gen)
        )

    @staticmethod
    def _tts_voice_entries(available) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for i, v in enumerate(available or []):
            voice_id = getattr(v, "id", None) or str(i)
            display_name = getattr(v, "name", None) or voice_id
            entries.append((str(display_name), str(voice_id)))
        return entries

    def _on_tts_voices_loaded(self, engine: str, entries: list, generation: int) -> None:
        self._tts_voices_loaded[engine] = entries
        if generation == self._tts_voice_load_generation and engine == self._tts_voices_loading_engine:
            self._tts_voices_loading = False
            self._tts_voices_loading_engine = None
        if engine == self._selected_tts_engine():
            self._apply_tts_voices(engine, entries)

    def _apply_tts_voices(self, engine: str, entries: list) -> None:
        preferred = self._tts_voice_var.value().strip()
        choices = [e[0] for e in entries] if entries else [self._copy("tts_voice_none")]
        display_to_id = {str(d): str(vid) for d, vid in entries}
        id_to_display: dict[str, str] = {}
        for d, vid in entries:
            id_to_display.setdefault(str(vid), str(d))
        selected_display = choices[0]
        if preferred in display_to_id:
            selected_display = preferred
        elif preferred in id_to_display:
            selected_display = id_to_display[preferred]
        self._tts_voice_display_to_id.clear()
        self._tts_voice_display_to_id.update(display_to_id)
        self._tts_voice_var.set(selected_display)
        combo = getattr(self, "_tts_voice_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(choices)
        combo.setCurrentText(selected_display)
        combo.blockSignals(False)

    def _on_tts_test(self) -> None:
        if self._tts_testing:
            return
        self._stop_tts_test_manager()
        self._tts_test_generation += 1
        generation = self._tts_test_generation
        self._set_tts_testing(True)
        test_text = self._selected_tts_test_text()
        engine = self._selected_tts_engine()
        sbv2_bert_language = self._selected_tts_bert_language()
        try:
            if engine == "style_bert_vits2":
                sbv2_model_id = style_bert_bert_model_id(sbv2_bert_language)
                if not model_is_complete(sbv2_model_id):
                    language_label = self._style_bert_language_label(sbv2_bert_language)
                    message = tr(self._ui_lang, "tts_bert_model_info").format(
                        language=language_label,
                        model_id=sbv2_model_id,
                    )
                    logger.warning(
                        "TTS test blocked: missing Style-Bert-VITS2 BERT model "
                        "(language=%s model_id=%s voice=%s)",
                        sbv2_bert_language,
                        sbv2_model_id,
                        self._selected_tts_voice_id() or "<default>",
                    )
                    self._refresh_bert_model_prompt()
                    self.tts_test_finished.emit(generation, False, message)
                    QMessageBox.warning(self, tr(self._ui_lang, "tts_test"), message)
                    return
            self._tts_test_manager = TTSManager(
                engine_name=engine,
                cache_enabled=False,
                allow_fallback=False,
                sbv2_device=self._tts_device_codes.get(self._tts_device_var.value(), "cpu"),
                sbv2_bert_language=sbv2_bert_language,
                engine_config=self._current_tts_engine_config(engine),
            )
            if not self._tts_test_manager.is_available():
                message = tr(self._ui_lang, "tts_engine_unavailable", engine=engine)
                logger.warning(
                    "TTS test engine unavailable (engine=%s sbv2_bert_language=%s)",
                    engine,
                    sbv2_bert_language,
                )
                self.tts_test_finished.emit(generation, False, message)
                QMessageBox.warning(self, tr(self._ui_lang, "tts_test"), message)
                return
            self._tts_test_manager.start()
            voice = self._selected_tts_voice_id()
            if self._is_tts_placeholder(voice):
                voice = ""
            voice_id = str(voice) if voice else ""
            timeout_ms = self._current_tts_test_timeout_ms(engine)
            self._active_tts_test_timeout_ms = timeout_ms
            logger.info(
                "TTS test started (engine=%s voice=%s test_language=%s sbv2_bert_language=%s timeout_ms=%d)",
                engine,
                voice_id or "<default>",
                self._selected_tts_test_language(),
                sbv2_bert_language,
                timeout_ms,
            )
            self._tts_test_timeout_timer.start(timeout_ms)

            def on_done(success: bool, message: str, *, test_generation: int = generation) -> None:
                self.tts_test_finished.emit(
                    test_generation,
                    bool(success),
                    str(message or ""),
                )

            accepted = self._tts_test_manager.speak(
                test_text,
                voice=voice_id,
                rate=float(self._tts_rate_var.value()),
                volume=float(self._tts_volume_var.value()),
                callback=on_done,
            )
            if not accepted:
                self.tts_test_finished.emit(generation, False, "TTS test request was not accepted")
        except Exception as e:
            logger.warning("TTS test failed: %s", e)
            self.tts_test_finished.emit(generation, False, str(e))

    def _on_tts_stop(self) -> None:
        if self._tts_test_manager:
            self._tts_test_manager.stop_playback()
        if self._tts_testing:
            self._finish_tts_test(self._tts_test_generation, False, "TTS test stopped")

    def _set_tts_testing(self, testing: bool) -> None:
        self._tts_testing = bool(testing)
        self._refresh_tts_test_button()

    def _refresh_tts_test_button(self) -> None:
        button = getattr(self, "_tts_test_btn", None)
        if button is None:
            return
        button.setText(tr(self._ui_lang, "tts_testing" if self._tts_testing else "tts_test"))
        button.setEnabled(not self._tts_testing)
        self._fit_button_to_text(button, min_width=82, height=34, padding=30)

    def _on_tts_test_timeout(self) -> None:
        if not self._tts_testing:
            return
        generation = self._tts_test_generation
        timeout_ms = int(getattr(self, "_active_tts_test_timeout_ms", self._tts_test_timeout_ms))
        logger.warning("TTS test timed out after %.1f seconds", timeout_ms / 1000.0)
        if self._tts_test_manager:
            try:
                self._tts_test_manager.stop_playback()
            except Exception:
                logger.debug("Failed to stop timed-out TTS playback", exc_info=True)
        self._finish_tts_test(generation, False, "TTS test timed out")

    def _finish_tts_test(self, generation: int, success: bool, message: str) -> None:
        if generation != self._tts_test_generation or not self._tts_testing:
            return
        self._tts_test_timeout_timer.stop()
        self._set_tts_testing(False)
        self._stop_tts_test_manager()
        if success:
            logger.info("TTS test finished successfully")
        else:
            logger.warning("TTS test finished without playback success: %s", message or "unknown error")

    def _stop_tts_test_manager(self) -> None:
        manager = self._tts_test_manager
        self._tts_test_manager = None
        if manager is None:
            return
        try:
            stop_playback = getattr(manager, "stop_playback", None)
            if callable(stop_playback):
                stop_playback()
        except Exception:
            logger.debug("Failed to stop TTS test manager", exc_info=True)

    def _set_update_checking(self, checking: bool) -> None:
        self._update_checking = bool(checking)
        for button in self._check_update_buttons:
            if button is None:
                continue
            button.setEnabled(not self._update_checking)
            button.setText(self._copy("settings_update_checking" if self._update_checking else "settings_check_update"))
            self._fit_button_to_text(
                button,
                min_width=SETTINGS_UPDATE_BUTTON_MIN_WIDTH,
                height=38,
                padding=SETTINGS_UPDATE_BUTTON_PADDING,
            )

    def _on_check_update(self) -> None:
        if self._update_checking:
            return
        self._set_update_checking(True)

        def on_update(info: UpdateInfo | None) -> None:
            if info is not None:
                self.update_check_available.emit(info)

        def on_no_update() -> None:
            self.update_check_no_update.emit()

        def on_error(msg: str) -> None:
            self.update_check_failed.emit(str(msg or "unknown error"))

        try:
            check_for_update(
                on_update,
                on_no_update=on_no_update,
                on_error=on_error,
                max_retries=2,
                retry_delays=(2,),
            )
        except Exception as exc:
            self._set_update_checking(False)
            QMessageBox.warning(
                self,
                self._copy("settings_check_update_failed"),
                self._copy("settings_check_update_failed_detail", message=exc),
            )

    def _on_update_check_available(self, info: object) -> None:
        self._set_update_checking(False)
        if not isinstance(info, UpdateInfo):
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "_handle_update_available"):
            try:
                parent._handle_update_available(info)
            except Exception:
                logger.debug("Failed to publish update info to main window", exc_info=True)
        self._open_update_window(info)

    def _on_update_check_no_update(self) -> None:
        self._set_update_checking(False)
        QMessageBox.information(self, self._copy("settings_check_update"), self._copy("settings_up_to_date"))

    def _on_update_check_failed(self, message: str) -> None:
        self._set_update_checking(False)
        QMessageBox.warning(
            self,
            self._copy("settings_check_update_failed"),
            self._copy("settings_check_update_failed_detail", message=message),
        )

    def _open_update_window(self, info: UpdateInfo) -> None:
        from src.ui_qt.update_window import UpdateWindow

        parent = self.parent()
        update_parent = parent if isinstance(parent, QWidget) else self
        self._update_win = UpdateWindow(update_parent, info, self._ui_lang)
        self._update_win.show()
        self._update_win.raise_()
        self._update_win.activateWindow()

    def _set_dictionary_custom_status(self, message: str, *, error: bool = False) -> None:
        if self._dictionary_custom_status_label is None:
            return
        self._dictionary_custom_status_label.setText(message)
        self._set_label_role(self._dictionary_custom_status_label, "errorLabel" if error else "successLabel")

    def _save_dictionary_custom_entry(self) -> None:
        replacement = self._dictionary_custom_replacement_var.value().strip()
        patterns_text = self._dictionary_custom_patterns_var.value().strip()
        if not replacement:
            message = self._copy("settings_dictionary_custom_missing_replacement")
            self._set_dictionary_custom_status(message, error=True)
            QMessageBox.warning(self, tr(self._ui_lang, "error_title"), message)
            return
        if not patterns_text:
            message = self._copy("settings_dictionary_custom_missing_patterns")
            self._set_dictionary_custom_status(message, error=True)
            QMessageBox.warning(self, tr(self._ui_lang, "error_title"), message)
            return

        if self._dictionary_custom_save_button is not None:
            self._dictionary_custom_save_button.setEnabled(False)
        try:
            result = upsert_user_dictionary_entry(replacement, patterns_text)
        except Exception as exc:
            message = self._copy("settings_dictionary_custom_failed", message=str(exc))
            self._set_dictionary_custom_status(message, error=True)
            QMessageBox.critical(self, tr(self._ui_lang, "error_title"), message)
            return
        finally:
            if self._dictionary_custom_save_button is not None:
                self._dictionary_custom_save_button.setEnabled(True)

        total = int(result.get("pattern_count", 0))
        saved_replacement = str(result.get("replacement") or replacement)
        message = self._copy("settings_dictionary_custom_saved", replacement=saved_replacement, total=total)
        self._set_dictionary_custom_status(message)
        self._dictionary_custom_patterns_var.set("")
        if self._dictionary_custom_patterns_edit is not None:
            self._dictionary_custom_patterns_edit.clear()
        self._refresh_dictionary_status()
        QMessageBox.information(self, tr(self._ui_lang, "info_title"), message)

    def _refresh_dictionary_status(self) -> None:
        if self._dictionary_status_label is None:
            return
        status = dictionary_status()
        layer_bits: list[str] = []
        for layer in status.get("layers", []):
            name = str(layer.get("name", "")).capitalize()
            count = int(layer.get("entry_count", 0))
            version = str(layer.get("version", "")).strip()
            layer_bits.append(f"{name}: {count} ({version})" if version else f"{name}: {count}")
        summary = " | ".join(layer_bits) if layer_bits else tr(self._ui_lang, "dictionary_status_empty")
        self._dictionary_status_label.setText(
            tr(
                self._ui_lang,
                "dictionary_status_summary",
                summary=summary,
                path=status.get("user_path", ""),
            )
        )

    def _set_dictionary_updating(self, updating: bool) -> None:
        self._dictionary_updating = updating
        if self._dictionary_update_button is None:
            return
        self._dictionary_update_button.setEnabled(not updating)
        self._dictionary_update_button.setText(
            tr(self._ui_lang, "dictionary_updating") if updating else self._copy("settings_dictionary_update")
        )

    def _start_dictionary_update(self) -> None:
        if self._dictionary_updating:
            return
        self._set_dictionary_updating(True)
        threading.Thread(target=self._run_dictionary_update, daemon=True, name="qt-dictionary-update").start()

    def _run_dictionary_update(self) -> None:
        try:
            result = update_official_dictionary(self._config)
        except Exception as exc:
            self.dictionary_update_failed.emit(str(exc))
            return
        self.dictionary_update_finished.emit(result)

    def _on_dictionary_update_finished(self, result: dict) -> None:
        self._set_dictionary_updating(False)
        self._refresh_dictionary_status()
        if result.get("changed"):
            QMessageBox.information(
                self,
                tr(self._ui_lang, "dictionary_update_done_title"),
                tr(
                    self._ui_lang,
                    "dictionary_update_done_message",
                    count=int(result.get("entry_count", 0)),
                    version=str(result.get("version", "")).strip() or "latest",
                ),
            )
            return
        QMessageBox.information(
            self,
            tr(self._ui_lang, "dictionary_update_done_title"),
            tr(self._ui_lang, "dictionary_update_no_change_message"),
        )

    def _on_dictionary_update_failed(self, message: str) -> None:
        self._set_dictionary_updating(False)
        QMessageBox.critical(self, tr(self._ui_lang, "dictionary_update_failed_title"), message)

    # ----------------------------------------------------------------
    # Save
    # ----------------------------------------------------------------
    def _save(self) -> None:
        cfg = copy.deepcopy(self._config)
        ui_cfg = cfg.setdefault("ui", {})
        trans_cfg = cfg.setdefault("translation", {})
        asr_cfg = cfg.setdefault("asr", {})
        audio_cfg = cfg.setdefault("audio", {})
        tts_cfg = cfg.setdefault("tts", {})
        vrc_cfg = cfg.setdefault("vrc_listen", {})
        hotkey_cfg = cfg.setdefault("hotkeys", {})
        text_input_cfg = cfg.setdefault("text_input_window", {})

        ui_cfg[MAIN_THEME_CONFIG_KEY] = self._theme_code_from_value(self._theme_var.value())
        ui_cfg["background_image_path"] = self._background_image_path

        ui_lang_code = self._ui_lang_codes.get(self._ui_lang_var.value(), self._ui_lang or "zh-CN")
        ui_cfg["language"] = ui_lang_code
        ui_cfg["language_source"] = "manual"

        target_code = self._lang_codes.get(self._target_lang_var.value(), "ja")
        target2_code = self._lang_codes.get(self._target_lang2_var.value(), "en")
        target3_code = self._lang3_codes.get(self._target_lang3_var.value(), "")
        source_code = self._src_codes.get(self._src_lang_var.value(), "auto")
        trans_cfg["target_language"] = target_code
        trans_cfg["target_language_2"] = target2_code
        trans_cfg["target_language_3"] = target3_code
        trans_cfg["source_language"] = source_code
        trans_cfg["language_pair_source"] = "manual"
        trans_cfg["backend_source"] = "manual"
        fallback_text = self._fallback_backends_var.value().strip()
        trans_cfg["fallback_backends"] = [
            item.strip()
            for item in fallback_text.replace(";", ",").split(",")
            if item.strip()
        ]

        backend = self._backend_code()
        trans_cfg["backend"] = backend
        backend_cfg = trans_cfg.setdefault(backend, {})
        if isinstance(backend_cfg, dict):
            backend_cfg["api_key"] = self._backend_api_key_var.value().strip()
            if backend_has_service_regions(backend):
                backend_region = self._selected_qwen_translation_region()
                backend_cfg["region"] = backend_region
                backend_cfg["base_url"] = (
                    get_backend_region_base_url(backend, backend_region)
                    if backend_region != "custom"
                    else self._backend_base_url_var.value().strip().rstrip("/")
                )
            else:
                backend_cfg["base_url"] = self._backend_base_url_var.value().strip() or get_backend_value(backend, "base_url")
            backend_cfg["model"] = self._backend_model_var.value().strip() or get_backend_value(backend, "model")
        fmt_code = self._fmt_codes.get(self._output_format_var.value(), "translated_with_original")
        trans_cfg["output_format"] = normalize_output_format(fmt_code)
        trans_cfg["chatbox_template"] = self._chatbox_template_var.value().strip()
        missing_api_key, backend_label = missing_required_translation_api_key(cfg)
        if missing_api_key:
            QMessageBox.warning(
                self,
                tr(self._ui_lang, "api_missing_title"),
                self._copy("api_missing_save_message", backend=backend_label),
            )
            return

        asr_code = self._asr_codes.get(self._asr_engine_var.value(), DEFAULT_ASR_ENGINE)
        asr_cfg["engine"] = asr_code
        asr_cfg["device"] = (
            self._asr_device_codes.get(self._asr_device_var.value(), "cpu")
            if self._is_local_asr_engine(asr_code)
            else "cpu"
        )
        asr_cfg["engine_source"] = "manual"
        asr_cfg["user_selected_engine"] = True
        qwen_cfg = asr_cfg.setdefault("qwen3_asr", {})
        if isinstance(qwen_cfg, dict):
            qwen_region = self._selected_qwen_region()
            qwen_cfg["api_key"] = self._qwen_api_key_var.value().strip()
            qwen_cfg["region"] = qwen_region
            qwen_cfg["base_url"] = (
                get_qwen3_asr_base_url(qwen_region)
                if qwen_region != "custom"
                else self._qwen_base_url_var.value().strip().rstrip("/")
            )
            qwen_cfg["model"] = self._selected_qwen_model()
            qwen_cfg.setdefault("language", "ja")
            qwen_cfg.setdefault("mode", "vad_chunked")
            qwen_cfg.setdefault("sample_rate", 16000)
            qwen_cfg.setdefault("max_segment_seconds", 6.0)
            qwen_cfg.setdefault("tail_silence_seconds", 0.7)
            qwen_cfg.setdefault("overlap_ms", 300)
            qwen_cfg.setdefault("timeout_seconds", 25)
            qwen_cfg.setdefault("max_retries", 0)
        gemini_cfg = asr_cfg.setdefault("gemini_live", {})
        if isinstance(gemini_cfg, dict):
            gemini_cfg["api_key"] = self._gemini_api_key_var.value().strip()
            gemini_cfg["model"] = self._gemini_model_var.value().strip() or "gemini-3.1-flash-live-preview"
            gemini_cfg.setdefault("language", "ja-JP")
            gemini_cfg.setdefault("transcribe_only", True)
            gemini_cfg.setdefault("timeout_seconds", 20)
            gemini_cfg.setdefault("use_live_api", True)

        try:
            backend_timeout_s = self._parse_float_range(self._backend_timeout_var.value(), self._copy("request_timeout"), 3.0, 120.0)
            backend_retries = int(self._parse_float_range(self._backend_retries_var.value(), self._copy("request_retries"), 0.0, 3.0))
            vad_threshold = self._parse_positive_float(self._vad_var.value(), self._copy("vad_seconds"))
            chunk_interval_ms = self._parse_positive_int(self._chunk_interval_var.value(), self._copy("partial_interval"))
            chunk_window_s = self._parse_positive_float(self._chunk_window_var.value(), self._copy("recognition_window"))
            partial_hits = self._parse_positive_int(self._partial_hits_var.value(), self._copy("partial_hits"))
            listen_self_suppress_seconds = self._parse_positive_float(self._listen_self_suppress_seconds_var.value(), self._copy("self_suppress_seconds"))
            listen_segment_duration_s = self._parse_positive_float(self._listen_segment_duration_var.value(), self._copy("segment_duration"))
            listen_tail_silence_s = self._parse_positive_float(self._listen_tail_silence_var.value(), self._copy("tail_silence"))
            listen_vad_min_rms = self._parse_positive_float(self._listen_vad_min_rms_var.value(), self._copy("listen_vad_min_rms"))
            osc_receive_port = self._parse_positive_int(self._osc_receive_port_var.value(), self._copy("osc_receive_port"))
            if osc_receive_port > 65535:
                raise ValueError(f"{self._copy('osc_receive_port')}: must be between 1 and 65535")
            vad_sensitivity = int(self._parse_float_range(self._vad_sensitivity_var.value(), self._copy("vad_sensitivity"), 0.0, 3.0))
            vad_speech_ratio = self._parse_float_range(self._vad_speech_ratio_var.value(), self._copy("vad_speech_ratio"), 0.0, 1.0)
            vad_activation = self._parse_positive_float(self._vad_activation_threshold_var.value(), self._copy("vad_activation"))
            vad_min_rms = self._parse_positive_float(self._vad_min_rms_var.value(), self._copy("vad_min_rms"))
            min_segment = self._parse_positive_float(self._min_segment_var.value(), self._copy("min_segment"))
            max_segment = self._parse_positive_float(self._max_segment_var.value(), self._copy("max_segment"))
            partial_min_speech = self._parse_positive_float(self._partial_min_speech_var.value(), self._copy("partial_min_speech"))
        except ValueError as exc:
            QMessageBox.warning(self, self._copy("save_failed"), str(exc))
            return

        if isinstance(backend_cfg, dict):
            backend_cfg["timeout_s"] = backend_timeout_s
            backend_cfg["max_retries"] = backend_retries

        if chunk_window_s * 1000 < chunk_interval_ms:
            QMessageBox.warning(self, self._copy("save_failed"), self._copy("recognition_window_too_short"))
            return

        audio_cfg["vad_silence_threshold"] = vad_threshold
        audio_cfg["denoise_strength"] = self._denoise_codes.get(self._denoise_var.value(), 0.0)
        audio_cfg["input_device_mode"] = self._input_mode_codes.get(self._input_device_mode_var.value(), "auto")
        input_device = self._input_device_var.value().strip()
        if input_device in {self._copy("input_device_default"), self._copy("input_device_missing")}:
            input_device = ""
        audio_cfg["input_device"] = input_device or None
        audio_cfg["vad_sensitivity"] = vad_sensitivity
        audio_cfg["vad_speech_ratio"] = vad_speech_ratio
        audio_cfg["vad_activation_threshold_s"] = vad_activation
        audio_cfg["vad_min_rms"] = vad_min_rms
        audio_cfg["min_segment_s"] = min_segment
        audio_cfg["max_segment_s"] = max_segment
        audio_cfg["partial_min_speech_s"] = partial_min_speech

        streaming_cfg = asr_cfg.setdefault("streaming", {})
        if isinstance(streaming_cfg, dict):
            streaming_cfg["chunk_interval_ms"] = chunk_interval_ms
            streaming_cfg["chunk_window_s"] = chunk_window_s
            streaming_cfg["partial_stability_hits"] = partial_hits
            streaming_cfg["ring_buffer_s"] = max(float(streaming_cfg.get("ring_buffer_s", 4.0)), chunk_window_s)
            streaming_cfg.setdefault("recent_speech_hold_s", 0.8)

        tts_engine = self._selected_tts_engine()
        tts_cfg["enabled"] = self._tts_enabled_var.value()
        tts_cfg["engine"] = tts_engine
        tts_cfg["auto_read"] = self._tts_auto_read_var.value()
        tts_cfg["monitor_enabled"] = self._tts_monitor_var.value()
        cfg.setdefault("app_mode", "translation")
        simul_cfg = cfg.setdefault("simul_mode", {})
        if isinstance(simul_cfg, dict):
            simul_cfg["tts_backend"] = tts_engine
        tts_engine_cfg = tts_cfg.setdefault(tts_engine, {})
        if isinstance(tts_engine_cfg, dict):
            tts_voice = self._selected_tts_voice_id()
            if self._is_tts_placeholder(tts_voice):
                tts_voice = self._default_tts_voice(tts_engine)
            tts_engine_cfg["voice"] = tts_voice or None
            tts_engine_cfg["rate"] = self._safe_tts_rate(self._tts_rate_var.value(), engine=tts_engine)
            tts_engine_cfg["volume"] = self._safe_tts_volume(self._tts_volume_var.value())
            if tts_engine in TTS_API_ENGINE_IDS:
                tts_api_region = self._selected_tts_api_region()
                tts_engine_cfg["api_key"] = self._tts_api_key_var.value().strip()
                tts_engine_cfg["region"] = tts_api_region
                tts_engine_cfg["base_url"] = (
                    get_tts_api_base_url(tts_engine, tts_api_region)
                    if tts_api_region != "custom"
                    else self._tts_api_base_url_var.value().strip().rstrip("/")
                )
                tts_engine_cfg["model"] = self._tts_api_model_var.value().strip() or str(get_tts_api_default_value(tts_engine, "model") or "")
        style_cfg = tts_cfg.setdefault("style_bert_vits2", {})
        if isinstance(style_cfg, dict):
            style_cfg["device"] = self._tts_device_codes.get(self._tts_device_var.value(), "cpu")
            style_cfg["bert_language"] = self._selected_tts_bert_language()
        tts_cfg["output_to_vrchat"] = self._tts_output_to_vrchat_var.value()
        if self._tts_output_to_vrchat_var.value():
            resolved = resolve_output_device(
                self._tts_virtual_device_id,
                self._tts_virtual_device_name,
                prefer_virtual=True,
            ) or find_best_virtual_output_device()
            if resolved is not None:
                tts_cfg["output_device"], tts_cfg["output_device_name"] = resolved
            else:
                tts_cfg["output_device"] = None
                tts_cfg["output_device_name"] = ""
        else:
            tts_cfg["output_device"] = None
            tts_cfg["output_device_name"] = ""

        vrc_cfg["enabled"] = self._vrc_listen_enabled_var.value()
        vrc_cfg["show_overlay"] = self._vrc_listen_overlay_var.value()
        vrc_cfg["send_to_chatbox"] = self._vrc_listen_send_var.value()
        loopback_device = self._loopback_device_var.value().strip()
        if loopback_device in {self._copy("vrc_listen_device_default"), self._copy("vrc_listen_device_missing")}:
            loopback_device = ""
        vrc_cfg["loopback_device"] = loopback_device or None
        vrc_cfg["asr_engine"] = self._listen_asr_engine_codes.get(self._listen_asr_engine_var.value(), ASR_ENGINE_FOLLOW_MAIN)
        vrc_cfg["source_language"] = self._listen_src_codes.get(self._vrc_listen_src_var.value(), "auto")
        vrc_cfg["target_language"] = self._listen_lang_codes.get(self._vrc_listen_tgt_var.value(), target_code)
        vrc_cfg["self_suppress"] = self._listen_self_suppress_var.value()
        vrc_cfg["self_suppress_seconds"] = listen_self_suppress_seconds
        vrc_cfg["segment_duration_s"] = listen_segment_duration_s
        vrc_cfg["tail_silence_s"] = listen_tail_silence_s
        vrc_cfg["vad_min_rms"] = listen_vad_min_rms
        trans_cfg["send_to_chatbox"] = self._mic_send_to_chatbox_var.value()

        osc_cfg = cfg.setdefault("osc", {})
        if not isinstance(osc_cfg, dict):
            osc_cfg = {}
            cfg["osc"] = osc_cfg
        osc_cfg["listener_enabled"] = self._osc_listener_enabled_var.value()
        osc_cfg["receive_host"] = self._osc_receive_host_var.value().strip() or "127.0.0.1"
        osc_cfg["receive_port"] = osc_receive_port
        osc_cfg["sync_mute_self"] = self._osc_sync_mute_self_var.value()
        osc_cfg["allow_avatar_control"] = self._osc_allow_avatar_control_var.value()
        osc_cfg["control_prefix"] = self._osc_control_prefix_var.value().strip() or "Mio"
        control_params = osc_cfg.get("control_params")
        if not isinstance(control_params, dict):
            control_params = {}
            osc_cfg["control_params"] = control_params
        control_params["mic"] = self._osc_toggle_mic_var.value().strip() or f"{osc_cfg['control_prefix']}ToggleMic"
        control_params["listen"] = self._osc_toggle_listen_var.value().strip() or f"{osc_cfg['control_prefix']}ToggleListen"
        control_params["tts"] = self._osc_toggle_tts_var.value().strip() or f"{osc_cfg['control_prefix']}ToggleTts"
        control_params["overlay"] = self._osc_toggle_overlay_var.value().strip() or f"{osc_cfg['control_prefix']}ToggleOverlay"
        avatar_cfg = osc_cfg.get("avatar_sync")
        if not isinstance(avatar_cfg, dict):
            avatar_cfg = {}
            osc_cfg["avatar_sync"] = avatar_cfg
        avatar_params = avatar_cfg.get("params")
        if not isinstance(avatar_params, dict):
            avatar_params = {}
            avatar_cfg["params"] = avatar_params
        avatar_cfg["enabled"] = self._avatar_sync_enabled_var.value()
        avatar_params["translating"] = self._avatar_translating_var.value().strip()
        avatar_params["speaking"] = self._avatar_speaking_var.value().strip()
        avatar_params["muted"] = self._avatar_muted_var.value().strip()
        avatar_params["error"] = self._avatar_error_var.value().strip()
        avatar_params["target_language"] = self._avatar_target_language_var.value().strip()

        try:
            hotkey_cfg["mic_mute"] = normalize_hotkey(self._mic_mute_hotkey_var.value())
        except HotkeyError as exc:
            QMessageBox.warning(self, self._copy("hotkey_error"), f"{self._copy('mic_mute_hotkey')}: {exc}")
            return

        try:
            text_input_cfg["hotkey"] = normalize_hotkey(self._text_input_hotkey_var.value())
        except HotkeyError as exc:
            QMessageBox.warning(self, self._copy("hotkey_error"), f"{self._copy('text_input_hotkey')}: {exc}")
            return

        social_cfg = trans_cfg.setdefault("social", {})
        social_cfg["mode"] = "roleplay" if self._roleplay_enabled_var.value() else "standard"
        selected_preset = self._roleplay_preset_codes.get(self._roleplay_preset_var.value(), "custom")
        preset_profile = ROLEPLAY_PRESETS.get(selected_preset, ROLEPLAY_PRESETS["custom"])
        social_cfg["persona_preset"] = selected_preset
        social_cfg["politeness"] = preset_profile.get("politeness", "neutral")
        social_cfg["tone"] = preset_profile.get("tone", "natural")
        social_cfg["persona_name"] = self._persona_name_var.value().strip()
        social_cfg["persona_prompt"] = self._roleplay_prompt_var.value()
        social_cfg["persona_glossary"] = self._roleplay_glossary_var.value()

        ui_cfg.setdefault("osc_guide_seen", False)

        self._pending_save_rollback_config = copy.deepcopy(self._config)
        self._config.clear()
        self._config.update(cfg)
        cfg = self._config

        self._saving = True
        self._set_save_controls_enabled(False)

        def do_save() -> None:
            try:
                config_manager.save_config(cfg)
            except Exception as exc:
                message = str(exc)
                self._call_in_ui(lambda msg=message: self._on_save_error(msg))
                return
            self._call_in_ui(lambda saved_config=cfg: self._on_save_complete(saved_config))

        threading.Thread(target=do_save, daemon=True, name="settings-save").start()

    def _on_save_error(self, error: str) -> None:
        rollback_config = self._pending_save_rollback_config
        self._pending_save_rollback_config = None
        if rollback_config is not None:
            self._config.clear()
            self._config.update(rollback_config)
        self._saving = False
        self._set_save_controls_enabled(True)
        QMessageBox.critical(self, self._copy("save_failed"), self._copy("settings_save_failed_message", error=error))

    def _on_save_complete(self, saved_config: dict | None = None) -> None:
        del saved_config
        self._pending_save_rollback_config = None
        self._saving = False
        self._set_save_controls_enabled(True)
        self._stop_tts_test_manager()
        if self._tts_test_timeout_timer.isActive():
            self._tts_test_timeout_timer.stop()
        self.accept()
        if callable(self._on_close):
            self._on_close()
        QTimer.singleShot(0, self._notify_save_complete)

    def _notify_save_complete(self) -> None:
        self.saved.emit()
        if callable(self._on_save):
            self._on_save()

    def _set_save_controls_enabled(self, enabled: bool) -> None:
        if self._save_btn is not None:
            self._save_btn.setEnabled(bool(enabled))
        if self._cancel_btn is not None:
            self._cancel_btn.setEnabled(bool(enabled))

    def reject(self) -> None:
        self._stop_tts_test_manager()
        if self._tts_test_timeout_timer.isActive():
            self._tts_test_timeout_timer.stop()
        super().reject()

    def _on_cancel(self) -> None:
        if callable(self._on_close):
            self._on_close()
        self.reject()

    def showEvent(self, event) -> None:  # noqa: N802
        self._closing = False
        if not self._saving:
            self._set_save_controls_enabled(True)
        if self._defer_initial_page:
            self._defer_initial_page = False
            QTimer.singleShot(0, self._finish_deferred_initial_page)
        if self._preloaded:
            self._preloaded = False
            QTimer.singleShot(150, self._load_tts_voices_deferred)
            QTimer.singleShot(300, self._maybe_prompt_missing_model_download)
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        """Stop pending timers and suppress Qt warnings when the window is closed."""
        self._closing = True
        if hasattr(self, "_tts_test_timeout_timer") and self._tts_test_timeout_timer.isActive():
            self._tts_test_timeout_timer.stop()
        super().closeEvent(event)

    # ----------------------------------------------------------------
    # Styling
    # ----------------------------------------------------------------
    def _apply_style(self, *, immediate: bool = True) -> None:
        theme = _resolve_theme(self._theme_code_from_value(self._theme_var.value()))
        previous_theme = getattr(self, "_applied_settings_theme", "")
        theme_changed = theme != previous_theme
        self._active_theme = theme
        tokens = theme_tokens(theme)
        # Build stylesheet once and cache; only rebuild when theme actually changes
        cache_key = f"_style_cache_{theme}"
        cached = getattr(self, cache_key, None)
        if cached is None:
            cached = build_settings_window_styles(theme)
            setattr(self, cache_key, cached)
        if getattr(self, "_applied_settings_stylesheet", None) != cached:
            self.setStyleSheet(cached)
            self._applied_settings_stylesheet = cached
        if theme_changed:
            apply_window_chrome_theme(self, theme)
        if self._background_widget is not None:
            self._background_widget.set_theme(theme)
            if getattr(self, "_applied_background_path", None) != self._background_image_path:
                self._background_widget.set_background_path(self._background_image_path)
                self._applied_background_path = self._background_image_path
        self._refresh_theme_button()
        # Defer CapsuleSwitch iteration to next event loop tick to avoid stuttering
        if immediate and theme_changed:
            self._apply_switch_colors_deferred(tokens, theme)
        elif theme_changed:
            QTimer.singleShot(0, lambda: self._apply_switch_colors_deferred(tokens, theme))
        self._applied_settings_theme = theme

    def _apply_switch_colors_deferred(self, tokens, theme: str) -> None:
        switch_track = str(tokens["FIELD_BORDER"] if theme == "dark" else tokens["PANEL_BORDER"])
        switch_border = str(tokens["FIELD_BORDER"])
        switch_accent = str(tokens["ACCENT"])
        self.setUpdatesEnabled(False)
        try:
            for switch in self.findChildren(CapsuleSwitch):
                switch.set_colors(accent=switch_accent, track=switch_track, border=switch_border, thumb="#ffffff")
        finally:
            self.setUpdatesEnabled(True)


class _StrVar:
    def __init__(self, value: str = "") -> None:
        self._value = value

    def value(self) -> str:
        return self._value

    def set(self, v: str) -> None:
        self._value = v


class _BoolVar:
    def __init__(self, value: bool = False) -> None:
        self._value = value

    def value(self) -> bool:
        return self._value

    def set(self, v: bool) -> None:
        self._value = v


class _FloatVar:
    def __init__(self, value: float = 0.0) -> None:
        self._value = value

    def value(self) -> float:
        return self._value

    def set(self, v: float) -> None:
        self._value = v
