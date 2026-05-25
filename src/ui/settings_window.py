# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from src.utils import config_manager
from src.audio.desktop_recorder import (
    list_output_devices as _list_desktop_output_devices,
)
from src.audio.recorder import AudioRecorder
from src.asr.hf_model_downloader import get_downloader, model_is_complete
from src.asr.model_registry import (
    ASR_ENGINE_FOLLOW_MAIN,
    LISTEN_SELECTABLE_ASR_ENGINES,
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_DEFAULT_REGION,
    QWEN3_ASR_MODEL_CHOICES,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
    USER_SELECTABLE_ASR_ENGINES,
)
from src.asr.text_corrections import (
    dictionary_status,
    update_official_dictionary,
    upsert_user_dictionary_entry,
)
from src.tts.factory import create_tts_engine
from src.tts.manager import (
    TTSManager,
    find_best_virtual_output_device,
    resolve_output_device,
)
from src.tts.style_bert_vits2_engine import (
    STYLE_BERT_LANGUAGE_NAMES,
    StyleBertVits2TTS,
    normalize_style_bert_bert_language,
    style_bert_bert_model_id,
    style_bert_runtime_available,
)
from src.tts.style_bert_vits2_models import (
    StyleBertVits2ModelError,
    import_style_bert_model_path,
    list_imported_style_bert_models,
)
from src.updater.update_checker import UpdateInfo, check_for_update
from src.version import APP_VERSION
from src.utils.i18n import tr
from src.utils.global_hotkey import (
    DEFAULT_TEXT_INPUT_HOTKEY,
    HotkeyError,
    normalize_hotkey,
)
from src.utils.ui_config import (
    BACKEND_ORDER,
    DEFAULT_ASR_ENGINE,
    OUTPUT_FORMAT_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    backend_base_url_is_editable,
    get_backend_config_value,
    get_backend_api_key_hint,
    backend_model_is_selectable,
    get_backend_label,
    get_backend_model_hint,
    get_backend_model_options,
    get_backend_model_profile,
    get_manual_source_language_options,
    get_target_language_options,
    get_backend_value,
    get_ui_language,
    normalize_backend,
    normalize_output_format,
)
from .window_effects import apply_window_icon, present_popup

logger = logging.getLogger(__name__)

BG_PRIMARY = "#f5f5f7"
BG_SECONDARY = "#eef1f5"
CARD_BG = "#ffffff"
CARD_BORDER = "#d8dde6"
GLASS_BG = "#f7f8fc"
GLASS_BORDER = "#d7dce4"
GLASS_HOVER = "#eceff5"
ACCENT = "#0071e3"
ACCENT_HOVER = "#0059b8"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#6e6e73"
TEXT_MUTED = "#8e8e93"
SETTINGS_WINDOW_WIDTH = 480
SETTINGS_WINDOW_HEIGHT = 590
SETTINGS_CARD_PADX = 6
SETTINGS_FIELD_PADX = 8
SETTINGS_TEXT_WRAP = 410
SETTINGS_HINT_WRAP = 390
SETTINGS_MODEL_WRAP = 370
SETTINGS_ASR_MENU_WIDTH = 340
TTS_VOICE_DROPDOWN_MAX_HEIGHT = 176
SETTINGS_SCROLL_UNITS_PER_WHEEL = 32
SECTION_ANIMATION_INTERVAL_MS = 16
SECTION_ANIMATION_DURATION_MS = 120
TTS_FEATURE_ENABLED = True

ASR_ENGINE_CODES = (
    *USER_SELECTABLE_ASR_ENGINES,
)
ASR_ENGINE_LABELS = {
    "zh-CN": {
        "webspeech": "Web Speech（在线 / 浏览器）",
        "qwen3-asr": "Qwen3-ASR（在线）",
        "gemini-live": "Gemini Live（在线）",
        "sensevoice-small": "SenseVoice Small（中文 / 粤语）",
    },
    "en": {
        "webspeech": "Web Speech (Online / Browser)",
        "qwen3-asr": "Qwen3-ASR (Online)",
        "gemini-live": "Gemini Live (Online)",
        "sensevoice-small": "SenseVoice Small (Chinese / Cantonese)",
    },
    "ja": {
        "webspeech": "Web Speech（オンライン / ブラウザ）",
        "qwen3-asr": "Qwen3-ASR（オンライン）",
        "gemini-live": "Gemini Live（オンライン）",
        "sensevoice-small": "SenseVoice Small（中国語 / 広東語）",
    },
    "ko": {
        "webspeech": "Web Speech (온라인 / 브라우저)",
        "qwen3-asr": "Qwen3-ASR (온라인)",
        "gemini-live": "Gemini Live (온라인)",
        "sensevoice-small": "SenseVoice Small (중국어 / 광동어)",
    },
    "ru": {
        "webspeech": "Web Speech (онлайн / браузер)",
        "qwen3-asr": "Qwen3-ASR (онлайн)",
        "gemini-live": "Gemini Live (онлайн)",
        "sensevoice-small": "SenseVoice Small (китайский / кантонский)",
    },
}
LISTEN_ASR_ENGINE_LABELS = {
    "zh-CN": {
        ASR_ENGINE_FOLLOW_MAIN: "跟随麦克风语音模型（自动复用）",
    },
    "en": {
        ASR_ENGINE_FOLLOW_MAIN: "Follow Microphone Speech Model (Reuse)",
    },
    "ja": {
        ASR_ENGINE_FOLLOW_MAIN: "マイク音声モデルに合わせる（再利用）",
    },
    "ko": {
        ASR_ENGINE_FOLLOW_MAIN: "마이크 음성 모델 따르기 (재사용)",
    },
    "ru": {
        ASR_ENGINE_FOLLOW_MAIN: "Как у микрофона (повторное использование)",
    },
}
QWEN3_REGION_LABELS = {
    "zh-CN": {
        "singapore": "国际站（新加坡，推荐）",
        "china_mainland": "中国大陆",
        "custom": "自定义 Base URL",
    },
    "en": {
        "singapore": "International (Singapore, Recommended)",
        "china_mainland": "China Mainland",
        "custom": "Custom Base URL",
    },
    "ja": {
        "singapore": "国際版（シンガポール、推奨）",
        "china_mainland": "中国大陸",
        "custom": "カスタム Base URL",
    },
    "ko": {
        "singapore": "국제 사이트 (싱가포르, 권장)",
        "china_mainland": "중국 본토",
        "custom": "사용자 지정 Base URL",
    },
    "ru": {
        "singapore": "International (Singapore, Recommended)",
        "china_mainland": "China Mainland",
        "custom": "Custom Base URL",
    },
}
QWEN3_MODEL_LABELS = {
    "zh-CN": {
        "qwen3-asr-flash": "qwen3-asr-flash（推荐）",
        "qwen3-asr-flash-2026-02-10": "qwen3-asr-flash-2026-02-10（固定版本）",
    },
    "en": {
        "qwen3-asr-flash": "qwen3-asr-flash (Recommended)",
        "qwen3-asr-flash-2026-02-10": "qwen3-asr-flash-2026-02-10 (Pinned)",
    },
    "ja": {
        "qwen3-asr-flash": "qwen3-asr-flash（推奨）",
        "qwen3-asr-flash-2026-02-10": "qwen3-asr-flash-2026-02-10（固定版）",
    },
    "ko": {
        "qwen3-asr-flash": "qwen3-asr-flash (권장)",
        "qwen3-asr-flash-2026-02-10": "qwen3-asr-flash-2026-02-10 (고정 버전)",
    },
    "ru": {
        "qwen3-asr-flash": "qwen3-asr-flash (Recommended)",
        "qwen3-asr-flash-2026-02-10": "qwen3-asr-flash-2026-02-10 (Pinned)",
    },
}
QWEN3_MODEL_HINTS = {
    "zh-CN": {
        "qwen3-asr-flash": "推荐默认模型：面向全语言的高精度快速识别，适合大多数实时场景。",
        "qwen3-asr-flash-2026-02-10": "固定版本：行为更稳定，适合想保持同一识别效果的玩家。",
    },
    "en": {
        "qwen3-asr-flash": "Recommended default: fast, highly accurate recognition across all languages, suitable for most realtime use.",
        "qwen3-asr-flash-2026-02-10": "Pinned version: more stable behavior for players who want consistent results.",
    },
    "ja": {
        "qwen3-asr-flash": "推奨デフォルト。全言語で高速かつ高精度な認識に向いており、多くのリアルタイム用途に適しています。",
        "qwen3-asr-flash-2026-02-10": "固定版。認識挙動をなるべく変えたくない場合に向いています。",
    },
    "ko": {
        "qwen3-asr-flash": "권장 기본 모델입니다. 모든 언어에서 빠르고 정확한 인식을 목표로 하며 대부분의 실시간 사용에 적합합니다.",
        "qwen3-asr-flash-2026-02-10": "고정 버전입니다. 같은 인식 결과를 유지하고 싶을 때 적합합니다.",
    },
    "ru": {
        "qwen3-asr-flash": "Recommended default: fast, highly accurate recognition across all languages, suitable for most realtime use.",
        "qwen3-asr-flash-2026-02-10": "Pinned version: more stable behavior for players who want consistent results.",
    },
}
ASR_SETTINGS_TEXT = {
    "zh-CN": {
        "provider_config": "在线 ASR 配置",
        "api_key": "ASR 密钥（API Key）",
        "region": "服务区域",
        "base_url": "接口地址（自动获取）",
        "model": "Qwen3-ASR 模型",
        "qwen_hint": "Qwen3-ASR 面向全语言的高精度、快速识别；ASR API Key 与翻译 API Key 分开保存，选择区域后会自动填入 Base URL。",
        "gemini_hint": "Gemini 使用单独的 Gemini API Key 进行在线音频转写；不可用时会按设置回退到 SenseVoice。",
        "webspeech_hint": "Web Speech 会打开浏览器桥接页并使用浏览器麦克风权限；它不会识别插件已采集的桌面回环音频，不可用时会回退到 SenseVoice。",
        "sensevoice_hint": "SenseVoice 是本地离线 ASR，中文 / 粤语识别更稳，隐私性好；首次使用前需要下载本地模型。如果下载速度过慢，可以改用 Qwen3-ASR 在线模型。",
        "listen_asr": "反向翻译语音模型",
        "listen_asr_hint": "选择“跟随麦克风语音模型”会复用同一条 ASR 流，降低占用；手动选择具体模型会创建独立 ASR 实例，减少和麦克风抢锁。",
    },
    "en": {
        "provider_config": "Online ASR Settings",
        "api_key": "ASR API Key",
        "region": "Service Region",
        "base_url": "Base URL (Auto)",
        "model": "Qwen3-ASR Model",
        "qwen_hint": "Qwen3-ASR is tuned for fast, highly accurate recognition across all languages. The ASR API key is stored separately from translation keys, and Base URL is filled automatically from the selected region.",
        "gemini_hint": "Gemini uses a separate Gemini API key for online audio transcription and falls back to SenseVoice when unavailable.",
        "webspeech_hint": "Web Speech opens a browser bridge page and uses browser microphone permission. It does not recognize desktop loopback audio captured by the app, and falls back to SenseVoice when unavailable.",
        "sensevoice_hint": "SenseVoice is a local offline ASR model with strong Chinese / Cantonese recognition and better privacy. It requires the local model before first use; if downloads are slow, use the online Qwen3-ASR model.",
        "listen_asr": "Reverse Translation Speech Model",
        "listen_asr_hint": "Follow Microphone Speech Model reuses the same ASR stream to reduce load. Choosing a specific model creates an independent ASR instance and reduces lock contention with the microphone.",
    },
}
TTS_ENGINE_IDS = (
    "edge",
    "gtts",
    "pyttsx3",
    "voicevox",
    "aivis_speech",
    "style_bert_vits2",
)
DEFAULT_TTS_ENGINE = "edge"
MIXLINE_DOWNLOAD_URL = "https://www.logitechg.com/en-us/software/mixline.html"
TTS_DEFAULT_VOICES = {
    "edge": (
        # Chinese (Mandarin) - Top 3 female voices
        "zh-CN-XiaoxiaoNeural",
        "zh-CN-XiaoyiNeural",
        "zh-CN-YunyangNeural",
        # Japanese - Top 3 female voices
        "ja-JP-NanamiNeural",
        "ja-JP-AoiNeural",
        "ja-JP-MayuNeural",
        # English (US) - Top 3 female voices
        "en-US-JennyNeural",
        "en-US-AriaNeural",
        "en-US-MichelleNeural",
    ),
    "gtts": ("zh-CN", "en", "ja", "ko", "ru"),
    "pyttsx3": (),
    "voicevox": (),
    "aivis_speech": (),
    "style_bert_vits2": (),
}
STYLE_BERT_BERT_LANGUAGE_CODES = ("jp", "zh", "en")
TTS_COPY_OVERRIDES = {
    "tts_section": {
        "zh-CN": "同声传译",
        "en": "Simultaneous Interpretation",
        "ja": "同時通訳",
    },
    "tts_subtitle": {
        "zh-CN": "把当前原文或译文朗读出来，并可路由到 VRChat 虚拟麦克风。",
        "en": "Read the current source or translation aloud and optionally route it into VRChat.",
        "ja": "現在の原文または翻訳文を読み上げ、必要に応じて VRChat の仮想マイクへ送ります。",
    },
    "tts_enable": {
        "zh-CN": "启用同声传译",
        "en": "Enable interpretation voice",
        "ja": "同時通訳を有効化",
    },
    "tts_auto_read": {
        "zh-CN": "自动朗读翻译结果",
        "en": "Auto-read translated result",
        "ja": "翻訳結果を自動で読み上げ",
    },
    "tts_monitor": {
        "zh-CN": "监听 TTS 语音",
        "en": "Monitor TTS voice",
        "ja": "TTS 音声をモニター",
        "ru": "Мониторить TTS",
        "ko": "TTS 음성 듣기",
    },
    "tts_monitor_hint": {
        "zh-CN": "开启后会有回声，不建议开启。可以先开启确认自己想要的声线后再关闭。",
        "en": "This can create echo, so it is not recommended for normal use. Turn it on briefly to confirm the voice you want, then turn it off.",
        "ja": "オンにするとエコーが出ることがあるため、通常はおすすめしません。好みの声を確認するときだけ一時的にオンにして、確認後はオフにしてください。",
        "ru": "Может появиться эхо, поэтому обычно включать не рекомендуется. Включите ненадолго, чтобы проверить нужный голос, затем выключите.",
        "ko": "켜면 에코가 생길 수 있어 평소에는 권장하지 않습니다. 원하는 음색을 확인할 때만 잠시 켠 뒤 다시 꺼 주세요.",
    },
    "tts_hint": {
        "zh-CN": "同声传译会朗读译文；“仅原文”输出格式下会朗读原文。Edge TTS 和 Google TTS 需要网络，pyttsx3 可离线使用。",
        "en": "Interpretation voice reads the translation; Original-only mode reads the source text. Edge TTS and Google TTS require network, while pyttsx3 is offline.",
        "ja": "同時通訳は翻訳文を読み上げます。原文のみモードでは原文を読み上げます。Edge TTS と Google TTS はネットワークが必要で、pyttsx3 はオフラインで使えます。",
    },
    "tts_output_device_hint": {
        "zh-CN": "同传语音需要 MixLine 才能进入 VRChat 麦克风。下载安装后，在 MixLine 接入真实麦克风，并在 VRChat 中选择 MixLine 虚拟麦克风。",
        "en": "Install MixLine to route interpretation voice into the VRChat microphone. After installing, add your real mic in MixLine and select the MixLine virtual mic in VRChat.",
        "ja": "同時通訳の音声を VRChat のマイクへ送るには MixLine が必要です。インストール後、MixLine に実マイクを追加し、VRChat で MixLine の仮想マイクを選択してください。",
    },
    "tts_install_virtual_device": {
        "zh-CN": (
            "要让 VRChat 听到同传语音，需要安装 MixLine。\n\n"
            "配置思路：\n"
            "1. 安装并启动 MixLine\n"
            "2. 在 MixLine 里接入你的真实麦克风\n"
            "3. 在本应用开启“输出到 VRChat”，并让 TTS 输出到 MixLine 虚拟输入\n"
            "4. 在 VRChat 中把麦克风选成 MixLine 暴露的虚拟麦克风"
        ),
        "en": (
            "To let VRChat hear the interpretation voice, install MixLine.\n\n"
            "Setup outline:\n"
            "1. Install and start MixLine\n"
            "2. Add your real microphone in MixLine\n"
            "3. Enable Output to VRChat in this app and route TTS to the MixLine virtual input\n"
            "4. In VRChat, choose the virtual microphone exposed by MixLine"
        ),
        "ja": (
            "VRChat に同時通訳の音声を届けるには、MixLine が必要です。\n\n"
            "設定の流れ:\n"
            "1. MixLine をインストールして起動\n"
            "2. MixLine に実マイクを追加\n"
            "3. このアプリで「VRChat に出力」を有効化し、TTS を MixLine の仮想入力へ送る\n"
            "4. VRChat で MixLine が公開する仮想マイクを選択"
        ),
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
    "tts_device": {
        "zh-CN": "推理设备",
        "en": "Inference Device",
        "ja": "推論デバイス",
        "ru": "Устройство вывода",
        "ko": "추론 장치",
    },
    "tts_device_cpu": {
        "zh-CN": "CPU",
        "en": "CPU",
        "ja": "CPU",
        "ru": "CPU",
        "ko": "CPU",
    },
    "tts_device_gpu": {
        "zh-CN": "GPU（需要 NVIDIA 显卡）",
        "en": "GPU (Requires NVIDIA)",
        "ja": "GPU (CUDA)",
        "ru": "GPU (требуется NVIDIA)",
        "ko": "GPU (NVIDIA 필요)",
    },
    "tts_bert_language": {
        "zh-CN": "BERT 模型语言",
        "en": "BERT Model Language",
        "ja": "BERT モデル言語",
        "ru": "Язык BERT модели",
        "ko": "BERT 모델 언어",
    },
    "tts_bert_language_jp": {
        "zh-CN": "日文",
        "en": "Japanese",
        "ja": "日本語",
        "ru": "Japanese",
        "ko": "Japanese",
    },
    "tts_bert_language_zh": {
        "zh-CN": "中文",
        "en": "Chinese",
        "ja": "中国語",
        "ru": "Chinese",
        "ko": "Chinese",
    },
    "tts_bert_language_en": {
        "zh-CN": "英文",
        "en": "English",
        "ja": "英語",
        "ru": "English",
        "ko": "English",
    },
    "tts_bert_language_hint": {
        "zh-CN": "选和音色语言一致的 BERT；同声传译要用它，没装模型时会在下面提示下载。",
        "en": "Choose the BERT language that matches the voice pack text.",
        "ja": "Choose the BERT language that matches the voice pack text.",
        "ru": "Choose the BERT language that matches the voice pack text.",
        "ko": "Choose the BERT language that matches the voice pack text.",
    },
    "tts_bert_model_info": {
        "zh-CN": "同声传译必须先下载 {language} BERT 模型。Style-Bert-VITS2 在合成该语言音色前，需要先下载这个本地模型。\n模型：{model_id}",
        "en": "Simultaneous interpretation requires the {language} BERT model. Style-Bert-VITS2 must download this local model before it can synthesize that voice language.\nModel: {model_id}",
        "ja": "Simultaneous interpretation requires the {language} BERT model. Style-Bert-VITS2 must download this local model before it can synthesize that voice language.\nModel: {model_id}",
        "ru": "Simultaneous interpretation requires the {language} BERT model. Style-Bert-VITS2 must download this local model before it can synthesize that voice language.\nModel: {model_id}",
        "ko": "Simultaneous interpretation requires the {language} BERT model. Style-Bert-VITS2 must download this local model before it can synthesize that voice language.\nModel: {model_id}",
    },
    "tts_bert_download_btn": {
        "zh-CN": "立即下载 BERT 模型",
        "en": "Download BERT Model Now",
        "ja": "BERT モデルを今すぐダウンロード",
        "ru": "Скачать BERT модель сейчас",
        "ko": "지금 BERT 모델 다운로드",
    },
    "tts_bert_already_downloaded": {
        "zh-CN": "已检测到 {language} BERT 模型。",
        "en": "The {language} BERT model is already installed.",
        "ja": "The {language} BERT model is already installed.",
        "ru": "The {language} BERT model is already installed.",
        "ko": "The {language} BERT model is already installed.",
    },
    "tts_bert_downloading": {
        "zh-CN": "正在下载 {language} BERT 模型...",
        "en": "Downloading the {language} BERT model...",
        "ja": "Downloading the {language} BERT model...",
        "ru": "Downloading the {language} BERT model...",
        "ko": "Downloading the {language} BERT model...",
    },
}
DENOISE_PRESET_VALUES = (
    ("off", 0.0),
    ("low", 0.35),
    ("medium", 0.65),
    ("high", 0.9),
)
DENOISE_PRESET_LABELS = {
    "zh-CN": {
        "off": "关闭",
        "low": "低",
        "medium": "中",
        "high": "高",
        "title": "降噪强度",
        "hint": "只影响本地麦克风前处理，用来压低环境噪声和误触发。强度越高，背景音抑制越明显，但很弱的人声也可能被一起压掉。",
    },
    "en": {
        "off": "Off",
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "title": "Denoise Strength",
        "hint": "This only affects local microphone preprocessing. Higher strength suppresses ambient noise more aggressively, but may also weaken very soft speech.",
    },
    "ja": {
        "off": "オフ",
        "low": "弱",
        "medium": "中",
        "high": "強",
        "title": "ノイズ除去強度",
        "hint": "ローカルのマイク前処理だけに作用します。強くするほど環境ノイズと誤反応を抑えますが、小さな声も削られやすくなります。",
    },
    "ru": {
        "off": "Выкл",
        "low": "Низкий",
        "medium": "Средний",
        "high": "Высокий",
        "title": "Сила шумоподавления",
        "hint": "Влияет только на локальную предварительную обработку микрофона. Чем выше значение, тем сильнее подавляется окружающий шум, но очень тихая речь тоже может ослабляться.",
    },
    "ko": {
        "off": "끔",
        "low": "낮음",
        "medium": "중간",
        "high": "높음",
        "title": "노이즈 제거 강도",
        "hint": "로컬 마이크 전처리에만 적용됩니다. 값을 높일수록 주변 소음 억제가 강해지지만, 아주 작은 목소리도 함께 약해질 수 있습니다.",
    },
}

ROLEPLAY_PRESETS: dict[str, dict[str, object]] = {
    "custom": {
        "labels": {
            "zh-CN": "自定义",
            "en": "Custom",
            "ja": "カスタム",
            "ru": "Свое",
            "ko": "사용자 정의",
        },
        "persona_name": "",
        "persona_prompt": "",
        "persona_glossary": "",
        "politeness": "neutral",
        "tone": "natural",
    },
    "cute_companion": {
        "labels": {
            "zh-CN": "软萌陪聊",
            "en": "Cute Companion",
            "ja": "ゆるふわ相棒",
            "ru": "Милый компаньон",
            "ko": "귀여운 동반자",
        },
        "persona_name": "Cute Companion",
        "persona_prompt": "Keep the roleplay voice soft, cute, and playful without changing the original meaning.",
        "persona_glossary": "Use warm reactions when it fits\nKeep lighthearted roleplay flavor",
        "politeness": "casual",
        "tone": "cute",
    },
    "cool_senpai": {
        "labels": {
            "zh-CN": "冷静前辈",
            "en": "Cool Senpai",
            "ja": "クール先輩",
            "ru": "Хладнокровный сэмпай",
            "ko": "쿨한 선배",
        },
        "persona_name": "Cool Senpai",
        "persona_prompt": "Use a calm, composed, slightly aloof in-character style while preserving meaning.",
        "persona_glossary": "Keep replies concise when possible\nAvoid overacting",
        "politeness": "polite",
        "tone": "cool",
    },
    "world_guide": {
        "labels": {
            "zh-CN": "导览主持",
            "en": "World Guide",
            "ja": "案内ガイド",
            "ru": "Гид",
            "ko": "월드 가이드",
        },
        "persona_name": "World Guide",
        "persona_prompt": "Speak like a clear, welcoming guide or host who helps visitors feel comfortable.",
        "persona_glossary": "Prefer clear navigation wording\nKeep friendly host energy",
        "politeness": "polite",
        "tone": "host",
    },
    "energetic_friend": {
        "labels": {
            "zh-CN": "元气好友",
            "en": "Energetic Friend",
            "ja": "元気な友達",
            "ru": "Энергичный друг",
            "ko": "활기찬 친구",
        },
        "persona_name": "Energetic Friend",
        "persona_prompt": "Use a bright, upbeat, friendly casual voice while preserving the exact meaning and not over-expanding.",
        "persona_glossary": "Keep cheerful friend energy\nUse lively reactions only when the source supports them",
        "politeness": "casual",
        "tone": "cheerful",
    },
    "gentle_big_sibling": {
        "labels": {
            "zh-CN": "温柔兄姐",
            "en": "Gentle Big Sibling",
            "ja": "優しい兄姉",
            "ru": "Добрый старший друг",
            "ko": "다정한 형/누나",
        },
        "persona_name": "Gentle Big Sibling",
        "persona_prompt": "Keep a warm, caring, mature voice that feels reassuring without sounding patronizing or changing intent.",
        "persona_glossary": "Use gentle reassurance when appropriate\nStay natural, not melodramatic",
        "politeness": "polite",
        "tone": "warm",
    },
    "teasing_tsundere": {
        "labels": {
            "zh-CN": "傲娇吐槽",
            "en": "Teasing Tsundere",
            "ja": "ツンデレ風",
            "ru": "Цундэрэ с подколами",
            "ko": "츤데레 장난",
        },
        "persona_name": "Teasing Tsundere",
        "persona_prompt": "Use light teasing and mild tsundere flavor when it fits, but keep the line friendly and never turn neutral text hostile.",
        "persona_glossary": "Light teasing is okay\nDo not insult unless the source clearly does",
        "politeness": "casual",
        "tone": "playful",
    },
    "gamer_teammate": {
        "labels": {
            "zh-CN": "游戏队友",
            "en": "Gamer Teammate",
            "ja": "ゲーム仲間",
            "ru": "Игровой напарник",
            "ko": "게임 팀원",
        },
        "persona_name": "Gamer Teammate",
        "persona_prompt": "Sound like a casual game teammate: concise, quick, natural, and comfortable with common gaming or VRChat slang.",
        "persona_glossary": "Prefer short team-callout style when useful\nKeep game terms natural",
        "politeness": "casual",
        "tone": "gamer",
    },
    "polite_interpreter": {
        "labels": {
            "zh-CN": "礼貌翻译员",
            "en": "Polite Interpreter",
            "ja": "丁寧な通訳",
            "ru": "Вежливый переводчик",
            "ko": "정중한 통역사",
        },
        "persona_name": "Polite Interpreter",
        "persona_prompt": "Use a clean, polite interpreter style that is clear and socially safe while staying conversational and concise.",
        "persona_glossary": "Avoid slang unless the source depends on it\nKeep wording respectful and clear",
        "politeness": "very_polite",
        "tone": "clear",
    },
    "catlike_partner": {
        "labels": {
            "zh-CN": "猫娘咖啡厅女仆",
            "en": "Catgirl Cafe Maid",
            "ja": "猫耳カフェメイド",
            "ru": "Кошкодевочка-мейд кафе",
            "ko": "고양이 카페 메이드",
        },
        "persona_name": "Catgirl Cafe Maid",
        "persona_prompt": "Use a cute catgirl cafe maid voice: warm, playful, service-minded, and lightly catlike while preserving the exact meaning. Keep cat puns and maid-style phrasing subtle unless the source already invites it.",
        "persona_glossary": "Keep cute cafe-maid service energy\nUse catlike flavor lightly\nDo not add random meows or honorifics that change meaning",
        "politeness": "polite",
        "tone": "cute",
    },
    "maid_butler": {
        "labels": {
            "zh-CN": "女仆管家",
            "en": "Maid / Butler",
            "ja": "メイド / 執事",
            "ru": "Мейд / дворецкий",
            "ko": "메이드 / 집사",
        },
        "persona_name": "Maid / Butler",
        "persona_prompt": "Keep a formal service-style roleplay tone with respectful wording and in-character elegance.",
        "persona_glossary": "Use service-style phrasing when it fits\nStay polite and composed",
        "politeness": "very_polite",
        "tone": "natural",
    },
}

WINDOW_COPY = {
    "header_title": {
        "zh-CN": "设置",
        "en": "Plugin Settings",
        "ja": "プラグイン設定",
    },
    "header_subtitle": {
        "zh-CN": "常用的放前面，不懂的先别改。",
        "en": "Curated for live translation with an emphasis on low latency, stability, and simple defaults.",
        "ja": "リアルタイム翻訳向けに、遅延と安定性、使いやすさを優先して整理しています。",
    },
    "translation_section": {
        "zh-CN": "常用",
        "en": "Translation",
        "ja": "翻訳",
    },
    "translation_subtitle": {
        "zh-CN": "先在这里改界面语言、AI 服务和发出去的样式。",
        "en": "Choose the backend, target language, and model here. The model notes call out speed and live-plugin suitability.",
        "ja": "翻訳バックエンド、対象言語、モデルをここで設定します。モデルの説明には速度とリアルタイム向きかどうかを表示します。",
    },
    "translation_provider": {
        "zh-CN": "AI 服务",
        "en": "Translation Service",
        "ja": "翻訳サービス",
    },
    "translation_provider_subtitle": {
        "zh-CN": "先选语言和 AI。大多数人只用改这一块。",
        "en": "Choose the translation service, target language, and model here. The model notes call out speed and live-plugin suitability.",
        "ja": "翻訳サービス、対象言語、モデルをここで設定します。モデルの説明には速度とリアルタイム向きかどうかを表示します。",
    },
    "translation_provider_params": {
        "zh-CN": "API 设置",
        "en": "Service Settings",
        "ja": "サービス設定",
    },
    "translation_lock_on": {
        "zh-CN": "现在是“仅原文”，不会调用 AI，下面这些先不用填。",
        "en": "Original-only mode skips translation calls, so the service and API key are locked.",
        "ja": "原文のみモードでは翻訳 API を使わないため、サービスと API Key は固定されます。",
    },
    "voice_section": {
        "zh-CN": "麦克风识别",
        "en": "Speech Input",
        "ja": "音声入力",
    },
    "voice_subtitle": {
        "zh-CN": "一般不用动。识别不稳、环境太吵时再来这里调。",
        "en": "These controls affect live-listening responsiveness, sentence splitting, and noise suppression.",
        "ja": "これらの設定は、リアルタイム音声入力の反応速度、文末判定、ノイズ抑制に影響します。",
    },
    "model_title": {
        "zh-CN": "模型",
        "en": "Model Notes",
        "ja": "モデル情報",
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
    "very_fast": {
        "zh-CN": "极快",
        "en": "Very fast",
        "ja": "とても速い",
    },
    "fast": {
        "zh-CN": "快",
        "en": "Fast",
        "ja": "速い",
    },
    "balanced": {
        "zh-CN": "均衡",
        "en": "Balanced",
        "ja": "バランス",
    },
    "slow": {
        "zh-CN": "偏慢",
        "en": "Slower",
        "ja": "やや遅い",
    },
    "basic": {
        "zh-CN": "基础",
        "en": "Basic",
        "ja": "基本",
    },
    "high": {
        "zh-CN": "高",
        "en": "High",
        "ja": "高い",
    },
    "recommended": {
        "zh-CN": "推荐",
        "en": "Recommended",
        "ja": "推奨",
    },
    "very_recommended": {
        "zh-CN": "非常推荐",
        "en": "Highly Recommended",
        "ja": "とても推奨",
    },
    "general": {
        "zh-CN": "一般",
        "en": "General",
        "ja": "普通",
    },
    "conditional": {
        "zh-CN": "一般",
        "en": "General",
        "ja": "普通",
    },
    "not_recommended": {
        "zh-CN": "不推荐",
        "en": "Not Recommended",
        "ja": "非推奨",
    },
    "live_default": {
        "zh-CN": "更适合长时间实时翻译，延迟和稳定性比较均衡，普通用户直接用这一档通常最省心。",
        "en": "A safe choice for long live-translation sessions with balanced latency and stability.",
        "ja": "長時間のリアルタイム翻訳に向いており、遅延と安定性のバランスが良いです。",
    },
    "balanced_quality": {
        "zh-CN": "比默认档更重视准确率，通常还能保持不错的速度，适合想提一点质量但不想明显变慢的情况。",
        "en": "Leans more toward accuracy while still keeping usable speed for live translation.",
        "ja": "標準より精度寄りですが、リアルタイム用途でもまだ使いやすい速度を保ちます。",
    },
    "quality_first": {
        "zh-CN": "更适合手动翻译或质量优先场景。放到持续监听里时，长句和高频输入会更容易感觉到延迟。",
        "en": "Better for manual or quality-first translation. In continuous live mode, longer lines will feel slower.",
        "ja": "手動翻訳や品質優先向けです。連続リアルタイム用途では遅延を感じやすくなります。",
    },
    "economy_first": {
        "zh-CN": "主打成本和速度，能快速出结果，但翻译稳定性和细节通常不如更高一档的模型。",
        "en": "Optimized for cost and speed first. Good for quick output, but translation quality is usually lower than higher tiers.",
        "ja": "コストと速度を優先したモデルです。出力は速いですが、品質は上位モデルに劣りやすいです。",
    },
    "reasoning": {
        "zh-CN": "推理型模型，通常更仔细，但实时场景下最容易拖慢整体翻译速度，只建议在你明确追求质量时使用。",
        "en": "A reasoning-oriented model. It can be more careful, but it is the easiest way to add latency in live translation.",
        "ja": "推論型モデルで精度重視ですが、リアルタイム翻訳では遅延が増えやすいです。",
    },
    "ultra_fast": {
        "zh-CN": "更偏向抢速度，适合对延迟很敏感的场景。如果你主要想让字幕尽快出来，这类模型会更顺手。",
        "en": "Biases hard toward speed. Useful when showing translated text as quickly as possible matters most.",
        "ja": "速度優先のモデルです。翻訳結果をできるだけ早く出したい場合に向いています。",
    },
    "flash_mt": {
        "zh-CN": "面向机器翻译场景优化，通常比通用聊天模型更适合插件里的实时翻译。",
        "en": "Optimized for machine translation and usually a better fit than general chat models for live plugin use.",
        "ja": "機械翻訳向けに最適化されており、一般的なチャットモデルよりプラグイン用途に向きます。",
    },
    "mt_quality": {
        "zh-CN": "同样是翻译专项模型，但比极速档更偏向质量，适合对翻译自然度更敏感的用户。",
        "en": "Also translation-focused, but leans more toward quality than the fastest tier.",
        "ja": "翻訳特化モデルで、最速帯よりも品質寄りです。",
    },
    "legacy_mt": {
        "zh-CN": "兼容旧配置保留下来的选项。除非你已有固定配额或历史经验，否则通常优先考虑更新的 flash / plus 档。",
        "en": "Kept mainly for compatibility. Newer flash / plus models are usually the better starting point.",
        "ja": "互換性のために残している選択肢です。通常は新しい flash / plus 系から選ぶ方が無難です。",
    },
    "general_high_quality": {
        "zh-CN": "更偏通用大模型路线，适合你想要更完整的表达和更高上限时使用，但实时性通常不如 flash 档。",
        "en": "A more general high-quality model. Better when you want richer phrasing, but usually slower than flash tiers.",
        "ja": "より汎用的な高品質モデルです。表現力は高いですが、flash 系より遅くなりやすいです。",
    },
    "custom": {
        "zh-CN": "这是一个未内置说明的模型。可以继续使用，但插件内的速度和稳定性需要你自行实测。",
        "en": "This model does not have a built-in profile yet. You can still use it, but latency and stability need to be tested manually.",
        "ja": "このモデルにはまだ内蔵の説明がありません。利用はできますが、速度と安定性は実機で確認してください。",
    },
}

WINDOW_COPY.update(
    {
        "rp_section": {
            "zh-CN": "说话风格",
            "en": "RP Mode",
            "ja": "RP モード",
        },
        "rp_subtitle": {
            "zh-CN": "想让翻译更像某种角色说话时再开，不需要就别动。",
            "en": "An optional feature placed at the end. When enabled, translation follows the selected or custom roleplay persona.",
            "ja": "最後に置いた任意機能です。有効にすると、選んだ人設または自定义人設で翻訳口調を調整します。",
        },
        "rp_enabled": {
            "zh-CN": "开启角色说话风格",
            "en": "Enable RP Persona",
            "ja": "RP 人設を有効化",
        },
        "rp_preset": {
            "zh-CN": "预设风格",
            "en": "Persona Preset",
            "ja": "人設プリセット",
        },
        "rp_hint": {
            "zh-CN": "选一个风格就行。下面那些看不懂可以先不改。",
            "en": "Selecting a preset auto-fills the fields below, and you can still edit them into your own persona.",
            "ja": "プリセットを選ぶと下の項目が自動入力されます。その後に自分用へ編集しても構いません。",
        },
        "persona_name": {
            "zh-CN": "人设名称",
            "en": "Persona Name",
            "ja": "人設名",
        },
        "persona_prompt": {
            "zh-CN": "人设提示",
            "en": "Persona Prompt",
            "ja": "人設メモ",
        },
        "persona_glossary": {
            "zh-CN": "人设固定词",
            "en": "Persona Glossary",
            "ja": "人設用語",
        },
        "persona_glossary_hint": {
            "zh-CN": "每行一条，可写口癖、称呼、人设禁忌词或必须保留的表达。",
            "en": "One entry per line for catchphrases, forms of address, forbidden rewrites, or protected wording.",
            "ja": "1 行に 1 件。口癖、呼び方、崩したくない表現、避けたい言い換えを書けます。",
        },
        "desktop_capture_device": {
            "zh-CN": "桌面音频设备",
            "en": "Desktop Output Device",
            "ja": "デスクトップ音声デバイス",
        },
        "desktop_capture_hint": {
            "zh-CN": "主界面 ON/OFF 按钮会使用这里选择的输出设备做回环采集；未选时使用系统默认输出。",
            "en": "The main-window ON/OFF button uses this output device for loopback capture. Leave it on default to follow the system speaker.",
            "ja": "メイン画面の ON/OFF ボタンはここで選んだ出力デバイスをループバック収録に使います。未選択なら既定の出力を使います。",
        },
        "desktop_capture_default": {
            "zh-CN": "系统默认输出",
            "en": "System Default Output",
            "ja": "既定の出力デバイス",
        },
        "vrc_listen_section": {
            "zh-CN": "反向翻译",
            "en": "VRC Listen",
            "ja": "VRC 音声リスン",
        },
        "vrc_listen_subtitle": {
            "zh-CN": "让程序听 VRChat 里的声音，再翻译给你看。",
            "en": "The app will try to find the playback device used by VRChat automatically and translate it into the language you choose.",
            "ja": "通常は VRChat が使っている再生デバイスを自動で見つけて、選んだ言語に翻訳します。",
        },
        "vrc_listen_enabled": {
            "zh-CN": "开启反向翻译",
            "en": "Enable VRC Listen",
            "ja": "VRC 音声リスンを有効化",
        },
        "vrc_listen_overlay": {
            "zh-CN": "显示悬浮窗",
            "en": "Show Overlay",
            "ja": "オーバーレイを表示",
        },
        "vrc_listen_overlay_hint": {
            "zh-CN": "这里管的是默认显示状态。主页上的“悬浮窗”按钮会继续保留，之后仍然可以随时临时开关。",
            "en": "This controls the default overlay visibility. The main-window Overlay button stays available for quick on/off toggles.",
            "ja": "ここでは既定の表示状態を設定します。メイン画面のオーバーレイボタンはそのまま残るので、あとから一時的に切り替えられます。",
        },
        "vrc_listen_send_to_chatbox": {
            "zh-CN": "将反向翻译结果发送到 VRC 聊天框",
            "en": "Send reverse translation to VRC Chatbox",
            "ja": "逆翻訳結果を VRC Chatbox に送信する",
        },
        "vrc_listen_send_to_chatbox_hint": {
            "zh-CN": "关闭后，反向翻译仍会继续识别、翻译并显示在悬浮窗里，只是不再自动发到 VRC 聊天框。",
            "en": "When disabled, reverse translation still appears in the overlay, but it is no longer sent to the VRC Chatbox.",
            "ja": "オフにしても逆翻訳の認識・翻訳とオーバーレイ表示は続きますが、VRC Chatbox には自動送信されません。",
        },
        "vrc_listen_device": {
            "zh-CN": "播放设备",
            "en": "Playback Device",
            "ja": "再生デバイス",
        },
        "vrc_listen_device_default": {
            "zh-CN": "自动检测（推荐）",
            "en": "Auto Detect (Recommended)",
            "ja": "自動検出（推奨）",
        },
        "vrc_listen_device_missing": {
            "zh-CN": "没有找到可用的播放设备",
            "en": "No playback devices found",
            "ja": "使える再生デバイスが見つかりません",
        },
        "vrc_listen_device_hint": {
            "zh-CN": "一般不用改。默认会自己找 VRChat 正在用的耳机或音箱。",
            "en": "By default, the app will try to find the device currently used by VRChat. If it can't, it falls back to your current default playback device. You can also choose a headset or speaker manually.",
            "ja": "通常は VRChat が今使っているデバイスを自動で探します。見つからない場合は、現在の既定の再生デバイスを使います。必要なら手動でヘッドセットやスピーカーを選べます。",
        },
        "vrc_listen_target_language": {
            "zh-CN": "翻成什么语言",
            "en": "Translate To",
            "ja": "翻訳先",
        },
        "vrc_listen_source_language": {
            "zh-CN": "对方在说什么语言",
            "en": "Speaker Language",
            "ja": "相手の言語",
        },
        "vrc_listen_source_language_hint": {
            "zh-CN": "自动识别老出错时，就在这里固定成中文、日语、英语这些。",
            "en": "If auto detection is often wrong, choose a fixed language here such as Chinese, Japanese, or English.",
            "ja": "自動判定が不安定な場合は、ここで中国語、日本語、英語などに固定できます。",
        },
        "vrc_listen_self_suppress": {
            "zh-CN": "开启自声抑制",
            "en": "Enable Self Suppression",
            "ja": "自声抑制を有効化",
        },
        "vrc_listen_self_suppress_hint": {
            "zh-CN": "如果你开了变声器、麦克风监听，或者反向翻译会把自己的原话也识别进去，建议打开这个选项。",
            "en": "Turn this on if a voice changer, mic monitoring, or reverse translation is picking up your own voice.",
            "ja": "ボイスチェンジャーやマイクモニターの影響で自分の声まで拾う場合は、この項目をオンにしてください。",
        },
        "vrc_listen_self_suppress_seconds": {
            "zh-CN": "抑制时长（秒）",
            "en": "Suppression Time (sec)",
            "ja": "抑制時間（秒）",
        },
        "vrc_listen_segment_duration": {
            "zh-CN": "监听截取时长（秒）",
            "en": "Listen Segment Length (sec)",
            "ja": "リスン分割長さ（秒）",
        },
        "vrc_listen_segment_duration_hint": {
            "zh-CN": "持续说话时，每隔这么久就截一段这么长的桌面音频送去识别。调小会更快出字，但句子会更碎。",
            "en": "During continuous speech, desktop audio is cut into slices of this length and sent for recognition. Lower values feel faster, but sentences become more fragmented.",
            "ja": "相手が話し続けている間、この長さごとに同じ長さのデスクトップ音声を切り出して認識へ送ります。短くすると表示は速くなりますが、文は細切れになりやすいです。",
        },
        "vrc_listen_tail_silence": {
            "zh-CN": "尾音判定（秒）",
            "en": "Tail Silence (sec)",
            "ja": "語尾の無音判定（秒）",
        },
        "vrc_listen_tail_silence_hint": {
            "zh-CN": "只影响反向翻译。静音达到这个时长后，当前一句才会被判定结束。它和上面的“监听截取时长”是分开的。",
            "en": "This only affects VRC listen. The current line ends after this much trailing silence. It is separate from the segment length above.",
            "ja": "VRC リスンだけに影響します。この長さだけ無音が続くと、その発話を終了扱いにします。上の分割長さとは別設定です。",
        },
        "vrc_listen_self_suppress_seconds_hint": {
            "zh-CN": "默认 0.65 秒。开了变声器、麦克风监听，或者反向翻译会把自己的原话一起识别进去时，可以适当调大一点；太大则可能把别人刚开口的内容也一起跳过。",
            "en": "Default: 0.65 seconds. Increase this if a voice changer, mic monitoring, or reverse translation is still picking up your own voice. If it's too large, the start of other people's speech may also be skipped.",
            "ja": "初期値は 0.65 秒です。ボイスチェンジャーやマイクモニターの影響で自分の声をまだ拾う場合は少し大きくしてください。大きすぎると、相手が話し始めた直後の音声まで飛ばすことがあります。",
        },
        "avatar_section": {
            "zh-CN": "Avatar 同步",
            "en": "Avatar / OSC",
            "ja": "Avatar / OSC",
        },
        "avatar_subtitle": {
            "zh-CN": "把翻译状态发给 Avatar。用不到就不用改。",
            "en": "Sync translation state to VRChat avatar parameters. Target language is sent as an integer parameter.",
            "ja": "翻訳状態を VRChat の Avatar パラメータへ同期します。対象言語は整数パラメータで送信します。",
        },
        "avatar_sync_enabled": {
            "zh-CN": "启用 Avatar 参数同步",
            "en": "Enable Avatar Sync",
            "ja": "Avatar 同期を有効化",
        },
        "avatar_sync_hint": {
            "zh-CN": "推荐参数：MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
            "en": "Recommended params: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
            "ja": "推奨パラメータ: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
        },
        "avatar_param_translating": {
            "zh-CN": "翻译中参数",
            "en": "Translating Param",
            "ja": "翻訳中パラメータ",
        },
        "avatar_param_speaking": {
            "zh-CN": "说话中参数",
            "en": "Speaking Param",
            "ja": "発話中パラメータ",
        },
        "avatar_param_error": {
            "zh-CN": "错误参数",
            "en": "Error Param",
            "ja": "エラーパラメータ",
        },
        "avatar_param_target_language": {
            "zh-CN": "目标语言参数",
            "en": "Target Language Param",
            "ja": "対象言語パラメータ",
        },
        "tts_section": {
            "zh-CN": "语音阅读",
            "en": "Text-to-Speech",
            "ja": "音声読み上げ",
        },
        "tts_subtitle": {
            "zh-CN": "把当前原文或译文读出来。需要网络的引擎放在前面，离线引擎作为备用。",
            "en": "Read the current original or translated text aloud. Network engines are listed first, with an offline fallback available.",
            "ja": "現在の原文または訳文を読み上げます。オンライン音声を優先し、オフライン音声も予備として使えます。",
        },
        "tts_enable": {
            "zh-CN": "启用语音阅读",
            "en": "Enable text-to-speech",
            "ja": "読み上げを有効化",
        },
        "tts_engine": {
            "zh-CN": "语音引擎",
            "en": "Speech Engine",
            "ja": "音声エンジン",
        },
        "tts_voice": {
            "zh-CN": "音色",
            "en": "Voice",
            "ja": "ボイス",
        },
        "tts_speed": {
            "zh-CN": "语速",
            "en": "Speed",
            "ja": "速度",
        },
        "tts_volume": {
            "zh-CN": "音量",
            "en": "Volume",
            "ja": "音量",
        },
        "tts_auto_read": {
            "zh-CN": "手动翻译完成后自动朗读",
            "en": "Auto-read after manual translation",
            "ja": "手動翻訳後に自動読み上げ",
        },
        "tts_test": {
            "zh-CN": "测试语音",
            "en": "Test Voice",
            "ja": "音声テスト",
        },
        "tts_testing": {
            "zh-CN": "测试中...",
            "en": "Testing...",
            "ja": "テスト中...",
        },
        "tts_hint": {
            "zh-CN": "主界面右上角会显示朗读按钮。输出格式为“仅原文”时读原文，其他格式默认读译文。",
            "en": "The read-aloud button appears in the main window. Original-only mode reads the source text; other modes read the translation.",
            "ja": "メイン画面右上に読み上げボタンが表示されます。原文のみでは原文を、それ以外では訳文を読み上げます。",
        },
        "tts_engine_edge": {
            "zh-CN": "Edge TTS（推荐）",
            "en": "Edge TTS (recommended)",
            "ja": "Edge TTS（推奨）",
        },
        "tts_engine_gtts": {
            "zh-CN": "Google TTS",
            "en": "Google TTS",
            "ja": "Google TTS",
        },
        "tts_engine_pyttsx3": {
            "zh-CN": "pyttsx3（离线）",
            "en": "pyttsx3 (offline)",
            "ja": "pyttsx3（オフライン）",
        },
        "tts_engine_voicevox": {
            "zh-CN": "VOICEVOX（本地）",
            "en": "VOICEVOX (local)",
            "ja": "VOICEVOX（ローカル）",
        },
        "tts_engine_aivis_speech": {
            "zh-CN": "AivisSpeech（本地）",
            "en": "AivisSpeech (local)",
            "ja": "AivisSpeech（ローカル）",
        },
        "tts_engine_style_bert_vits2": {
            "zh-CN": "Style-Bert-VITS2",
            "en": "Style-Bert-VITS2",
            "ja": "Style-Bert-VITS2",
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
        "tts_voice_local_service_unavailable": {
            "zh-CN": "未连接到本地语音引擎，请先启动对应程序",
            "en": "Local speech engine not detected. Start it first.",
            "ja": "ローカル音声エンジンに接続できません。先に起動してください。",
        },
        "tts_voice_style_runtime_missing": {
            "zh-CN": "已找到音色，但推理组件未就绪",
            "en": "Voices found, but the inference runtime is not ready.",
            "ja": "ボイスは見つかりましたが、推論ランタイムが未準備です。",
        },
        "tts_voice_custom_missing": {
            "zh-CN": "还没有可用的自定义音色",
            "en": "No usable custom voice has been imported yet.",
            "ja": "利用できるカスタムボイスがまだありません。",
        },
        "tts_hololive_ready": {
            "zh-CN": "已就绪：该 Hololive 音色包可以直接使用",
            "en": "Ready: this Hololive voice pack can be used now.",
            "ja": "準備完了: この Hololive ボイスパックは使用できます。",
        },
        "tts_hololive_runtime_missing": {
            "zh-CN": "音色包已下载，但推理组件未就绪，暂时还不能测试",
            "en": "Voice pack downloaded, but the inference runtime is not ready yet.",
            "ja": "ボイスパックは取得済みですが、推論ランタイムが未準備です。",
        },
        "tts_hololive_runtime_assets_missing": {
            "zh-CN": "首次使用还需下载共享基础模型，完成后所有 Hololive 音色共用。",
            "en": "First use also needs the shared base model. Every Hololive voice will reuse it.",
            "ja": "初回のみ共有ベースモデルの取得が必要です。すべての Hololive ボイスで共用します。",
        },
        "tts_unavailable": {
            "zh-CN": "当前语音引擎不可用，请确认依赖已安装。",
            "en": "The selected speech engine is unavailable. Check that its dependency is installed.",
            "ja": "選択した音声エンジンを利用できません。依存パッケージを確認してください。",
        },
        "tts_test_failed": {
            "zh-CN": "语音测试失败：{message}",
            "en": "Voice test failed: {message}",
            "ja": "音声テストに失敗しました: {message}",
        },
        "tts_output_device": {
            "zh-CN": "输出到VRChat",
            "en": "Output to VRChat",
            "ja": "VRChatに出力",
        },
        "tts_output_device_hint": {
            "zh-CN": "同传语音需要 MixLine 才能进入 VRChat 麦克风。下载安装后，在 MixLine 接入真实麦克风，并在 VRChat 中选择 MixLine 虚拟麦克风。",
            "en": "Install MixLine to route interpretation voice into the VRChat microphone. After installing, add your real mic in MixLine and select the MixLine virtual mic in VRChat.",
            "ja": "同時通訳の音声を VRChat のマイクへ送るには MixLine が必要です。インストール後、MixLine に実マイクを追加し、VRChat で MixLine の仮想マイクを選択してください。",
        },
        "tts_output_device_status": {
            "zh-CN": "当前设备：{device}",
            "en": "Current device: {device}",
            "ja": "現在のデバイス：{device}",
        },
        "tts_output_device_auto": {
            "zh-CN": "自动检测",
            "en": "Auto-detected",
            "ja": "自動検出",
        },
        "tts_output_device_default": {
            "zh-CN": "系统默认",
            "en": "System Default",
            "ja": "システムデフォルト",
        },
        "tts_no_virtual_device": {
            "zh-CN": "未检测到 MixLine",
            "en": "MixLine Not Detected",
            "ja": "MixLine が検出されません",
        },
        "tts_install_virtual_device": {
            "zh-CN": (
                "要让 VRChat 听到 AI 语音，需要安装 MixLine。\n\n"
                "配置步骤:\n"
                "1. 安装并启动 MixLine\n"
                "2. 在 MixLine 中接入你的真实麦克风\n"
                "3. 在 VRChat 中把麦克风选择为 MixLine 暴露的虚拟麦克风\n"
                "4. 在本应用中开启\"输出到 VRChat\"开关"
            ),
            "en": (
                "To make VRChat hear AI voice, you need to install MixLine.\n\n"
                "Setup steps:\n"
                "1. Install and start MixLine\n"
                "2. Add your real microphone in MixLine\n"
                "3. In VRChat: Set microphone to the virtual microphone exposed by MixLine\n"
                "4. In this app: Enable Output to VRChat"
            ),
            "ja": (
                "VRChatでAI音声を聞かせるには、MixLine のインストールが必要です。\n\n"
                "セットアップ手順:\n"
                "1. MixLine をインストールして起動\n"
                "2. MixLine に実マイクを追加\n"
                "3. VRChatで: MixLine が公開する仮想マイクに設定\n"
                "4. このアプリで: VRChatに出力 スイッチをオン"
            ),
        },
        "tts_download_mixline": {
            "zh-CN": "下载 MixLine",
            "en": "Download MixLine",
            "ja": "MixLine をダウンロード",
        },
        "tts_show_guide": {
            "zh-CN": "安装 MixLine",
            "en": "Install MixLine",
            "ja": "MixLine をインストール",
        },
        "close": {
            "zh-CN": "关闭",
            "en": "Close",
            "ja": "閉じる",
        },
        "settings_app_language": {
            "zh-CN": "界面语言",
            "en": "Interface Language",
            "ja": "表示言語",
        },
        "text_input_hotkey": {
            "zh-CN": "文本悬浮窗快捷键",
            "en": "Floating Text Input Hotkey",
            "ja": "テキスト入力ウィンドウのホットキー",
        },
        "text_input_hotkey_hint": {
            "zh-CN": "全局快捷键，默认 Ctrl+Alt+X。必须包含 Ctrl / Alt / Win 之一，可再加 Shift；留空则关闭快捷键。",
            "en": "Global hotkey. Default: Ctrl+Alt+X. Must include Ctrl, Alt, or Win; Shift is optional. Leave empty to disable.",
            "ja": "グローバルホットキーです。既定は Ctrl+Alt+X。Ctrl / Alt / Win のいずれかが必要で、Shift は任意です。空欄で無効化します。",
        },
        "settings_target_language": {
            "zh-CN": "你想翻成什么语言",
            "en": "Target Language",
            "ja": "翻訳したい言語",
        },
        "settings_output_format": {
            "zh-CN": "发到聊天框的样式",
            "en": "Chatbox Style",
            "ja": "チャット欄の表示",
        },
        "settings_output_format_hint": {
            "zh-CN": "译文（原文）：先发译文，后面带原文\n仅译文：只发翻译结果\n仅原文：不走 AI 翻译，只发原文\n原文（译文）：先发原文，后面带译文",
            "en": "Translation (Original): translated text first, original after it\nTranslation only: only translated text\nOriginal only: no AI translation, send original only\nOriginal (Translation): original text first, translation after it",
            "ja": "訳文（原文）：訳文のあとに原文\n訳文のみ：訳文だけ\n原文のみ：AI 翻訳なしで原文だけ\n原文（訳文）：原文のあとに訳文",
        },
        "settings_send_to_chatbox": {
            "zh-CN": "将麦克风翻译结果发送到 VRC 聊天框",
            "en": "Send microphone translation to VRC Chatbox",
            "ja": "マイク翻訳結果を VRC Chatbox に送信する",
        },
        "settings_send_to_chatbox_hint": {
            "zh-CN": "关闭后，麦克风识别和翻译仍会继续显示在主界面里，只是不再自动发到 VRC。你仍然可以手动点发送。",
            "en": "When disabled, microphone recognition and translation still appear locally, but they are no longer auto-sent to VRC. You can still send manually.",
            "ja": "オフにしてもマイクの認識と翻訳は画面に表示されますが、VRC への自動送信だけ止まります。手動送信は引き続き使えます。",
        },
        "settings_asr_backend": {
            "zh-CN": "语音模型",
            "en": "Speech Model",
            "ja": "音声モデル",
        },
        "settings_asr_hint": {
            "zh-CN": "一般不用改，这个版本固定用这一套。",
            "en": "You usually don't need to change this in this build.",
            "ja": "このバージョンでは基本そのままで大丈夫です。",
        },
        "settings_streaming": {
            "zh-CN": "识别速度",
            "en": "Recognition Speed",
            "ja": "認識スピード",
        },
        "settings_partial_refresh_interval": {
            "zh-CN": "刷新间隔（毫秒）",
            "en": "Refresh Interval (ms)",
            "ja": "更新間隔（ms）",
        },
        "settings_recognition_window_length": {
            "zh-CN": "识别窗口（秒）",
            "en": "Recognition Window (s)",
            "ja": "認識ウィンドウ（秒）",
        },
        "settings_partial_hits": {
            "zh-CN": "稳定后再显示次数",
            "en": "Stable Hits",
            "ja": "安定判定回数",
        },
        "settings_streaming_hint": {
            "zh-CN": "想更快出字就把刷新间隔调小一点；电脑压力会更大。看不懂就保持默认。",
            "en": "Lower values update text faster, but use more CPU. If unsure, keep the default.",
            "ja": "小さくすると文字は早く出ますが、PC 負荷は上がります。迷ったら初期値のままで大丈夫です。",
        },
        "settings_vad": {
            "zh-CN": "停顿判定",
            "en": "Pause Detection",
            "ja": "無音判定",
        },
        "settings_vad_seconds": {
            "zh-CN": "停多久算一句说完（秒）",
            "en": "Silence Before Sentence End (s)",
            "ja": "何秒止まったら一区切りか",
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
            "zh-CN": "第一栏填希望识别成的标准词，第二栏填 ASR 经常听错的词。多个错词可用逗号、分号或换行分隔。",
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
        "settings_input_device": {
            "zh-CN": "麦克风设备",
            "en": "Microphone Device",
            "ja": "マイクデバイス",
        },
        "settings_input_device_mode": {
            "zh-CN": "设备选择模式",
            "en": "Device Selection Mode",
            "ja": "デバイス選択モード",
        },
        "settings_input_device_mode_auto": {
            "zh-CN": "自动（推荐）",
            "en": "Auto (Recommended)",
            "ja": "自動（推奨）",
        },
        "settings_input_device_mode_fixed": {
            "zh-CN": "固定设备",
            "en": "Fixed Device",
            "ja": "固定デバイス",
        },
        "settings_input_device_hint": {
            "zh-CN": "自动模式会跟随系统默认麦克风；固定模式锁定到指定设备。一般用自动就行。",
            "en": "Auto mode follows the system default microphone; Fixed mode locks to a specific device. Auto is usually fine.",
            "ja": "自動モードはシステム既定のマイクに追従します。固定モードは指定デバイスに固定します。通常は自動で大丈夫です。",
        },
        "settings_input_device_default": {
            "zh-CN": "系统默认麦克风",
            "en": "System Default Microphone",
            "ja": "システム既定のマイク",
        },
        "settings_input_device_missing": {
            "zh-CN": "没有找到可用的麦克风设备",
            "en": "No microphone devices found",
            "ja": "使えるマイクデバイスが見つかりません",
        },
        "settings_vad_advanced": {
            "zh-CN": "高级 VAD 设置",
            "en": "Advanced VAD Settings",
            "ja": "高度な VAD 設定",
        },
        "settings_vad_sensitivity": {
            "zh-CN": "VAD 灵敏度（0-3）",
            "en": "VAD Sensitivity (0-3)",
            "ja": "VAD 感度（0-3）",
        },
        "settings_vad_sensitivity_hint": {
            "zh-CN": "0 最不敏感，3 最敏感。默认 2。太敏感会误触发，太低会漏掉轻声。",
            "en": "0 is least sensitive, 3 is most sensitive. Default is 2. Too high causes false triggers, too low misses soft speech.",
            "ja": "0 が最も鈍感、3 が最も敏感です。既定は 2。高すぎると誤反応、低すぎると小声を逃します。",
        },
        "settings_vad_speech_ratio": {
            "zh-CN": "语音比例阈值（0.0-1.0）",
            "en": "Speech Ratio Threshold (0.0-1.0)",
            "ja": "音声比率しきい値（0.0-1.0）",
        },
        "settings_vad_speech_ratio_hint": {
            "zh-CN": "窗口内多少比例的帧被判定为语音时才算真正在说话。默认 0.6。",
            "en": "What fraction of frames must be speech to count as talking. Default is 0.6.",
            "ja": "ウィンドウ内で何割のフレームが音声と判定されたら発話中とみなすか。既定は 0.6。",
        },
        "settings_vad_activation_threshold": {
            "zh-CN": "激活阈值（秒）",
            "en": "Activation Threshold (s)",
            "ja": "起動しきい値（秒）",
        },
        "settings_vad_activation_threshold_hint": {
            "zh-CN": "连续多久的语音才开始录制。默认 0.2 秒。太短容易误触发。",
            "en": "How long speech must continue before recording starts. Default is 0.2s. Too short causes false starts.",
            "ja": "何秒連続で音声が続いたら録音を開始するか。既定は 0.2 秒。短すぎると誤起動します。",
        },
        "settings_vad_min_rms": {
            "zh-CN": "最小音量（RMS）",
            "en": "Minimum Volume (RMS)",
            "ja": "最小音量（RMS）",
        },
        "settings_vad_min_rms_hint": {
            "zh-CN": "低于这个音量的音频会被忽略。默认 0.012。环境太吵可以调高一点。",
            "en": "Audio below this volume is ignored. Default is 0.012. Raise it if the environment is too noisy.",
            "ja": "この音量以下の音声は無視されます。既定は 0.012。環境が騒がしい場合は上げてください。",
        },
        "settings_segment_length": {
            "zh-CN": "音频分段长度",
            "en": "Audio Segment Length",
            "ja": "音声セグメント長",
        },
        "settings_min_segment": {
            "zh-CN": "最短分段（秒）",
            "en": "Minimum Segment (s)",
            "ja": "最短セグメント（秒）",
        },
        "settings_min_segment_hint": {
            "zh-CN": "一句话至少要录多久才会发送识别。默认 0.45 秒。太短会产生很多碎片。",
            "en": "Minimum recording length before sending for recognition. Default is 0.45s. Too short creates many fragments.",
            "ja": "認識に送る前に最低何秒録音するか。既定は 0.45 秒。短すぎると断片が増えます。",
        },
        "settings_max_segment": {
            "zh-CN": "最长分段（秒）",
            "en": "Maximum Segment (s)",
            "ja": "最長セグメント（秒）",
        },
        "settings_max_segment_hint": {
            "zh-CN": "一句话最多录多久就强制切断。默认 6 秒。太长会导致识别延迟。",
            "en": "Maximum recording length before forced cutoff. Default is 6s. Too long causes recognition delay.",
            "ja": "最大何秒録音したら強制的に切るか。既定は 6 秒。長すぎると認識遅延が起きます。",
        },
        "settings_partial_min_speech": {
            "zh-CN": "部分结果最小语音（秒）",
            "en": "Partial Min Speech (s)",
            "ja": "部分結果最小音声（秒）",
        },
        "settings_partial_min_speech_hint": {
            "zh-CN": "实时识别时，至少要有多久的语音才显示部分结果。默认 0.45 秒。",
            "en": "Minimum speech duration before showing partial results during live recognition. Default is 0.45s.",
            "ja": "リアルタイム認識時、何秒以上の音声があれば部分結果を表示するか。既定は 0.45 秒。",
        },
        "settings_check_update": {
            "zh-CN": "检查更新",
            "en": "Check for Updates",
            "ja": "更新を確認",
        },
        "settings_checking_update": {
            "zh-CN": "���在检查…",
            "en": "Checking…",
            "ja": "確認中…",
        },
        "settings_up_to_date": {
            "zh-CN": "已是最新版本",
            "en": "You're up to date",
            "ja": "最新版です",
        },
        "settings_check_update_failed": {
            "zh-CN": "检查失败，请稍后重试",
            "en": "Check failed, try again later",
            "ja": "確認に失敗しました。後で再試行してください",
        },
    }
)


def _extend_window_copy_language(language: str, entries: dict[str, str]) -> None:
    for key, text in entries.items():
        WINDOW_COPY.setdefault(key, {})[language] = text


_extend_window_copy_language(
    "ru",
    {
        "header_title": "Настройки",
        "header_subtitle": "Сверху только самое нужное. Если не уверены, лучше не менять.",
        "translation_section": "Основное",
        "translation_subtitle": "Здесь меняются язык интерфейса, AI-сервис и вид отправки в чат.",
        "translation_provider": "AI сервис",
        "translation_provider_subtitle": "Сначала выберите язык и AI. Большинству людей хватает только этого блока.",
        "translation_provider_params": "API настройки",
        "translation_lock_on": "Сейчас выбран режим «только оригинал», AI не вызывается, поля ниже можно не заполнять.",
        "voice_section": "Микрофон",
        "voice_subtitle": "Обычно это трогать не нужно. Возвращайтесь сюда только если распознавание нестабильно или вокруг шумно.",
        "model_title": "Модель",
        "speed": "Скорость",
        "quality": "Качество",
        "fit": "Совет",
        "very_fast": "Очень быстро",
        "fast": "Быстро",
        "balanced": "Баланс",
        "slow": "Медленно",
        "basic": "Базовое",
        "high": "Высокое",
        "recommended": "Рекомендуется",
        "very_recommended": "Очень рекомендуется",
        "general": "Обычное",
        "conditional": "Обычное",
        "not_recommended": "Не советуем",
        "live_default": "Хороший вариант для долгого живого перевода: задержка и стабильность сбалансированы.",
        "balanced_quality": "Больше упора на точность, но скорость для живого перевода обычно остается нормальной.",
        "quality_first": "Лучше для ручного перевода или когда важнее качество. В постоянном режиме длинные фразы будут медленнее.",
        "economy_first": "Упор на цену и скорость. Результат выходит быстро, но качество обычно ниже.",
        "reasoning": "Модель с рассуждением. Может быть аккуратнее, но сильнее всего добавляет задержку в реальном времени.",
        "ultra_fast": "Максимальный упор на скорость. Подходит, если важнее всего вывести перевод как можно раньше.",
        "flash_mt": "Оптимизировано для машинного перевода и обычно лучше подходит для живого перевода в плагине.",
        "mt_quality": "Тоже переводческая модель, но сильнее упирается в качество, чем в максимальную скорость.",
        "legacy_mt": "Оставлено в основном для совместимости. Обычно лучше начинать с новых flash / plus моделей.",
        "general_high_quality": "Более универсальная качественная модель. Формулировки богаче, но она обычно медленнее flash-серий.",
        "custom": "Для этой модели нет встроенного описания. Использовать можно, но скорость и стабильность нужно проверять вручную.",
        "rp_section": "Стиль речи",
        "rp_subtitle": "Включайте только если хотите, чтобы перевод звучал как персонаж. Иначе лучше не трогать.",
        "rp_enabled": "Включить стиль персонажа",
        "rp_preset": "Готовый стиль",
        "rp_hint": "Можно просто выбрать стиль. Если не понимаете поля ниже, оставьте как есть.",
        "persona_name": "Имя персонажа",
        "persona_prompt": "Описание стиля",
        "persona_glossary": "Фразы персонажа",
        "persona_glossary_hint": "По одной записи на строку: словечки, обращения, нежелательные замены или выражения, которые нельзя менять.",
        "desktop_capture_device": "Устройство вывода",
        "desktop_capture_hint": "Кнопка на главном экране использует выбранное здесь устройство для loopback-захвата. Если не выбирать, берется системное по умолчанию.",
        "desktop_capture_default": "Системное по умолчанию",
        "vrc_listen_section": "Обратный перевод",
        "vrc_listen_subtitle": "Программа слушает звук из VRChat и переводит его для вас.",
        "vrc_listen_enabled": "Включить обратный перевод",
        "vrc_listen_overlay": "Показывать оверлей",
        "vrc_listen_overlay_hint": "Это задает состояние по умолчанию. Кнопка оверлея на главном экране останется и ее можно будет переключать отдельно.",
        "vrc_listen_send_to_chatbox": "Отправлять результат обратного перевода в VRC Chatbox",
        "vrc_listen_send_to_chatbox_hint": "Если выключить, обратный перевод все равно будет распознаваться, переводиться и показываться в оверлее, но перестанет отправляться в VRC Chatbox.",
        "vrc_listen_device": "Устройство воспроизведения",
        "vrc_listen_device_default": "Автоопределение (рекомендуется)",
        "vrc_listen_device_missing": "Доступных устройств не найдено",
        "vrc_listen_device_hint": "Обычно менять не нужно. По умолчанию программа сама ищет наушники или колонки, которые использует VRChat.",
        "vrc_listen_target_language": "Во что переводить",
        "vrc_listen_source_language": "На каком языке говорит собеседник",
        "vrc_listen_source_language_hint": "Если автоопределение часто ошибается, зафиксируйте язык здесь: китайский, японский, английский и т.д.",
        "vrc_listen_self_suppress": "Подавлять свой голос",
        "vrc_listen_self_suppress_hint": "Включите это, если войсчейнджер, мониторинг микрофона или обратный перевод подхватывают вашу собственную речь.",
        "vrc_listen_self_suppress_seconds": "Время подавления (сек)",
        "vrc_listen_segment_duration": "Длина сегмента прослушивания (сек)",
        "vrc_listen_segment_duration_hint": "Пока собеседник говорит без паузы, программа будет раз в столько секунд вырезать фрагмент такой же длины и отправлять его на распознавание. Меньше значение дает более быстрый вывод, но фразы чаще дробятся.",
        "vrc_listen_tail_silence": "Хвостовая тишина (сек)",
        "vrc_listen_tail_silence_hint": "Влияет только на обратный перевод. После такой длительности тишины текущая фраза считается завершенной. Это отдельная настройка, не связанная с длиной сегмента выше.",
        "vrc_listen_self_suppress_seconds_hint": "По умолчанию 0.65 сек. Увеличьте это значение, если программа все еще цепляет ваш голос. Слишком большое значение может пропускать начало чужой речи.",
        "avatar_section": "Синхронизация Avatar",
        "avatar_subtitle": "Отправляет состояние перевода в Avatar. Если не нужно, можно не трогать.",
        "avatar_sync_enabled": "Включить синхронизацию Avatar",
        "avatar_sync_hint": "Рекомендуемые параметры: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
        "avatar_param_translating": "Параметр перевода",
        "avatar_param_speaking": "Параметр речи",
        "avatar_param_error": "Параметр ошибки",
        "avatar_param_target_language": "Параметр целевого языка",
        "tts_section": "Озвучивание",
        "tts_subtitle": "Прочитать текущий оригинал или перевод вслух. Онлайн-движки идут первыми, офлайн-вариант остается запасным.",
        "tts_enable": "Включить озвучивание",
        "tts_engine": "Голосовой движок",
        "tts_voice": "Голос",
        "tts_speed": "Скорость",
        "tts_volume": "Громкость",
        "tts_auto_read": "Автоматически читать после ручного перевода",
        "tts_test": "Проверить голос",
        "tts_testing": "Проверка...",
        "tts_hint": "Кнопка озвучивания появится в главном окне. В режиме только оригинала читается исходный текст, в остальных режимах - перевод.",
        "tts_engine_edge": "Edge TTS (рекомендуется)",
        "tts_engine_gtts": "Google TTS",
        "tts_engine_pyttsx3": "pyttsx3 (офлайн)",
        "tts_engine_voicevox": "VOICEVOX (локально)",
        "tts_engine_aivis_speech": "AivisSpeech (локально)",
        "tts_engine_style_bert_vits2": "Style-Bert-VITS2",
        "tts_voice_loading": "Загрузка голосов...",
        "tts_voice_none": "Голоса не найдены",
        "tts_voice_local_service_unavailable": "Локальный голосовой движок не найден. Сначала запустите его.",
        "tts_voice_style_runtime_missing": "Голоса найдены, но среда синтеза пока не готова.",
        "tts_voice_custom_missing": "Пока нет доступных пользовательских голосов.",
        "tts_hololive_ready": "Готово: этот Hololive-пакет голосов уже можно использовать.",
        "tts_hololive_runtime_missing": "Пакет голосов скачан, но среда синтеза пока не готова.",
        "tts_hololive_runtime_assets_missing": "Для первого запуска нужен общий базовый пакет. Его используют все голоса Hololive.",
        "tts_unavailable": "Выбранный голосовой движок недоступен. Проверьте, установлена ли зависимость.",
        "tts_test_failed": "Проверка голоса не удалась: {message}",
        "settings_app_language": "Язык интерфейса",
        "settings_target_language": "На какой язык переводить",
        "settings_output_format": "Как отправлять в чат",
        "settings_output_format_hint": "Перевод (Оригинал): сначала перевод, потом оригинал\nТолько перевод: отправлять только перевод\nТолько оригинал: без AI, отправлять только оригинал\nОригинал (Перевод): сначала оригинал, потом перевод",
        "settings_send_to_chatbox": "Отправлять перевод с микрофона в VRC Chatbox",
        "settings_send_to_chatbox_hint": "Если выключить, микрофон все равно будет распознаваться и переводиться в окне программы, но перестанет автоматически отправляться в VRC. Ручная отправка останется доступной.",
        "settings_asr_backend": "Речевая модель",
        "settings_asr_hint": "Обычно менять не нужно, в этой версии используется фиксированный вариант.",
        "settings_streaming": "Скорость распознавания",
        "settings_partial_refresh_interval": "Интервал обновления (мс)",
        "settings_recognition_window_length": "Окно распознавания (сек)",
        "settings_partial_hits": "Сколько раз подтвердить перед показом",
        "settings_streaming_hint": "Чтобы текст появлялся быстрее, уменьшите интервал обновления, но нагрузка на ПК вырастет. Если не уверены, оставьте по умолчанию.",
        "settings_vad": "Пауза",
        "settings_vad_seconds": "Сколько тишины считать концом фразы (сек)",
        "settings_dictionary": "Словарь исправления",
        "settings_dictionary_hint": "Используется только для исправления ошибок распознавания. Обычно приложение не выходит в сеть, загрузка будет только по кнопке обновления.",
        "settings_dictionary_update": "Обновить словарь",
        "settings_dictionary_custom": "Пользовательский словарь",
        "settings_dictionary_custom_hint": "В первом поле укажите правильное слово, во втором — ошибки ASR, которые нужно заменить. Несколько вариантов можно разделять запятыми, точкой с запятой или переносами строк.",
        "settings_dictionary_custom_replacement": "Правильное слово",
        "settings_dictionary_custom_patterns": "Ошибочные варианты",
        "settings_dictionary_custom_patterns_hint": "Повторное сохранение того же правильного слова добавит варианты в существующую запись, а не создаст дубль.",
        "settings_dictionary_custom_save": "Сохранить запись",
        "settings_dictionary_custom_missing_replacement": "Сначала введите правильное слово.",
        "settings_dictionary_custom_missing_patterns": "Введите хотя бы один ошибочный вариант.",
        "settings_dictionary_custom_saved": "«{replacement}» сохранено в пользовательский словарь. Всего ошибочных вариантов: {total}.",
        "settings_dictionary_custom_failed": "Не удалось сохранить пользовательский словарь: {message}",
        "settings_input_device": "Устройство микрофона",
        "settings_input_device_mode": "Режим выбора устройства",
        "settings_input_device_mode_auto": "Авто (рекомендуется)",
        "settings_input_device_mode_fixed": "Фиксированное устройство",
        "settings_input_device_hint": "Авто-режим следует за системным микрофоном по умолчанию; фиксированный режим привязывается к конкретному устройству. Обычно авто достаточно.",
        "settings_input_device_default": "Системный микрофон по умолчанию",
        "settings_input_device_missing": "Микрофоны не найдены",
        "settings_vad_advanced": "Расширенные настройки VAD",
        "settings_vad_sensitivity": "Чувствительность VAD (0-3)",
        "settings_vad_sensitivity_hint": "0 — наименее чувствительно, 3 — наиболее. По умолчанию 2. Слишком высокое вызывает ложные срабатывания, слишком низкое пропускает тихую речь.",
        "settings_vad_speech_ratio": "Порог доли речи (0.0-1.0)",
        "settings_vad_speech_ratio_hint": "Какая доля кадров должна быть речью, чтобы считать, что говорят. По умолчанию 0.6.",
        "settings_vad_activation_threshold": "Порог активации (сек)",
        "settings_vad_activation_threshold_hint": "Как долго должна продолжаться речь перед началом записи. По умолчанию 0.2 сек. Слишком короткое вызывает ложные старты.",
        "settings_vad_min_rms": "Минимальная громкость (RMS)",
        "settings_vad_min_rms_hint": "Звук ниже этой громкости игнорируется. По умолчанию 0.012. Увеличьте, если окружение слишком шумное.",
        "settings_segment_length": "Длина аудиосегмента",
        "settings_min_segment": "Минимальный сегмент (сек)",
        "settings_min_segment_hint": "Минимальная длина записи перед отправкой на распознавание. По умолчанию 0.45 сек. Слишком короткое создает много фрагментов.",
        "settings_max_segment": "Максимальный сегмент (сек)",
        "settings_max_segment_hint": "Максимальная длина записи перед принудительным обрезанием. По умолчанию 6 сек. Слишком длинное вызывает задержку распознавания.",
        "settings_partial_min_speech": "Мин. речь для частичного (сек)",
        "settings_partial_min_speech_hint": "Минимальная длительность речи перед показом частичных результатов при живом распознавании. По умолчанию 0.45 сек.",
        "settings_check_update": "Проверить обновление",
        "settings_checking_update": "Проверка…",
        "settings_up_to_date": "У вас последняя версия",
        "settings_check_update_failed": "Не удалось проверить, попробуйте позже",
    },
)

_extend_window_copy_language(
    "ko",
    {
        "header_title": "설정",
        "header_subtitle": "자주 쓰는 것만 위에 두었습니다. 잘 모르겠으면 건드리지 마세요.",
        "translation_section": "기본",
        "translation_subtitle": "여기서 화면 언어, AI 서비스, 채팅창으로 보낼 형식을 바꿉니다.",
        "translation_provider": "AI 서비스",
        "translation_provider_subtitle": "먼저 언어와 AI를 고르세요. 대부분은 이 블록만 바꾸면 됩니다.",
        "translation_provider_params": "API 설정",
        "translation_lock_on": "지금은 '원문만' 모드라서 AI를 호출하지 않습니다. 아래 항목은 입력하지 않아도 됩니다.",
        "voice_section": "마이크 인식",
        "voice_subtitle": "보통은 건드릴 필요가 없습니다. 인식이 불안정하거나 주변이 시끄러울 때만 조정하세요.",
        "model_title": "모델",
        "speed": "속도",
        "quality": "품질",
        "fit": "추천",
        "very_fast": "매우 빠름",
        "fast": "빠름",
        "balanced": "균형",
        "slow": "느림",
        "basic": "기본",
        "high": "높음",
        "recommended": "추천",
        "very_recommended": "강력 추천",
        "general": "보통",
        "conditional": "보통",
        "not_recommended": "비추천",
        "live_default": "오랜 실시간 번역에 무난한 선택입니다. 지연과 안정성의 균형이 좋습니다.",
        "balanced_quality": "정확도 쪽에 조금 더 무게를 두지만, 실시간 번역에도 보통 충분한 속도를 유지합니다.",
        "quality_first": "수동 번역이나 품질 우선 상황에 더 적합합니다. 계속 듣기에서는 긴 문장이 더 느리게 느껴질 수 있습니다.",
        "economy_first": "비용과 속도를 우선합니다. 결과는 빠르지만 품질은 상위 모델보다 낮을 수 있습니다.",
        "reasoning": "추론형 모델입니다. 더 꼼꼼할 수 있지만 실시간 번역에서는 지연이 가장 크게 늘어납니다.",
        "ultra_fast": "속도를 최우선으로 둔 모델입니다. 번역이 최대한 빨리 뜨는 것이 중요할 때 유용합니다.",
        "flash_mt": "기계 번역에 최적화되어 있어 일반 채팅 모델보다 실시간 번역에 더 잘 맞는 경우가 많습니다.",
        "mt_quality": "번역 특화 모델이지만 가장 빠른 등급보다 품질에 더 무게를 둡니다.",
        "legacy_mt": "주로 호환성 때문에 남아 있는 옵션입니다. 보통은 새로운 flash / plus 모델부터 시작하는 편이 좋습니다.",
        "general_high_quality": "보다 범용적인 고품질 모델입니다. 표현력은 좋지만 flash 계열보다 느린 편입니다.",
        "custom": "이 모델은 내장 설명이 없습니다. 사용할 수는 있지만 속도와 안정성은 직접 확인해야 합니다.",
        "rp_section": "말투",
        "rp_subtitle": "번역을 특정 캐릭터처럼 말하게 하고 싶을 때만 켜세요. 필요 없으면 건드리지 마세요.",
        "rp_enabled": "캐릭터 말투 켜기",
        "rp_preset": "프리셋 말투",
        "rp_hint": "말투만 골라도 충분합니다. 아래 항목이 어렵다면 그대로 두세요.",
        "persona_name": "캐릭터 이름",
        "persona_prompt": "말투 설명",
        "persona_glossary": "고정 표현",
        "persona_glossary_hint": "한 줄에 하나씩 넣으세요. 말버릇, 호칭, 바꾸면 안 되는 표현 등을 적을 수 있습니다.",
        "desktop_capture_device": "출력 장치",
        "desktop_capture_hint": "메인 화면 버튼은 여기서 고른 출력 장치를 루프백 캡처에 사용합니다. 선택하지 않으면 시스템 기본 장치를 따릅니다.",
        "desktop_capture_default": "시스템 기본 장치",
        "vrc_listen_section": "역방향 번역",
        "vrc_listen_subtitle": "VRChat 안의 소리를 듣고, 그 내용을 번역해서 보여 줍니다.",
        "vrc_listen_enabled": "역방향 번역 켜기",
        "vrc_listen_overlay": "오버레이 표시",
        "vrc_listen_overlay_hint": "여기서는 기본 표시 상태만 정합니다. 메인 화면의 오버레이 버튼은 그대로 남아서 나중에 바로 켜고 끌 수 있습니다.",
        "vrc_listen_send_to_chatbox": "역방향 번역 결과를 VRC 채팅창으로 보내기",
        "vrc_listen_send_to_chatbox_hint": "끄면 역방향 번역은 계속 인식·번역되고 오버레이에 표시되지만, VRC 채팅창으로는 자동 전송되지 않습니다.",
        "vrc_listen_device": "재생 장치",
        "vrc_listen_device_default": "자동 감지 (권장)",
        "vrc_listen_device_missing": "사용 가능한 재생 장치를 찾지 못했습니다",
        "vrc_listen_device_hint": "보통은 바꿀 필요가 없습니다. 기본적으로 VRChat이 쓰는 헤드셋이나 스피커를 자동으로 찾습니다.",
        "vrc_listen_target_language": "어떤 언어로 번역할지",
        "vrc_listen_source_language": "상대가 말하는 언어",
        "vrc_listen_source_language_hint": "자동 감지가 자주 틀리면 여기서 중국어, 일본어, 영어처럼 고정하세요.",
        "vrc_listen_self_suppress": "내 목소리 억제",
        "vrc_listen_self_suppress_hint": "보이스체인저, 마이크 모니터링, 역방향 번역 때문에 내 목소리까지 잡힐 때 켜세요.",
        "vrc_listen_self_suppress_seconds": "억제 시간 (초)",
        "vrc_listen_segment_duration": "듣기 분할 길이 (초)",
        "vrc_listen_segment_duration_hint": "상대가 계속 말하는 동안 이 길이만큼의 데스크톱 오디오를 같은 간격으로 잘라 인식에 보냅니다. 값을 줄이면 더 빨리 뜨지만 문장이 더 잘게 나뉩니다.",
        "vrc_listen_tail_silence": "말끝 무음 판정 (초)",
        "vrc_listen_tail_silence_hint": "역방향 번역에만 적용됩니다. 이 시간만큼 조용하면 현재 문장이 끝난 것으로 봅니다. 위의 분할 길이와는 별도 설정입니다.",
        "vrc_listen_self_suppress_seconds_hint": "기본값은 0.65초입니다. 아직도 내 목소리를 잡으면 조금 늘려 보세요. 너무 크면 다른 사람이 막 말하기 시작한 부분도 건너뛸 수 있습니다.",
        "avatar_section": "Avatar 동기화",
        "avatar_subtitle": "번역 상태를 Avatar로 보냅니다. 필요 없으면 건드리지 않아도 됩니다.",
        "avatar_sync_enabled": "Avatar 동기화 켜기",
        "avatar_sync_hint": "권장 파라미터: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
        "avatar_param_translating": "번역 중 파라미터",
        "avatar_param_speaking": "말하는 중 파라미터",
        "avatar_param_error": "오류 파라미터",
        "avatar_param_target_language": "목표 언어 파라미터",
        "tts_section": "음성 읽기",
        "tts_subtitle": "현재 원문이나 번역문을 소리로 읽습니다. 온라인 엔진을 먼저 두고, 오프라인 엔진은 예비로 둡니다.",
        "tts_enable": "음성 읽기 켜기",
        "tts_engine": "음성 엔진",
        "tts_voice": "목소리",
        "tts_speed": "속도",
        "tts_volume": "음량",
        "tts_auto_read": "수동 번역 완료 후 자동 읽기",
        "tts_test": "목소리 테스트",
        "tts_testing": "테스트 중...",
        "tts_hint": "메인 화면 오른쪽 위에 읽기 버튼이 표시됩니다. '원문만' 모드에서는 원문을, 다른 모드에서는 번역문을 읽습니다.",
        "tts_engine_edge": "Edge TTS (추천)",
        "tts_engine_gtts": "Google TTS",
        "tts_engine_pyttsx3": "pyttsx3 (오프라인)",
        "tts_engine_voicevox": "VOICEVOX (로컬)",
        "tts_engine_aivis_speech": "AivisSpeech (로컬)",
        "tts_engine_style_bert_vits2": "Style-Bert-VITS2",
        "tts_voice_loading": "목소리 불러오는 중...",
        "tts_voice_none": "사용 가능한 목소리가 없습니다",
        "tts_voice_local_service_unavailable": "로컬 음성 엔진을 찾지 못했습니다. 먼저 실행하세요.",
        "tts_voice_style_runtime_missing": "음색은 찾았지만 추론 런타임이 아직 준비되지 않았습니다.",
        "tts_voice_custom_missing": "사용 가능한 사용자 음색이 아직 없습니다.",
        "tts_hololive_ready": "준비됨: 이 Hololive 음색 팩을 바로 사용할 수 있습니다.",
        "tts_hololive_runtime_missing": "음색 팩은 내려받았지만 추론 런타임이 아직 준비되지 않았습니다.",
        "tts_hololive_runtime_assets_missing": "처음 한 번은 공용 베이스 모델도 내려받아야 합니다. 모든 Hololive 음색이 함께 사용합니다.",
        "tts_unavailable": "선택한 음성 엔진을 사용할 수 없습니다. 의존성이 설치되어 있는지 확인하세요.",
        "tts_test_failed": "목소리 테스트 실패: {message}",
        "settings_app_language": "화면 언어",
        "settings_target_language": "어떤 언어로 번역할지",
        "settings_output_format": "채팅창에 보내는 형식",
        "settings_output_format_hint": "번역문 (원문): 번역문을 먼저 보내고 뒤에 원문을 붙입니다\n번역문만: 번역 결과만 보냅니다\n원문만: AI 번역 없이 원문만 보냅니다\n원문 (번역문): 원문을 먼저 보내고 뒤에 번역문을 붙입니다",
        "settings_send_to_chatbox": "마이크 번역 결과를 VRC 채팅창으로 보내기",
        "settings_send_to_chatbox_hint": "끄면 마이크 인식과 번역은 화면에 계속 표시되지만, VRC로는 자동 전송되지 않습니다. 수동 전송은 계속 사용할 수 있습니다.",
        "settings_asr_backend": "음성 모델",
        "settings_asr_hint": "보통은 바꿀 필요가 없습니다. 이 버전에서는 이 구성을 고정으로 사용합니다.",
        "settings_streaming": "인식 속도",
        "settings_partial_refresh_interval": "갱신 간격 (ms)",
        "settings_recognition_window_length": "인식 창 길이 (초)",
        "settings_partial_hits": "안정된 뒤 보여줄 횟수",
        "settings_streaming_hint": "글자가 더 빨리 뜨게 하려면 갱신 간격을 줄이세요. 대신 PC 부담이 커집니다. 잘 모르겠으면 기본값을 유지하세요.",
        "settings_vad": "멈춤 판정",
        "settings_vad_seconds": "얼마나 멈추면 한 문장이 끝난 것으로 볼지 (초)",
        "settings_dictionary": "인식 교정 사전",
        "settings_dictionary_hint": "음성 인식 오타를 고치는 데만 사용됩니다. 평소에는 인터넷에 연결하지 않고, 업데이트 버튼을 눌렀을 때만 공식 사전을 받습니다.",
        "settings_dictionary_update": "사전 업데이트",
        "settings_dictionary_custom": "사용자 사전",
        "settings_dictionary_custom_hint": "첫 번째 칸에는 올바른 단어를, 두 번째 칸에는 ASR이 자주 잘못 인식하는 단어를 입력하세요. 여러 항목은 쉼표, 세미콜론, 줄바꿈으로 구분할 수 있습니다.",
        "settings_dictionary_custom_replacement": "올바른 단어",
        "settings_dictionary_custom_patterns": "잘못 인식된 단어",
        "settings_dictionary_custom_patterns_hint": "같은 올바른 단어를 다시 저장하면 중복 항목을 만들지 않고 기존 항목에 추가됩니다.",
        "settings_dictionary_custom_save": "항목 저장",
        "settings_dictionary_custom_missing_replacement": "먼저 올바른 단어를 입력하세요.",
        "settings_dictionary_custom_missing_patterns": "잘못 인식된 단어를 하나 이상 입력하세요.",
        "settings_dictionary_custom_saved": "\"{replacement}\"이 사용자 사전에 저장되었습니다. 현재 잘못 인식된 단어는 총 {total}개입니다.",
        "settings_dictionary_custom_failed": "사용자 사전 저장 실패: {message}",
        "settings_input_device": "마이크 장치",
        "settings_input_device_mode": "장치 선택 모드",
        "settings_input_device_mode_auto": "자동 (권장)",
        "settings_input_device_mode_fixed": "고정 장치",
        "settings_input_device_hint": "자동 모드는 시스템 기본 마이크를 따라갑니다. 고정 모드는 특정 장치에 고정됩니다. 보통은 자동으로 충분합니다.",
        "settings_input_device_default": "시스템 기본 마이크",
        "settings_input_device_missing": "사용 가능한 마이크 장치를 찾을 수 없습니다",
        "settings_vad_advanced": "고급 VAD 설정",
        "settings_vad_sensitivity": "VAD 감도 (0-3)",
        "settings_vad_sensitivity_hint": "0이 가장 둔감하고 3이 가장 민감합니다. 기본값은 2입니다. 너무 높으면 오작동이 발생하고, 너무 낮으면 작은 목소리를 놓칩니다.",
        "settings_vad_speech_ratio": "음성 비율 임계값 (0.0-1.0)",
        "settings_vad_speech_ratio_hint": "말하는 것으로 간주하려면 프레임의 몇 퍼센트가 음성이어야 하는지. 기본값은 0.6입니다.",
        "settings_vad_activation_threshold": "활성화 임계값 (초)",
        "settings_vad_activation_threshold_hint": "녹음이 시작되기 전에 음성이 얼마나 계속되어야 하는지. 기본값은 0.2초입니다. 너무 짧으면 오작동이 발생합니다.",
        "settings_vad_min_rms": "최소 볼륨 (RMS)",
        "settings_vad_min_rms_hint": "이 볼륨 이하의 오디오는 무시됩니다. 기본값은 0.012입니다. 환경이 너무 시끄러우면 높이세요.",
        "settings_segment_length": "오디오 세그먼트 길이",
        "settings_min_segment": "최소 세그먼트 (초)",
        "settings_min_segment_hint": "인식을 위해 전송하기 전 최소 녹음 길이. 기본값은 0.45초입니다. 너무 짧으면 많은 조각이 생성됩니다.",
        "settings_max_segment": "최대 세그먼트 (초)",
        "settings_max_segment_hint": "강제 차단 전 최대 녹음 길이. 기본값은 6초입니다. 너무 길면 인식 지연이 발생합니다.",
        "settings_partial_min_speech": "부분 최소 음성 (초)",
        "settings_partial_min_speech_hint": "실시간 인식 중 부분 결과를 표시하기 전 최소 음성 지속 시간. 기본값은 0.45초입니다.",
        "settings_check_update": "업데이트 확인",
        "settings_checking_update": "확인 중…",
        "settings_up_to_date": "최신 버전입니다",
        "settings_check_update_failed": "확인 실패, 나중에 다시 시도하세요",
    },
)


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


def section_label(parent, text: str) -> None:
    ctk.CTkLabel(
        parent,
        text=text,
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color=TEXT_SEC,
    ).pack(padx=12, pady=(4, 2), anchor="w")


def _asr_engine_options(ui_lang: str) -> list[tuple[str, str]]:
    labels = (
        ASR_ENGINE_LABELS.get(ui_lang)
        or ASR_ENGINE_LABELS.get(ui_lang.split("-", 1)[0])
        or ASR_ENGINE_LABELS["en"]
    )
    return [(labels.get(code, code), code) for code in ASR_ENGINE_CODES]


def _localized_table(table: dict[str, dict[str, str]], ui_lang: str) -> dict[str, str]:
    return (
        table.get(ui_lang)
        or table.get(ui_lang.split("-", 1)[0])
        or table.get("en")
        or {}
    )


def _listen_asr_engine_options(ui_lang: str) -> list[tuple[str, str]]:
    labels = dict(_localized_table(ASR_ENGINE_LABELS, ui_lang))
    labels.update(_localized_table(LISTEN_ASR_ENGINE_LABELS, ui_lang))
    return [
        (labels.get(code, code), code)
        for code in LISTEN_SELECTABLE_ASR_ENGINES
    ]


class SettingsWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        config: dict,
        on_save=None,
        on_close=None,
        preload=False,
        on_listen_state_changed=None,
    ):
        super().__init__(parent)
        logger.info("SettingsWindow: Initializing")
        self._config = config
        self._on_save = on_save
        self._on_close = on_close
        # Optional live-sync hook: invoked when the user toggles a vrc_listen
        # switch so the main window's button can update without waiting for
        # the user to click Save.
        self._on_listen_state_changed = on_listen_state_changed
        self._ui_lang = get_ui_language(config)
        self._preloaded = preload  # 标记是否为预加载模式

        self.title(tr(self._ui_lang, "settings_window_title"))
        apply_window_icon(self)
        self.geometry(f"{SETTINGS_WINDOW_WIDTH}x{SETTINGS_WINDOW_HEIGHT}")
        self._popup_size = (SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT)
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=BG_PRIMARY)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._field_vars: dict[str, ctk.StringVar] = {}
        self._editable_backend_entries: list[ctk.CTkEntry] = []
        self._editable_backend_menus: list[ctk.CTkOptionMenu] = []
        self._readonly_backend_entries: list[ctk.CTkEntry] = []
        self._model_info_title_label: ctk.CTkLabel | None = None
        self._model_info_body_label: ctk.CTkLabel | None = None
        self._model_info_badges: ctk.CTkFrame | None = None
        self._translation_lock_box: ctk.CTkFrame | None = None
        self._translation_lock_label: ctk.CTkLabel | None = None
        self._backend_model_hint_box: ctk.CTkFrame | None = None
        self._backend_model_hint_label: ctk.CTkLabel | None = None
        self._dictionary_status_label: ctk.CTkLabel | None = None
        self._dictionary_update_button: ctk.CTkButton | None = None
        self._dictionary_updating = False
        self._dictionary_custom_replacement_var: ctk.StringVar | None = None
        self._dictionary_custom_patterns_box: ctk.CTkTextbox | None = None
        self._dictionary_custom_save_button: ctk.CTkButton | None = None
        self._dictionary_custom_status_label: ctk.CTkLabel | None = None
        self._persona_prompt_box: ctk.CTkTextbox | None = None
        self._persona_glossary_box: ctk.CTkTextbox | None = None
        self._tts_test_manager: TTSManager | None = None
        self._tts_testing = False
        self._tts_voice_load_generation = 0
        self._tts_engine_available = False
        self._tts_controls: list[object] = []
        self._active_option_popup: tk.Toplevel | None = None
        self._section_cards: list[dict[str, object]] = []
        self._settings_layout_after_id: str | None = None
        self._build()
        logger.info("SettingsWindow: Build complete, preparing to show")
        self.after(1, lambda: self._show_window_with_logging(parent))

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _show_window_with_logging(self, parent) -> None:
        """Show window with logging for debugging"""
        # 如果是预加载模式，不显示窗口
        if self._preloaded:
            self.withdraw()
            logger.info("SettingsWindow: Preloaded, window hidden")
            return

        logger.info("SettingsWindow: Calling present_popup")
        present_popup(self, parent=parent, animate=False)
        logger.info("SettingsWindow: present_popup complete")
        self.after(10, self.grab_set)
        logger.info("SettingsWindow: Window shown")

    def _ui_copy(self, key: str) -> str:
        values = TTS_COPY_OVERRIDES.get(key) or WINDOW_COPY.get(key, {})
        if self._ui_lang in values:
            return values[self._ui_lang]
        base_lang = self._ui_lang.split("-", 1)[0]
        for lang, text in values.items():
            if lang.split("-", 1)[0] == base_lang:
                return text
        return values.get("en", "")

    def _asr_copy(self, key: str) -> str:
        values = ASR_SETTINGS_TEXT.get(self._ui_lang)
        if values is None:
            values = ASR_SETTINGS_TEXT.get(self._ui_lang.split("-", 1)[0])
        if values is None:
            values = ASR_SETTINGS_TEXT["en"]
        return values.get(key, ASR_SETTINGS_TEXT["en"].get(key, key))

    def _build_header_card(self, parent) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color=CARD_BG,
            corner_radius=24,
            border_width=1,
            border_color=CARD_BORDER,
        )
        card.pack(padx=SETTINGS_CARD_PADX, pady=(10, 8), fill="x")

        ctk.CTkLabel(
            card,
            text=self._ui_copy("header_title"),
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(anchor="w", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            card,
            text=self._ui_copy("header_subtitle"),
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_TEXT_WRAP,
        ).pack(anchor="w", padx=12, pady=(0, 6))

        version_row = ctk.CTkFrame(card, fg_color="transparent")
        version_row.pack(anchor="w", padx=12, pady=(0, 10), fill="x")

        ctk.CTkLabel(
            version_row,
            text=f"v{APP_VERSION}",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="left")

        self._check_update_btn = ctk.CTkButton(
            version_row,
            text=self._ui_copy("settings_check_update"),
            fg_color="transparent",
            hover_color=GLASS_HOVER,
            corner_radius=8,
            height=24,
            width=10,
            text_color=ACCENT,
            font=ctk.CTkFont(size=11),
            command=self._start_check_update,
        )
        self._check_update_btn.pack(side="left", padx=(8, 0))

    def _bind_section_toggle(self, state: dict[str, object], *widgets) -> None:
        for widget in widgets:
            widget.bind("<Button-1>", lambda _event, s=state: self._toggle_section_card(s))

    @staticmethod
    def _section_ease(progress: float) -> float:
        progress = min(max(progress, 0.0), 1.0)
        return 1.0 - pow(1.0 - progress, 3.0)

    def _section_content_height(
        self,
        state: dict[str, object],
        *,
        force_measure: bool = False,
    ) -> int:
        cached_height = int(state.get("cached_height", 0) or 0)
        if cached_height > 0 and not force_measure:
            return cached_height
        content = state["content"]
        if hasattr(content, "update_idletasks") and hasattr(content, "winfo_reqheight"):
            content.update_idletasks()
            cached_height = max(1, int(content.winfo_reqheight()))
            state["cached_height"] = cached_height
            return cached_height
        return 1

    def _schedule_section_content_measure(self, state: dict[str, object]) -> None:
        if bool(state.get("collapsed", False)) or bool(state.get("animating", False)):
            return
        if state.get("measure_after_id"):
            return

        def _measure() -> None:
            state["measure_after_id"] = None
            if bool(state.get("collapsed", False)) or bool(state.get("animating", False)):
                return
            target_height = self._section_content_height(state, force_measure=True)
            self._apply_section_height(state, target_height)

        state["measure_after_id"] = self.after_idle(_measure)

    def _apply_section_height(self, state: dict[str, object], height: int) -> None:
        wrap = state["content_wrap"]
        if not isinstance(wrap, ctk.CTkFrame):
            return
        height = max(0, int(height))
        if int(state.get("rendered_height", -1)) == height:
            return
        configured_height = height
        if hasattr(wrap, "_reverse_widget_scaling"):
            configured_height = int(round(wrap._reverse_widget_scaling(height)))
        wrap.configure(height=max(0, configured_height))
        state["rendered_height"] = height

    def _on_section_content_configure(self, state: dict[str, object]) -> None:
        self._schedule_section_content_measure(state)

    def _animate_section(
        self,
        state: dict[str, object],
        start: int,
        end: int,
    ) -> None:
        start = max(0, int(start))
        end = max(0, int(end))
        if start == end:
            state["animating"] = False
            self._apply_section_height(state, end)
            return

        after_id = state.get("after_id")
        if after_id:
            try:
                self.after_cancel(str(after_id))
            except Exception:
                pass
        state["animating"] = True
        state["animation_generation"] = int(state.get("animation_generation", 0)) + 1
        generation = int(state["animation_generation"])
        duration_s = SECTION_ANIMATION_DURATION_MS / 1000.0
        started_at = time.perf_counter()
        delta = end - start

        def step() -> None:
            if int(state.get("animation_generation", 0)) != generation:
                return
            elapsed = time.perf_counter() - started_at
            progress = 1.0 if duration_s <= 0 else min(elapsed / duration_s, 1.0)
            eased = self._section_ease(progress)
            self._apply_section_height(state, round(start + delta * eased))
            if progress >= 1.0:
                state["after_id"] = None
                state["animating"] = False
                self._apply_section_height(state, end)
                if end <= 0:
                    self._unpack_section_content(state)
                return
            state["after_id"] = self.after(SECTION_ANIMATION_INTERVAL_MS, step)

        step()

    def _pack_section_content(self, state: dict[str, object]) -> None:
        if state.get("content_packed"):
            return
        content = state.get("content")
        if not isinstance(content, ctk.CTkFrame):
            return
        content.pack(fill="x")
        state["content_packed"] = True

    def _unpack_section_content(self, state: dict[str, object]) -> None:
        if not state.get("content_packed"):
            return
        content = state.get("content")
        if not isinstance(content, ctk.CTkFrame):
            return
        content.pack_forget()
        state["content_packed"] = False

    def _set_section_collapsed(
        self,
        state: dict[str, object],
        collapsed: bool,
        *,
        animate: bool,
    ) -> None:
        state["collapsed"] = collapsed
        arrow = state["arrow"]
        wrap = state["content_wrap"]
        if isinstance(arrow, ctk.CTkLabel):
            arrow.configure(
                text="▸" if collapsed else "▾",
                text_color=TEXT_MUTED if collapsed else ACCENT,
            )
        if not isinstance(wrap, ctk.CTkFrame):
            return
        if not collapsed:
            self._ensure_section_built(state)
            self._pack_section_content(state)
        target_height = 0 if collapsed else self._section_content_height(
            state,
            force_measure=True,
        )
        if not animate:
            state["animating"] = False
            self._apply_section_height(state, target_height)
            if collapsed:
                self._unpack_section_content(state)
            return
        current_height = int(state.get("rendered_height", wrap.winfo_height()) or 0)
        if current_height <= 1 and collapsed:
            current_height = self._section_content_height(state, force_measure=True)
        self._animate_section(state, current_height, target_height)

    def _toggle_section_card(self, state: dict[str, object]) -> None:
        self._set_section_collapsed(
            state,
            not bool(state.get("collapsed", False)),
            animate=True,
        )

    def _ensure_section_built(self, state: dict[str, object]) -> None:
        if bool(state.get("lazy_built", True)):
            return
        builder = state.get("lazy_builder")
        content = state.get("content")
        if not callable(builder) or not isinstance(content, ctk.CTkFrame):
            state["lazy_built"] = True
            return
        builder(content)
        state["lazy_built"] = True
        state["cached_height"] = 0
        scroll = getattr(self, "_settings_scroll", None)
        if isinstance(scroll, ctk.CTkScrollableFrame):
            self._bind_settings_scroll(scroll)

    def _ensure_all_lazy_sections_built(self) -> None:
        for state in getattr(self, "_section_cards", []):
            self._ensure_section_built(state)

    def _initialize_section_cards(self) -> None:
        self.update_idletasks()
        for state in self._section_cards:
            collapsed = bool(state.get("collapsed", False))
            if collapsed:
                state["animating"] = False
                self._apply_section_height(state, 0)
                self._unpack_section_content(state)
                continue
            self._section_content_height(state, force_measure=True)
            self._set_section_collapsed(state, False, animate=False)

    def _bind_settings_scroll(self, scroll: ctk.CTkScrollableFrame) -> None:
        canvas = getattr(scroll, "_parent_canvas", None)
        if canvas is None:
            return
        bound_widgets = getattr(self, "_settings_scroll_bound_widgets", None)
        if bound_widgets is None:
            bound_widgets = set()
            self._settings_scroll_bound_widgets = bound_widgets

        scroll_handler = getattr(self, "_settings_scroll_handler", None)
        if scroll_handler is None:
            def _fast_scroll(event, target_canvas=canvas):
                delta = getattr(event, "delta", 0)
                if delta:
                    steps = max(1, abs(int(delta)) // 120)
                    units = (-steps if delta > 0 else steps) * SETTINGS_SCROLL_UNITS_PER_WHEEL
                elif getattr(event, "num", None) == 4:
                    units = -SETTINGS_SCROLL_UNITS_PER_WHEEL
                elif getattr(event, "num", None) == 5:
                    units = SETTINGS_SCROLL_UNITS_PER_WHEEL
                else:
                    units = 0
                if units:
                    target_canvas.yview_scroll(units, "units")
                return "break"

            self._settings_scroll_handler = _fast_scroll
            scroll_handler = _fast_scroll

        def _bind_recursive(widget) -> None:
            widget_id = str(widget)
            if widget_id in bound_widgets:
                return
            bound_widgets.add(widget_id)
            widget.bind("<MouseWheel>", scroll_handler, add="+")
            widget.bind("<Button-4>", scroll_handler, add="+")
            widget.bind("<Button-5>", scroll_handler, add="+")
            for child in widget.winfo_children():
                _bind_recursive(child)

        def _run_bind() -> None:
            try:
                if not self.winfo_exists() or not scroll.winfo_exists():
                    return
                _bind_recursive(scroll)
            except tk.TclError:
                return

        self.after_idle(_run_bind)

    def _schedule_settings_layout_refresh(self) -> None:
        if getattr(self, "_settings_layout_after_id", None):
            return

        def _refresh() -> None:
            self._settings_layout_after_id = None
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            self.update_idletasks()
            for state in getattr(self, "_section_cards", []):
                if not bool(state.get("collapsed", False)) and not bool(state.get("animating", False)):
                    state["cached_height"] = 0
                    self._apply_section_height(
                        state,
                        self._section_content_height(state, force_measure=True),
                    )
            scroll = getattr(self, "_settings_scroll", None)
            if isinstance(scroll, ctk.CTkScrollableFrame):
                canvas = getattr(scroll, "_parent_canvas", None)
                if canvas is not None:
                    bbox = canvas.bbox("all")
                    if bbox is not None:
                        canvas.configure(scrollregion=bbox)

        self._settings_layout_after_id = self.after_idle(_refresh)

    def _build_section_card(
        self,
        parent,
        title: str,
        subtitle: str,
        *,
        collapsed: bool = True,
        lazy_builder=None,
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=CARD_BG,
            corner_radius=22,
            border_width=1,
            border_color=CARD_BORDER,
        )
        card.pack(padx=SETTINGS_CARD_PADX, pady=(0, 10), fill="x")

        header = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        header.pack(fill="x", padx=12, pady=(6, 0))

        top_row = ctk.CTkFrame(header, fg_color="transparent", corner_radius=0)
        top_row.pack(fill="x")

        arrow = ctk.CTkLabel(
            top_row,
            text="▾",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT,
        )
        arrow.pack(side="left", padx=(0, 8))

        title_label = ctk.CTkLabel(
            top_row,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRI,
        )
        title_label.pack(side="left")

        subtitle_label = ctk.CTkLabel(
            header,
            text=subtitle,
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=SETTINGS_TEXT_WRAP,
        )
        subtitle_label.pack(anchor="w", padx=(24, 0), pady=(2, 6))

        content_wrap = ctk.CTkFrame(card, fg_color="transparent", corner_radius=0)
        content_wrap.pack(fill="x", padx=0, pady=(0, 4))
        content_wrap.pack_propagate(False)
        content = ctk.CTkFrame(content_wrap, fg_color="transparent", corner_radius=0)
        if not collapsed:
            content.pack(fill="x")

        state = {
            "card": card,
            "header": header,
            "arrow": arrow,
            "content_wrap": content_wrap,
            "content": content,
            "content_packed": not collapsed,
            "collapsed": collapsed,
            "after_id": None,
            "cached_height": 1,
            "rendered_height": -1,
            "animating": False,
            "animation_generation": 0,
            "measure_after_id": None,
            "lazy_builder": lazy_builder,
            "lazy_built": lazy_builder is None,
        }
        self._section_cards.append(state)
        self._bind_section_toggle(state, header, top_row, arrow, title_label, subtitle_label)
        content.bind(
            "<Configure>",
            lambda _event, s=state: self._on_section_content_configure(s),
        )
        return content

    def _build_model_info_card(self, backend: str, model: str) -> None:
        card = ctk.CTkFrame(
            self._fields_frame,
            fg_color=CARD_BG,
            corner_radius=16,
            border_width=1,
            border_color=CARD_BORDER,
        )
        card.pack(fill="x", padx=4, pady=(8, 4))

        self._model_info_title_label = ctk.CTkLabel(
            card,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_PRI,
            anchor="w",
        )
        self._model_info_title_label.pack(anchor="w", padx=12, pady=(12, 6))

        self._model_info_badges = ctk.CTkFrame(card, fg_color="transparent")
        self._model_info_badges.pack(fill="x", padx=12, pady=(0, 8))

        self._model_info_body_label = ctk.CTkLabel(
            card,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_MODEL_WRAP,
            anchor="w",
        )
        self._model_info_body_label.pack(anchor="w", padx=12, pady=(0, 12))
        self._refresh_model_info(backend, model)

    def _badge_colors(self, category: str, value: str) -> tuple[str, str]:
        if category == "fit":
            if value == "very_recommended":
                return ("#ecf7ef", "#1f6b3d")
            if value == "recommended":
                return ("#e8f3ff", "#0057b8")
            if value == "not_recommended":
                return ("#fff1f0", "#a63d2f")
            return ("#f3f4f7", "#6e6e73")
        if category == "quality":
            if value == "high":
                return ("#ecf7ef", "#1f6b3d")
            if value == "basic":
                return ("#f3f4f7", "#6e6e73")
            return ("#eef3ff", "#345ca8")
        if value == "very_fast":
            return ("#ecf7ef", "#1f6b3d")
        if value == "slow":
            return ("#fff5e8", "#9a5c00")
        return ("#eef3ff", "#345ca8")

    def _add_model_badge(self, parent, category: str, title: str, value: str, index: int) -> None:
        bg_color, text_color = self._badge_colors(category, value)
        ctk.CTkLabel(
            parent,
            text=f"{title}  {self._ui_copy(value)}",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=text_color,
            fg_color=bg_color,
            corner_radius=999,
            padx=10,
            pady=5,
        ).grid(row=0, column=index, sticky="w", padx=(0, 6), pady=2)

    def _refresh_model_info(self, backend: str, model: str) -> None:
        if not self._model_info_title_label or not self._model_info_body_label or not self._model_info_badges:
            return

        profile = get_backend_model_profile(backend, model)
        self._model_info_title_label.configure(
            text=f"{self._ui_copy('model_title')}: {profile['model']}"
        )

        for child in self._model_info_badges.winfo_children():
            child.destroy()

        self._add_model_badge(self._model_info_badges, "speed", self._ui_copy("speed"), profile["speed"], 0)
        self._add_model_badge(self._model_info_badges, "quality", self._ui_copy("quality"), profile["quality"], 1)
        self._add_model_badge(self._model_info_badges, "fit", self._ui_copy("fit"), profile["fit"], 2)

        self._model_info_body_label.configure(text=self._ui_copy(profile["note"]))

    def _on_model_change(self, _selected_value: str) -> None:
        backend = self._backend_codes.get(self._backend_var.get(), BACKEND_ORDER[0])
        model = self._field_vars.get("model").get().strip() if "model" in self._field_vars else ""
        self._refresh_model_info(backend, model)
        self._schedule_settings_layout_refresh()

    def _build(self):
        pad = {"padx": SETTINGS_FIELD_PADX, "pady": 4}
        trans_cfg = self._config.get("translation", {})
        asr_cfg = self._config.get("asr", {})
        streaming_cfg = asr_cfg.get("streaming", {})
        audio_cfg = self._config.get("audio", {})
        tts_cfg = self._config.get("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
        text_input_cfg = self._config.get("text_input_window", {})
        if not isinstance(text_input_cfg, dict):
            text_input_cfg = {}
        social_cfg = trans_cfg.get("social", {}) if isinstance(trans_cfg.get("social", {}), dict) else {}
        legacy_desktop_cfg = audio_cfg.get("desktop_capture", {}) if isinstance(audio_cfg.get("desktop_capture", {}), dict) else {}
        vrc_cfg = self._config.get("vrc_listen", {}) if isinstance(self._config.get("vrc_listen", {}), dict) else {}
        if not vrc_cfg:
            vrc_cfg = {
                "enabled": bool(legacy_desktop_cfg.get("enabled", False)),
                "loopback_device": str(legacy_desktop_cfg.get("output_device", "")).strip() or None,
                "source_language": "auto",
                "target_language": "zh",
                "self_suppress": False,
                "self_suppress_seconds": 0.65,
                "show_overlay": False,
                "send_to_chatbox": True,
            }
        else:
            vrc_cfg.setdefault("source_language", "auto")
            vrc_cfg.setdefault("target_language", "zh")
            vrc_cfg.setdefault("self_suppress", False)
            vrc_cfg.setdefault("self_suppress_seconds", 0.65)
            vrc_cfg.setdefault("show_overlay", False)
            vrc_cfg.setdefault("send_to_chatbox", True)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self._settings_scroll = scroll
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_header_card(scroll)

        translation_card = self._build_section_card(
            scroll,
            self._ui_copy("translation_section"),
            self._ui_copy("translation_provider_subtitle"),
            collapsed=True,
        )

        self._build_asr_model_selector(translation_card, pad, asr_cfg)

        section_label(translation_card, self._ui_copy("settings_app_language"))
        ui_lang_labels = [label for label, _ in UI_LANGUAGE_OPTIONS]
        self._ui_lang_codes = {label: code for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_reverse = {code: label for label, code in UI_LANGUAGE_OPTIONS}
        self._ui_lang_var = ctk.StringVar(
            value=self._ui_lang_reverse.get(self._ui_lang, ui_lang_labels[0])
        )
        ctk.CTkOptionMenu(
            translation_card,
            values=ui_lang_labels,
            variable=self._ui_lang_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=14,
            text_color=TEXT_PRI,
            height=34,
        ).pack(**pad, fill="x")

        self._text_input_hotkey_var = ctk.StringVar(
            value=str(text_input_cfg.get("hotkey", DEFAULT_TEXT_INPUT_HOTKEY) or "")
        )
        self._build_entry(
            translation_card,
            self._ui_copy("text_input_hotkey"),
            self._text_input_hotkey_var,
            **pad,
        )
        self._build_hint_box(translation_card, self._ui_copy("text_input_hotkey_hint"))

        section_label(translation_card, self._ui_copy("translation_provider"))
        backend = normalize_backend(trans_cfg.get("backend"))
        backend_labels = [get_backend_label(code) for code in BACKEND_ORDER]
        self._backend_codes = {
            get_backend_label(code): code for code in BACKEND_ORDER
        }
        self._backend_reverse = {code: get_backend_label(code) for code in BACKEND_ORDER}
        self._backend_var = ctk.StringVar(value=self._backend_reverse.get(backend, backend_labels[0]))
        self._backend_menu = ctk.CTkOptionMenu(
            translation_card,
            values=backend_labels,
            variable=self._backend_var,
            command=self._on_backend_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=14,
            text_color=TEXT_PRI,
            height=34,
        )
        self._backend_menu.pack(**pad, fill="x")

        section_label(translation_card, self._ui_copy("settings_target_language"))
        target_language_options = get_target_language_options(ui_language=self._ui_lang)
        lang_labels = [label for label, _ in target_language_options]
        self._lang_codes = {label: code for label, code in target_language_options}
        self._lang_reverse = {code: label for label, code in target_language_options}
        current_target = trans_cfg.get("target_language", "ja")
        self._lang_var = ctk.StringVar(
            value=self._lang_reverse.get(current_target, lang_labels[0])
        )
        ctk.CTkOptionMenu(
            translation_card,
            values=lang_labels,
            variable=self._lang_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=14,
            text_color=TEXT_PRI,
            height=34,
        ).pack(**pad, fill="x")

        section_label(translation_card, self._ui_copy("settings_output_format"))
        format_labels = [label for label, _ in OUTPUT_FORMAT_OPTIONS]
        self._fmt_codes = {label: code for label, code in OUTPUT_FORMAT_OPTIONS}
        self._fmt_reverse = {code: label for label, code in OUTPUT_FORMAT_OPTIONS}
        current_format = normalize_output_format(trans_cfg.get("output_format"))
        self._fmt_var = ctk.StringVar(
            value=self._fmt_reverse.get(current_format, format_labels[0])
        )
        self._format_menu = ctk.CTkOptionMenu(
            translation_card,
            values=format_labels,
            variable=self._fmt_var,
            command=self._on_output_format_change,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=14,
            text_color=TEXT_PRI,
            height=34,
        )
        self._format_menu.pack(**pad, fill="x")
        self._build_hint_box(translation_card, self._ui_copy("settings_output_format_hint"))

        self._mic_send_to_chatbox_var = ctk.StringVar(
            value="1" if bool(trans_cfg.get("send_to_chatbox", True)) else "0"
        )
        self._build_switch_entry(
            translation_card,
            self._ui_copy("settings_send_to_chatbox"),
            self._mic_send_to_chatbox_var,
            **pad,
        )
        self._build_hint_box(
            translation_card,
            self._ui_copy("settings_send_to_chatbox_hint"),
        )

        section_label(translation_card, self._ui_copy("translation_provider_params"))
        self._fields_frame = ctk.CTkFrame(
            translation_card,
            fg_color=BG_SECONDARY,
            corner_radius=16,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self._fields_frame.pack(padx=SETTINGS_FIELD_PADX, pady=(4, 6), fill="x")

        self._backend_model_hint_box = self._build_hint_box(
            translation_card,
            "",
            visible=False,
        )
        self._backend_model_hint_label = ctk.CTkLabel(
            self._backend_model_hint_box,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._backend_model_hint_label.pack(padx=10, pady=8, anchor="w")
        self._on_backend_change(self._backend_var.get())

        self._translation_lock_box = self._build_hint_box(
            translation_card,
            "",
            visible=False,
        )
        self._translation_lock_label = ctk.CTkLabel(
            self._translation_lock_box,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._translation_lock_label.pack(padx=10, pady=8, anchor="w")
        self._apply_translation_mode_state()

        voice_card = self._build_section_card(
            scroll,
            self._ui_copy("voice_section"),
            self._ui_copy("voice_subtitle"),
            collapsed=True,
            lazy_builder=lambda card: self._build_voice_section(
                card,
                pad,
                asr_cfg,
                streaming_cfg,
                audio_cfg,
            ),
        )
        vrc_listen_card = self._build_section_card(
            scroll,
            self._ui_copy("vrc_listen_section"),
            self._ui_copy("vrc_listen_subtitle"),
            collapsed=True,
            lazy_builder=lambda card: self._build_vrc_listen_section(
                card,
                pad,
                vrc_cfg,
            ),
        )
        tts_card = self._build_section_card(
            scroll,
            self._ui_copy("tts_section"),
            self._ui_copy("tts_subtitle"),
            collapsed=True,
            lazy_builder=lambda card: self._build_tts_section(
                card,
                pad,
                tts_cfg,
            ),
        )
        rp_card = self._build_section_card(
            scroll,
            self._ui_copy("rp_section"),
            self._ui_copy("rp_subtitle"),
            collapsed=True,
            lazy_builder=lambda card: self._build_roleplay_section(
                card,
                pad,
                social_cfg,
            ),
        )
        self._initialize_section_cards()
        self._bind_settings_scroll(scroll)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(side="bottom", fill="x", padx=SETTINGS_FIELD_PADX, pady=10)

        ctk.CTkButton(
            btn_frame,
            text=self._t("save"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=12,
            height=36,
            width=100,
            text_color="#ffffff",
            command=self._save,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            btn_frame,
            text=self._t("cancel"),
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=36,
            width=100,
            text_color=TEXT_PRI,
            command=self.destroy,
        ).pack(side="right", padx=4)

    def _build_asr_model_selector(self, parent, pad, asr_cfg: dict) -> None:
        section_label(parent, self._ui_copy("settings_asr_backend"))
        asr_engines = _asr_engine_options(self._ui_lang)
        asr_labels = [label for label, _ in asr_engines]
        self._asr_codes = {label: code for label, code in asr_engines}
        self._asr_reverse = {code: label for label, code in asr_engines}

        raw_engine = asr_cfg.get("engine", DEFAULT_ASR_ENGINE)
        current_engine = raw_engine if raw_engine in self._asr_reverse else DEFAULT_ASR_ENGINE

        self._asr_var = ctk.StringVar(
            value=self._asr_reverse.get(current_engine, asr_labels[0])
        )
        self._asr_menu = ctk.CTkOptionMenu(
            parent,
            values=asr_labels,
            variable=self._asr_var,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=14,
            text_color=TEXT_PRI,
            width=SETTINGS_ASR_MENU_WIDTH,
            height=34,
            command=self._on_asr_engine_changed,
        )
        self._asr_menu.pack(**pad, fill="x")

        self._asr_hint_frame = self._build_hint_box(parent, "")
        self._asr_hint_label = ctk.CTkLabel(
            self._asr_hint_frame,
            text=self._engine_hint_text(current_engine),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._asr_hint_label.pack(padx=10, pady=8, anchor="w")
        self._asr_provider_after = self._asr_hint_frame

        self._build_asr_provider_config(parent, pad, asr_cfg, current_engine)

    def _build_voice_section(self, voice_card, pad, _asr_cfg, streaming_cfg, audio_cfg) -> None:
        # Input device selection
        section_label(voice_card, self._ui_copy("settings_input_device"))

        input_devices = AudioRecorder.list_devices()
        self._input_devices = {
            str(device.get("name", "")).strip(): int(device.get("index", -1))
            for device in input_devices
            if str(device.get("name", "")).strip()
        }
        input_default_label = self._ui_copy("settings_input_device_default")
        input_missing_label = self._ui_copy("settings_input_device_missing")
        input_labels = [input_default_label]
        input_labels.extend(self._input_devices.keys())

        configured_input = str(audio_cfg.get("input_device") or "").strip()
        if configured_input and configured_input not in input_labels:
            input_labels.append(configured_input)
        if len(input_labels) == 1:
            input_labels.append(input_missing_label)

        # Input device mode
        input_mode = str(audio_cfg.get("input_device_mode", "auto")).strip().lower()
        if input_mode not in {"auto", "fixed"}:
            input_mode = "auto"

        mode_auto_label = self._ui_copy("settings_input_device_mode_auto")
        mode_fixed_label = self._ui_copy("settings_input_device_mode_fixed")
        mode_labels = [mode_auto_label, mode_fixed_label]
        self._input_mode_codes = {
            mode_auto_label: "auto",
            mode_fixed_label: "fixed",
        }
        self._input_mode_reverse = {
            "auto": mode_auto_label,
            "fixed": mode_fixed_label,
        }
        self._input_device_mode_var = ctk.StringVar(
            value=self._input_mode_reverse.get(input_mode, mode_auto_label)
        )
        self._build_option_entry(
            voice_card,
            self._ui_copy("settings_input_device_mode"),
            mode_labels,
            self._input_device_mode_var,
            **pad,
        )

        # Input device selector
        selected_input = configured_input or input_default_label
        if selected_input not in input_labels:
            selected_input = input_default_label
        self._input_device_var = ctk.StringVar(value=selected_input)
        self._build_option_entry(
            voice_card,
            self._ui_copy("settings_input_device"),
            input_labels,
            self._input_device_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_input_device_hint"))

        section_label(voice_card, self._ui_copy("settings_streaming"))
        self._chunk_interval_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_interval_ms", 250))
        )
        self._chunk_window_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_window_s", 1.6))
        )
        self._partial_hits_var = ctk.StringVar(
            value=str(streaming_cfg.get("partial_stability_hits", 2))
        )

        self._build_entry(
            voice_card,
            self._ui_copy("settings_partial_refresh_interval"),
            self._chunk_interval_var,
            **pad,
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_recognition_window_length"),
            self._chunk_window_var,
            **pad,
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_partial_hits"),
            self._partial_hits_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_streaming_hint"))

        section_label(voice_card, self._ui_copy("settings_vad"))
        self._vad_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_silence_threshold", 0.65))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_vad_seconds"),
            self._vad_var,
            **pad,
        )

        denoise_texts = DENOISE_PRESET_LABELS.get(
            self._ui_lang,
            DENOISE_PRESET_LABELS["en"],
        )
        self._denoise_codes = {
            denoise_texts[code]: value for code, value in DENOISE_PRESET_VALUES
        }
        self._denoise_reverse = {
            value: denoise_texts[code] for code, value in DENOISE_PRESET_VALUES
        }
        try:
            denoise_strength = float(audio_cfg.get("denoise_strength", 0.0))
        except (TypeError, ValueError):
            denoise_strength = 0.0
        denoise_strength = min(
            DENOISE_PRESET_VALUES,
            key=lambda item: abs(item[1] - denoise_strength),
        )[1]
        denoise_labels = list(self._denoise_codes.keys())
        self._denoise_var = ctk.StringVar(
            value=self._denoise_reverse.get(denoise_strength, denoise_labels[0])
        )
        self._build_option_entry(
            voice_card,
            denoise_texts["title"],
            denoise_labels,
            self._denoise_var,
            **pad,
        )
        self._build_hint_box(voice_card, denoise_texts["hint"])

        # VAD Advanced Settings
        section_label(voice_card, self._ui_copy("settings_vad_advanced"))

        self._vad_sensitivity_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_sensitivity", 2))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_vad_sensitivity"),
            self._vad_sensitivity_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_vad_sensitivity_hint"))

        self._vad_speech_ratio_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_speech_ratio", 0.6))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_vad_speech_ratio"),
            self._vad_speech_ratio_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_vad_speech_ratio_hint"))

        self._vad_activation_threshold_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_activation_threshold_s", 0.2))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_vad_activation_threshold"),
            self._vad_activation_threshold_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_vad_activation_threshold_hint"))

        self._vad_min_rms_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_min_rms", 0.012))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_vad_min_rms"),
            self._vad_min_rms_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_vad_min_rms_hint"))

        # Audio Segment Length Settings
        section_label(voice_card, self._ui_copy("settings_segment_length"))

        self._min_segment_var = ctk.StringVar(
            value=str(audio_cfg.get("min_segment_s", 0.45))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_min_segment"),
            self._min_segment_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_min_segment_hint"))

        self._max_segment_var = ctk.StringVar(
            value=str(audio_cfg.get("max_segment_s", 6.0))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_max_segment"),
            self._max_segment_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_max_segment_hint"))

        self._partial_min_speech_var = ctk.StringVar(
            value=str(audio_cfg.get("partial_min_speech_s", 0.45))
        )
        self._build_entry(
            voice_card,
            self._ui_copy("settings_partial_min_speech"),
            self._partial_min_speech_var,
            **pad,
        )
        self._build_hint_box(voice_card, self._ui_copy("settings_partial_min_speech_hint"))

        section_label(voice_card, self._ui_copy("settings_dictionary"))
        self._build_hint_box(voice_card, self._ui_copy("settings_dictionary_hint"))

        dictionary_status_box = self._build_hint_box(voice_card, "")
        self._dictionary_status_label = ctk.CTkLabel(
            dictionary_status_box,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._dictionary_status_label.pack(padx=10, pady=8, anchor="w")
        self._refresh_dictionary_status()

        dictionary_actions = ctk.CTkFrame(voice_card, fg_color="transparent")
        dictionary_actions.pack(padx=SETTINGS_FIELD_PADX, pady=(0, 6), fill="x")
        self._dictionary_update_button = ctk.CTkButton(
            dictionary_actions,
            text=self._ui_copy("settings_dictionary_update"),
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=34,
            text_color=TEXT_PRI,
            command=self._start_dictionary_update,
        )
        self._dictionary_update_button.pack(anchor="w")
        self._build_dictionary_custom_editor(voice_card)

    def _build_dictionary_custom_editor(self, parent) -> None:
        section_label(parent, self._ui_copy("settings_dictionary_custom"))
        self._build_hint_box(parent, self._ui_copy("settings_dictionary_custom_hint"))

        editor = self._build_hint_box(parent, "")
        editor.grid_columnconfigure(0, weight=1)

        self._dictionary_custom_replacement_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            editor,
            text=self._ui_copy("settings_dictionary_custom_replacement"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        ctk.CTkEntry(
            editor,
            textvariable=self._dictionary_custom_replacement_var,
            fg_color=GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=34,
            text_color=TEXT_PRI,
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        ctk.CTkLabel(
            editor,
            text=self._ui_copy("settings_dictionary_custom_patterns"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=0, sticky="w", padx=10, pady=(0, 2))
        self._dictionary_custom_patterns_box = ctk.CTkTextbox(
            editor,
            height=70,
            fg_color=GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            text_color=TEXT_PRI,
            wrap="word",
        )
        self._dictionary_custom_patterns_box.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=10,
            pady=(0, 4),
        )

        ctk.CTkLabel(
            editor,
            text=self._ui_copy("settings_dictionary_custom_patterns_hint"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        ).grid(row=4, column=0, sticky="w", padx=10, pady=(0, 8))

        self._dictionary_custom_save_button = ctk.CTkButton(
            editor,
            text=self._ui_copy("settings_dictionary_custom_save"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=12,
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_dictionary_custom_entry,
        )
        self._dictionary_custom_save_button.grid(
            row=5,
            column=0,
            sticky="w",
            padx=10,
            pady=(0, 8),
        )

        self._dictionary_custom_status_label = ctk.CTkLabel(
            editor,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._dictionary_custom_status_label.grid(
            row=6,
            column=0,
            sticky="w",
            padx=10,
            pady=(0, 10),
        )

    def _engine_hint_text(self, engine: str) -> str:
        if engine == "webspeech":
            return self._t("asr_hint_webspeech")
        if engine == "qwen3-asr":
            return self._t("asr_hint_qwen3")
        if engine == "gemini-live":
            return self._t("asr_hint_gemini")
        return self._t("asr_hint_sensevoice")

    def _on_asr_engine_changed(self, _label: str | None = None) -> None:
        engine = self._asr_codes.get(self._asr_var.get(), DEFAULT_ASR_ENGINE)
        try:
            self._asr_hint_label.configure(text=self._engine_hint_text(engine))
        except tk.TclError:
            pass
        self._render_asr_provider_config(engine)
        self._schedule_settings_layout_refresh()

    def _build_asr_provider_config(self, parent, pad, asr_cfg: dict, current_engine: str) -> None:
        qwen_cfg = asr_cfg.get("qwen3_asr", {}) if isinstance(asr_cfg.get("qwen3_asr", {}), dict) else {}
        gemini_cfg = asr_cfg.get("gemini_live", {}) if isinstance(asr_cfg.get("gemini_live", {}), dict) else {}

        qwen_region = normalize_qwen3_asr_region(qwen_cfg.get("region", QWEN3_ASR_DEFAULT_REGION))
        qwen_base_url = str(qwen_cfg.get("base_url", "") or "").strip().rstrip("/")
        if qwen_region != "custom":
            qwen_base_url = get_qwen3_asr_base_url(qwen_region)
        qwen_model = str(qwen_cfg.get("model", QWEN3_ASR_DEFAULT_MODEL) or "").strip()
        if qwen_model not in QWEN3_ASR_MODEL_CHOICES:
            qwen_model = QWEN3_ASR_DEFAULT_MODEL

        self._qwen_api_key_var = ctk.StringVar(value=str(qwen_cfg.get("api_key", "") or ""))
        self._qwen_base_url_var = ctk.StringVar(value=qwen_base_url)

        region_labels = _localized_table(QWEN3_REGION_LABELS, self._ui_lang)
        self._qwen_region_codes = {label: code for code, label in region_labels.items()}
        self._qwen_region_reverse = {code: label for code, label in region_labels.items()}
        self._qwen_region_var = ctk.StringVar(
            value=self._qwen_region_reverse.get(qwen_region, self._qwen_region_reverse[QWEN3_ASR_DEFAULT_REGION])
        )

        model_labels = _localized_table(QWEN3_MODEL_LABELS, self._ui_lang)
        self._qwen_model_codes = {label: code for code, label in model_labels.items()}
        self._qwen_model_reverse = {code: label for code, label in model_labels.items()}
        self._qwen_model_var = ctk.StringVar(
            value=self._qwen_model_reverse.get(qwen_model, self._qwen_model_reverse[QWEN3_ASR_DEFAULT_MODEL])
        )

        self._gemini_api_key_var = ctk.StringVar(value=str(gemini_cfg.get("api_key", "") or ""))
        self._gemini_model_var = ctk.StringVar(
            value=str(
                gemini_cfg.get("model", "gemini-3.1-flash-live-preview")
                or "gemini-3.1-flash-live-preview"
            )
        )
        self._asr_provider_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self._asr_provider_pad = dict(pad)
        self._render_asr_provider_config(current_engine)

    def _pack_asr_config_frame(self) -> None:
        pack_options = {
            "padx": SETTINGS_FIELD_PADX,
            "pady": (0, 4),
            "fill": "x",
        }
        anchor = getattr(self, "_asr_provider_after", None)
        try:
            if anchor is not None and anchor.winfo_exists():
                pack_options["after"] = anchor
        except tk.TclError:
            pass
        self._asr_provider_frame.pack(**pack_options)

    def _pack_asr_note(self, parent, text: str) -> None:
        note = ctk.CTkFrame(
            parent,
            fg_color=BG_SECONDARY,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
        )
        note.pack(padx=0, pady=(2, 6), fill="x")
        ctk.CTkLabel(
            note,
            text=text,
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        ).pack(padx=10, pady=8, anchor="w")

    def _build_asr_text_entry(
        self,
        parent,
        label_text: str,
        variable: ctk.StringVar,
        *,
        secret: bool = False,
        readonly: bool = False,
    ) -> ctk.CTkEntry:
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(2, 0))
        entry = ctk.CTkEntry(
            parent,
            textvariable=variable,
            fg_color=BG_SECONDARY if readonly else GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=36,
            text_color=TEXT_SEC if readonly else TEXT_PRI,
            show="*" if secret else "",
            state="disabled" if readonly else "normal",
        )
        entry.pack(padx=0, pady=(0, 4), fill="x")
        return entry

    def _selected_qwen_region(self) -> str:
        return getattr(self, "_qwen_region_codes", {}).get(
            self._qwen_region_var.get(),
            QWEN3_ASR_DEFAULT_REGION,
        )

    def _selected_qwen_model(self) -> str:
        return getattr(self, "_qwen_model_codes", {}).get(
            self._qwen_model_var.get(),
            QWEN3_ASR_DEFAULT_MODEL,
        )

    def _sync_qwen_base_url(self) -> None:
        region = self._selected_qwen_region()
        readonly = region != "custom"
        if readonly:
            self._qwen_base_url_var.set(get_qwen3_asr_base_url(region))
        entry = getattr(self, "_qwen_base_url_entry", None)
        if entry is not None:
            entry.configure(
                state="disabled" if readonly else "normal",
                fg_color=BG_SECONDARY if readonly else GLASS_BG,
                text_color=TEXT_SEC if readonly else TEXT_PRI,
            )

    def _on_qwen_region_changed(self, _label: str | None = None) -> None:
        self._sync_qwen_base_url()
        self._schedule_settings_layout_refresh()

    def _on_qwen_model_changed(self, _label: str | None = None) -> None:
        hints = _localized_table(QWEN3_MODEL_HINTS, self._ui_lang)
        hint = hints.get(self._selected_qwen_model(), "")
        label = getattr(self, "_qwen_model_hint_label", None)
        if label is not None:
            label.configure(text=hint)
        self._schedule_settings_layout_refresh()

    def _render_asr_provider_config(self, engine: str) -> None:
        frame = getattr(self, "_asr_provider_frame", None)
        if frame is None:
            return
        for child in frame.winfo_children():
            child.destroy()

        if engine == "sensevoice-small":
            if frame.winfo_manager():
                frame.pack_forget()
            return

        self._pack_asr_config_frame()
        section_label(frame, self._asr_copy("provider_config"))

        if engine == "qwen3-asr":
            self._build_asr_text_entry(
                frame,
                self._asr_copy("api_key"),
                self._qwen_api_key_var,
                secret=True,
            )
            self._build_option_entry(
                frame,
                self._asr_copy("region"),
                list(getattr(self, "_qwen_region_codes", {}).keys()),
                self._qwen_region_var,
                command=self._on_qwen_region_changed,
                padx=0,
                pady=(0, 4),
            )
            self._qwen_base_url_entry = self._build_asr_text_entry(
                frame,
                self._asr_copy("base_url"),
                self._qwen_base_url_var,
                readonly=self._selected_qwen_region() != "custom",
            )
            self._build_option_entry(
                frame,
                self._asr_copy("model"),
                list(getattr(self, "_qwen_model_codes", {}).keys()),
                self._qwen_model_var,
                command=self._on_qwen_model_changed,
                padx=0,
                pady=(0, 4),
            )
            self._qwen_model_hint_label = ctk.CTkLabel(
                frame,
                text=_localized_table(QWEN3_MODEL_HINTS, self._ui_lang).get(
                    self._selected_qwen_model(),
                    "",
                ),
                font=ctk.CTkFont(size=11),
                text_color=TEXT_SEC,
                justify="left",
                wraplength=SETTINGS_HINT_WRAP,
            )
            self._qwen_model_hint_label.pack(padx=12, pady=(0, 6), anchor="w")
            self._pack_asr_note(frame, self._asr_copy("qwen_hint"))
            self._sync_qwen_base_url()
            return

        if engine == "gemini-live":
            self._build_asr_text_entry(
                frame,
                self._asr_copy("api_key"),
                self._gemini_api_key_var,
                secret=True,
            )
            self._build_asr_text_entry(
                frame,
                self._t("model"),
                self._gemini_model_var,
            )
            self._pack_asr_note(frame, self._asr_copy("gemini_hint"))
            return

        if engine == "webspeech":
            self._pack_asr_note(frame, self._asr_copy("webspeech_hint"))
            return


    def _build_vrc_listen_section(self, vrc_listen_card, pad, vrc_cfg) -> None:
        self._vrc_listen_enabled_var = ctk.StringVar(
            value="1" if bool(vrc_cfg.get("enabled", False)) else "0"
        )
        self._build_switch_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_enabled"),
            self._vrc_listen_enabled_var,
            command=self._on_vrc_listen_enabled_toggled,
            **pad,
        )

        self._listen_overlay_enabled_var = ctk.StringVar(
            value="1" if bool(vrc_cfg.get("show_overlay", False)) else "0"
        )
        self._build_switch_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_overlay"),
            self._listen_overlay_enabled_var,
            command=self._on_listen_overlay_toggled,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_overlay_hint"),
        )

        self._listen_send_to_chatbox_var = ctk.StringVar(
            value="1" if bool(vrc_cfg.get("send_to_chatbox", True)) else "0"
        )
        self._build_switch_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_send_to_chatbox"),
            self._listen_send_to_chatbox_var,
            command=self._on_listen_send_to_chatbox_toggled,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_send_to_chatbox_hint"),
        )

        listen_asr_options = _listen_asr_engine_options(self._ui_lang)
        listen_asr_labels = [label for label, _ in listen_asr_options]
        self._listen_asr_engine_codes = {
            label: code for label, code in listen_asr_options
        }
        self._listen_asr_engine_reverse = {
            code: label for label, code in listen_asr_options
        }
        current_listen_asr = str(
            vrc_cfg.get("asr_engine", ASR_ENGINE_FOLLOW_MAIN) or ASR_ENGINE_FOLLOW_MAIN
        ).strip()
        if current_listen_asr not in self._listen_asr_engine_reverse:
            current_listen_asr = ASR_ENGINE_FOLLOW_MAIN
        self._listen_asr_engine_var = ctk.StringVar(
            value=self._listen_asr_engine_reverse.get(
                current_listen_asr,
                listen_asr_labels[0],
            )
        )
        self._build_option_entry(
            vrc_listen_card,
            self._asr_copy("listen_asr"),
            listen_asr_labels,
            self._listen_asr_engine_var,
            **pad,
        )
        self._build_hint_box(vrc_listen_card, self._asr_copy("listen_asr_hint"))

        self._listen_self_suppress_var = ctk.StringVar(
            value="1" if bool(vrc_cfg.get("self_suppress", False)) else "0"
        )
        self._build_switch_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_self_suppress"),
            self._listen_self_suppress_var,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_self_suppress_hint"),
        )
        self._listen_self_suppress_seconds_var = ctk.StringVar(
            value=str(vrc_cfg.get("self_suppress_seconds", 0.65))
        )
        self._build_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_self_suppress_seconds"),
            self._listen_self_suppress_seconds_var,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_self_suppress_seconds_hint"),
        )
        self._listen_segment_duration_var = ctk.StringVar(
            value=str(vrc_cfg.get("segment_duration_s", 2.0))
        )
        self._build_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_segment_duration"),
            self._listen_segment_duration_var,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_segment_duration_hint"),
        )
        self._listen_tail_silence_var = ctk.StringVar(
            value=str(vrc_cfg.get("tail_silence_s", 0.65))
        )
        self._build_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_tail_silence"),
            self._listen_tail_silence_var,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_tail_silence_hint"),
        )

        target_language_options = get_target_language_options(ui_language=self._ui_lang)
        listen_lang_labels = [label for label, _ in target_language_options]
        self._listen_lang_codes = {
            label: code for label, code in target_language_options
        }
        self._listen_lang_reverse = {
            code: label for label, code in target_language_options
        }
        listen_source_options = get_manual_source_language_options(
            ui_language=self._ui_lang
        )
        listen_source_labels = [label for label, _ in listen_source_options]
        self._listen_src_codes = {
            label: code for label, code in listen_source_options
        }
        self._listen_src_reverse = {
            code: label for label, code in listen_source_options
        }

        section_label(vrc_listen_card, self._ui_copy("vrc_listen_device"))
        loopback_devices = _list_desktop_output_devices()
        self._loopback_devices = {
            str(device.get("name", "")).strip(): int(device.get("index", -1))
            for device in loopback_devices
            if str(device.get("name", "")).strip()
        }
        loopback_default_label = self._ui_copy("vrc_listen_device_default")
        loopback_missing_label = self._ui_copy("vrc_listen_device_missing")
        loopback_labels = [loopback_default_label]
        loopback_labels.extend(self._loopback_devices.keys())
        configured_loopback = str(vrc_cfg.get("loopback_device") or "").strip()

        if configured_loopback and configured_loopback not in loopback_labels:
            loopback_labels.append(configured_loopback)
        if len(loopback_labels) == 1:
            loopback_labels.append(loopback_missing_label)
        selected_loopback = configured_loopback or loopback_default_label
        if selected_loopback not in loopback_labels:
            selected_loopback = loopback_default_label
        self._loopback_device_var = ctk.StringVar(value=selected_loopback)
        self._build_option_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_device"),
            loopback_labels,
            self._loopback_device_var,
            **pad,
        )
        self._build_hint_box(vrc_listen_card, self._ui_copy("vrc_listen_device_hint"))

        current_listen_source = str(vrc_cfg.get("source_language", "auto")).strip() or "auto"
        self._listen_source_lang_var = ctk.StringVar(
            value=self._listen_src_reverse.get(
                current_listen_source,
                listen_source_labels[0],
            )
        )
        self._build_option_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_source_language"),
            listen_source_labels,
            self._listen_source_lang_var,
            **pad,
        )
        self._build_hint_box(
            vrc_listen_card,
            self._ui_copy("vrc_listen_source_language_hint"),
        )

        current_listen_target = str(vrc_cfg.get("target_language", "zh")).strip() or "zh"
        self._listen_target_lang_var = ctk.StringVar(
            value=self._listen_lang_reverse.get(
                current_listen_target,
                listen_lang_labels[0],
            )
        )
        self._build_option_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_target_language"),
            listen_lang_labels,
            self._listen_target_lang_var,
            **pad,
        )


    def _build_tts_section(self, tts_card, pad, tts_cfg) -> None:
        self._tts_enabled_var = ctk.StringVar(
            value="1" if bool(tts_cfg.get("enabled", False)) else "0"
        )
        self._tts_enabled_switch = self._build_switch_entry(
            tts_card,
            self._ui_copy("tts_enable"),
            self._tts_enabled_var,
            command=self._on_tts_enabled_change,
        )

        tts_engine = self._normalized_tts_engine(tts_cfg.get("engine"))
        tts_engine_cfg = self._tts_engine_config(tts_cfg, tts_engine)
        tts_engine_options = self._tts_engine_options()
        self._tts_engine_codes = {
            label: code for label, code in tts_engine_options
        }
        self._tts_engine_reverse = {
            code: label for label, code in tts_engine_options
        }
        self._tts_engine_var = ctk.StringVar(
            value=self._tts_engine_reverse.get(tts_engine, tts_engine_options[0][0])
        )
        configured_voice = str(
            tts_engine_cfg.get("voice") or self._default_tts_voice(tts_engine) or ""
        )
        self._tts_voice_var = ctk.StringVar(value=configured_voice)
        self._tts_voice_display_to_id = {}
        self._tts_voice_id_to_display = {}
        self._tts_rate_var = ctk.DoubleVar(
            value=self._safe_tts_rate(tts_engine_cfg.get("rate"), engine=tts_engine)
        )
        self._tts_volume_var = ctk.DoubleVar(
            value=self._safe_tts_volume(tts_engine_cfg.get("volume"))
        )
        stored_output_to_vrchat = tts_cfg.get("output_to_vrchat")
        tts_output_device = tts_cfg.get("output_device")
        output_to_vrchat = (
            tts_output_device is not None and tts_output_device != -1
            if stored_output_to_vrchat is None
            else bool(stored_output_to_vrchat)
        )
        self._tts_output_to_vrchat_var = ctk.StringVar(value="1" if output_to_vrchat else "0")
        self._tts_auto_read_var = ctk.StringVar(
            value="1" if bool(tts_cfg.get("auto_read", True)) else "0"
        )
        self._tts_monitor_var = ctk.StringVar(
            value="1" if bool(tts_cfg.get("monitor_enabled", False)) else "0"
        )
        if not TTS_FEATURE_ENABLED:
            self._tts_controls = [self._tts_enabled_switch]
            self._tts_engine_available = False
            self._apply_tts_enabled_state()
            return

        self._tts_engine_menu = self._build_option_entry(
            tts_card,
            self._ui_copy("tts_engine"),
            [label for label, _ in tts_engine_options],
            self._tts_engine_var,
            command=self._on_tts_engine_change,
            **pad,
        )

        initial_voices = list(self._default_tts_voice_options(tts_engine))
        if configured_voice and configured_voice not in initial_voices:
            initial_voices.insert(0, configured_voice)
        if not initial_voices:
            initial_voices = [self._ui_copy("tts_voice_loading")]
        self._tts_voice_var.set(initial_voices[0])
        self._tts_voice_menu = self._build_option_entry(
            tts_card,
            self._ui_copy("tts_voice"),
            initial_voices,
            self._tts_voice_var,
            max_dropdown_height=TTS_VOICE_DROPDOWN_MAX_HEIGHT,
            **pad,
        )

        custom_voice_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
        custom_voice_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=(0, 4))
        self._tts_custom_voice_import_button = ctk.CTkButton(
            custom_voice_frame,
            text=self._ui_copy("tts_import_custom_voice"),
            width=164,
            height=30,
            corner_radius=8,
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            text_color=TEXT_PRI,
            command=self._import_custom_tts_voice_folder,
        )
        self._tts_custom_voice_import_button.pack(side="left", padx=(4, 8))
        self._tts_custom_voice_status_label = ctk.CTkLabel(
            custom_voice_frame,
            text="",
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        self._tts_custom_voice_status_label.pack(side="left", fill="x", expand=True)
        self._refresh_custom_tts_voice_status()

        # CPU/GPU device selection for Style-Bert-VITS2
        tts_device_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
        tts_device_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=(0, 4))

        tts_device_label = ctk.CTkLabel(
            tts_device_frame,
            text=self._ui_copy("tts_device"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        tts_device_label.pack(side="left", padx=(4, 12))

        self._tts_device_var = ctk.StringVar(
            value=self._config.get("tts", {}).get("style_bert_vits2", {}).get("device", "cpu")
        )

        # Map display labels to internal values
        device_options = [
            (self._ui_copy("tts_device_cpu"), "cpu"),
            (self._ui_copy("tts_device_gpu"), "cuda"),
        ]
        self._tts_device_codes = {label: code for label, code in device_options}
        self._tts_device_reverse = {code: label for label, code in device_options}

        # Set current value using display label
        current_device = self._config.get("tts", {}).get("style_bert_vits2", {}).get("device", "cpu")
        self._tts_device_var.set(
            self._tts_device_reverse.get(current_device, device_options[0][0])
        )

        self._tts_device_menu = ctk.CTkOptionMenu(
            tts_device_frame,
            values=[label for label, _ in device_options],
            variable=self._tts_device_var,
            width=120,
            height=28,
            corner_radius=8,
            fg_color=GLASS_BG,
            button_color=GLASS_HOVER,
            button_hover_color=GLASS_BORDER,
            dropdown_fg_color=BG_SECONDARY,
            dropdown_hover_color=GLASS_HOVER,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
        )
        self._tts_device_menu.pack(side="left", padx=(0, 8))

        tts_device_hint = ctk.CTkLabel(
            tts_device_frame,
            text="(Style-Bert-VITS2 only)",
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        tts_device_hint.pack(side="left", fill="x", expand=True)

        bert_language_options = [
            (self._ui_copy("tts_bert_language_jp"), "jp"),
            (self._ui_copy("tts_bert_language_zh"), "zh"),
            (self._ui_copy("tts_bert_language_en"), "en"),
        ]
        self._tts_bert_language_codes = {
            label: code for label, code in bert_language_options
        }
        self._tts_bert_language_reverse = {
            code: label for label, code in bert_language_options
        }
        style_bert_cfg = self._config.get("tts", {}).get("style_bert_vits2", {})
        current_bert_language = normalize_style_bert_bert_language(
            style_bert_cfg.get("bert_language", "jp")
        )
        self._tts_bert_language_var = ctk.StringVar(
            value=self._tts_bert_language_reverse.get(
                current_bert_language,
                bert_language_options[0][0],
            )
        )

        tts_bert_language_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
        tts_bert_language_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=(0, 4))

        tts_bert_language_label = ctk.CTkLabel(
            tts_bert_language_frame,
            text=self._ui_copy("tts_bert_language"),
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=13),
            anchor="w",
        )
        tts_bert_language_label.pack(side="left", padx=(4, 12))

        self._tts_bert_language_menu = ctk.CTkOptionMenu(
            tts_bert_language_frame,
            values=[label for label, _ in bert_language_options],
            variable=self._tts_bert_language_var,
            width=180,
            height=28,
            corner_radius=8,
            fg_color=GLASS_BG,
            button_color=GLASS_HOVER,
            button_hover_color=GLASS_BORDER,
            dropdown_fg_color=BG_SECONDARY,
            dropdown_hover_color=GLASS_HOVER,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
            command=self._on_tts_bert_language_change,
        )
        self._tts_bert_language_menu.pack(side="left", padx=(0, 8))

        tts_bert_language_hint = ctk.CTkLabel(
            tts_bert_language_frame,
            text=self._ui_copy("tts_bert_language_hint"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            anchor="w",
        )
        tts_bert_language_hint.pack(side="left", fill="x", expand=True)

        self._bert_info_slot = ctk.CTkFrame(tts_card, fg_color="transparent")
        self._bert_info_slot.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=(0, 8))

        # BERT model download info
        self._bert_info_frame = ctk.CTkFrame(
            self._bert_info_slot,
            fg_color=BG_SECONDARY,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self._bert_info_pack_options = {
            "fill": "x",
        }
        self._bert_info_frame.pack(**self._bert_info_pack_options)

        self._bert_info_label = ctk.CTkLabel(
            self._bert_info_frame,
            text=self._ui_copy("tts_bert_model_info"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
        )
        self._bert_info_label.pack(padx=12, pady=(8, 4), fill="x")

        # Add download button for BERT model
        self._bert_download_btn = ctk.CTkButton(
            self._bert_info_frame,
            text=self._ui_copy("tts_bert_download_btn"),
            command=self._download_bert_model,
            height=28,
            corner_radius=8,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=12),
        )
        self._bert_download_btn.pack(padx=12, pady=(4, 8), anchor="w")

        rate_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
        rate_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=4)
        ctk.CTkLabel(
            rate_frame,
            text=self._ui_copy("tts_speed"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=4, pady=(0, 2))
        rate_row = ctk.CTkFrame(rate_frame, fg_color="transparent")
        rate_row.pack(fill="x")
        self._tts_rate_slider = ctk.CTkSlider(
            rate_row,
            from_=0.5,
            to=2.0,
            number_of_steps=30,
            variable=self._tts_rate_var,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            fg_color=BG_SECONDARY,
            command=self._update_tts_rate_label,
        )
        self._tts_rate_slider.pack(side="left", fill="x", expand=True, padx=(4, 10))
        self._tts_rate_label = ctk.CTkLabel(
            rate_row,
            text="",
            width=46,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._tts_rate_label.pack(side="left", padx=(0, 4))

        volume_frame = ctk.CTkFrame(tts_card, fg_color="transparent")
        volume_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=4)
        ctk.CTkLabel(
            volume_frame,
            text=self._ui_copy("tts_volume"),
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=4, pady=(0, 2))
        volume_row = ctk.CTkFrame(volume_frame, fg_color="transparent")
        volume_row.pack(fill="x")
        self._tts_volume_slider = ctk.CTkSlider(
            volume_row,
            from_=0.0,
            to=1.0,
            number_of_steps=20,
            variable=self._tts_volume_var,
            progress_color=ACCENT,
            button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            fg_color=BG_SECONDARY,
            command=self._update_tts_volume_label,
        )
        self._tts_volume_slider.pack(side="left", fill="x", expand=True, padx=(4, 10))
        self._tts_volume_label = ctk.CTkLabel(
            volume_row,
            text="",
            width=46,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._tts_volume_label.pack(side="left", padx=(0, 4))

        # TTS Output to VRChat - resolve saved virtual device by stable name.
        tts_output_device_name = str(tts_cfg.get("output_device_name") or "").strip()
        if output_to_vrchat:
            virtual_device = resolve_output_device(
                tts_output_device,
                tts_output_device_name,
                prefer_virtual=True,
            )
        else:
            virtual_device = find_best_virtual_output_device()

        self._tts_virtual_device_id = virtual_device[0] if virtual_device else None
        self._tts_virtual_device_name = virtual_device[1] if virtual_device else None

        self._tts_output_to_vrchat_switch = self._build_switch_entry(
            tts_card,
            self._ui_copy("tts_output_device"),
            self._tts_output_to_vrchat_var,
        )

        # Show current device status
        if virtual_device:
            device_display = f"{virtual_device[1]}"
            status_text = self._ui_copy("tts_output_device_status").format(
                device=device_display
            )
            status_color = TEXT_SEC
        else:
            status_text = self._ui_copy("tts_output_device_status").format(
                device=self._ui_copy("tts_output_device_default")
            )
            status_color = TEXT_MUTED

        self._tts_device_status_label = ctk.CTkLabel(
            tts_card,
            text=status_text,
            text_color=status_color,
            font=ctk.CTkFont(size=11),
        )
        self._tts_device_status_label.pack(anchor="w", padx=SETTINGS_FIELD_PADX + 4, pady=(0, 4))

        self._build_mixline_download_hint(tts_card, self._ui_copy("tts_output_device_hint"))

        # Check if virtual audio device exists
        if not virtual_device:
            warning_frame = ctk.CTkFrame(tts_card, fg_color="#fff3cd", corner_radius=8)
            warning_frame.pack(fill="x", padx=SETTINGS_FIELD_PADX, pady=(4, 8))

            ctk.CTkLabel(
                warning_frame,
                text="⚠️ " + self._ui_copy("tts_no_virtual_device"),
                text_color="#856404",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(anchor="w", padx=8, pady=(8, 4))

        self._tts_monitor_switch = self._build_switch_entry(
            tts_card,
            self._ui_copy("tts_monitor"),
            self._tts_monitor_var,
            command=self._on_tts_enabled_change,
        )
        self._build_hint_box(tts_card, self._ui_copy("tts_monitor_hint"))

        self._tts_auto_read_switch = self._build_switch_entry(
            tts_card,
            self._ui_copy("tts_auto_read"),
            self._tts_auto_read_var,
            command=self._on_tts_enabled_change,
        )

        self._tts_test_button = ctk.CTkButton(
            tts_card,
            text=self._ui_copy("tts_test"),
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=34,
            width=120,
            text_color=TEXT_PRI,
            command=self._test_tts,
        )
        self._tts_test_button.pack(anchor="w", padx=SETTINGS_FIELD_PADX, pady=(6, 8))
        self._build_hint_box(tts_card, self._ui_copy("tts_hint"))

        self._tts_controls = [
            self._tts_engine_menu,
            self._tts_voice_menu,
            self._tts_device_menu,
            self._tts_bert_language_menu,
            self._tts_rate_slider,
            self._tts_volume_slider,
            self._tts_output_to_vrchat_switch,
            self._tts_monitor_switch,
            self._tts_auto_read_switch,
            self._tts_test_button,
        ]
        self._update_tts_rate_label(self._tts_rate_var.get())
        self._update_tts_volume_label(self._tts_volume_var.get())
        self._on_tts_engine_change(self._tts_engine_var.get())
        self._apply_tts_enabled_state()


    def _build_roleplay_section(self, rp_card, pad, social_cfg) -> None:
        rp_enabled = str(social_cfg.get("mode", "standard")).strip() == "roleplay"
        stored_preset = str(social_cfg.get("persona_preset", "custom")).strip() or "custom"
        if stored_preset not in ROLEPLAY_PRESETS:
            stored_preset = "custom"
        preset_profile = ROLEPLAY_PRESETS.get(stored_preset, ROLEPLAY_PRESETS["custom"])
        default_name = str(preset_profile.get("persona_name", ""))
        default_prompt = str(preset_profile.get("persona_prompt", ""))
        default_glossary = str(preset_profile.get("persona_glossary", ""))

        self._roleplay_enabled_var = ctk.StringVar(value="1" if rp_enabled else "0")
        self._build_switch_entry(
            rp_card,
            self._ui_copy("rp_enabled"),
            self._roleplay_enabled_var,
        )

        roleplay_preset_options = _roleplay_preset_options(self._ui_lang)
        self._roleplay_preset_codes = {
            label: code for label, code in roleplay_preset_options
        }
        self._roleplay_preset_reverse = {
            code: label for label, code in roleplay_preset_options
        }
        self._roleplay_preset_var = ctk.StringVar(
            value=self._roleplay_preset_reverse.get(stored_preset, roleplay_preset_options[0][0])
        )
        self._build_option_entry(
            rp_card,
            self._ui_copy("rp_preset"),
            [label for label, _ in roleplay_preset_options],
            self._roleplay_preset_var,
            command=self._on_roleplay_preset_change,
            **pad,
        )
        self._build_hint_box(rp_card, self._ui_copy("rp_hint"))

        self._persona_name_var = ctk.StringVar(
            value=str(social_cfg.get("persona_name", "")) or default_name
        )
        self._build_entry(
            rp_card,
            self._ui_copy("persona_name"),
            self._persona_name_var,
            **pad,
        )
        self._persona_prompt_box = self._build_multiline_entry(
            rp_card,
            self._ui_copy("persona_prompt"),
            str(social_cfg.get("persona_prompt", "")) or default_prompt,
            height=82,
            **pad,
        )
        self._persona_glossary_box = self._build_multiline_entry(
            rp_card,
            self._ui_copy("persona_glossary"),
            str(social_cfg.get("persona_glossary", "")) or default_glossary,
            height=88,
            **pad,
        )
        self._build_hint_box(rp_card, self._ui_copy("persona_glossary_hint"))

    def _build_hint_box(self, parent, text: str, *, visible: bool = True) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            parent,
            fg_color=BG_SECONDARY,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
        )
        if visible:
            frame.pack(padx=SETTINGS_FIELD_PADX, pady=(0, 4), fill="x")
        if text:
            ctk.CTkLabel(
                frame,
                text=text,
                font=ctk.CTkFont(size=11),
                text_color=TEXT_SEC,
                justify="left",
                wraplength=SETTINGS_HINT_WRAP,
            ).pack(padx=10, pady=8, anchor="w")
        return frame

    def _build_mixline_download_hint(self, parent, text: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            parent,
            fg_color=BG_SECONDARY,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
        )
        frame.pack(padx=SETTINGS_FIELD_PADX, pady=(0, 4), fill="x")
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            frame,
            text=text,
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=max(260, SETTINGS_HINT_WRAP - 150),
        ).grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)

        ctk.CTkButton(
            frame,
            text=self._ui_copy("tts_download_mixline"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=10,
            height=30,
            width=126,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._open_mixline_download,
        ).grid(row=0, column=1, sticky="e", padx=(0, 10), pady=8)
        return frame

    def _build_entry(self, parent, label_text: str, variable: ctk.StringVar, **pack_kwargs):
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(2, 0))
        ctk.CTkEntry(
            parent,
            textvariable=variable,
            fg_color=GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=36,
            text_color=TEXT_PRI,
        ).pack(**pack_kwargs, fill="x")

    def _build_option_entry(
        self,
        parent,
        label_text: str,
        values: list[str],
        variable: ctk.StringVar,
        *,
        command=None,
        max_dropdown_height: int | None = None,
        **pack_kwargs,
    ):
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(2, 0))
        menu = ctk.CTkOptionMenu(
            parent,
            values=values,
            variable=variable,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=12,
            text_color=TEXT_PRI,
            height=36,
            dynamic_resizing=False,
            command=command,
        )
        menu.pack(**pack_kwargs, fill="x")

        if max_dropdown_height is not None:
            self._install_scrollable_option_popup(
                menu,
                max_dropdown_height=max_dropdown_height,
            )

        return menu

    def _install_scrollable_option_popup(
        self,
        menu: ctk.CTkOptionMenu,
        *,
        max_dropdown_height: int,
    ) -> None:
        def _open_dropdown_menu() -> None:
            self._open_scrollable_option_popup(
                menu,
                max_dropdown_height=max_dropdown_height,
            )

        menu._open_dropdown_menu = _open_dropdown_menu

    def _close_active_option_popup(self) -> None:
        popup = self._active_option_popup
        self._active_option_popup = None
        if popup is None:
            return
        try:
            if popup.winfo_exists():
                popup.destroy()
        except Exception:
            pass

    def _open_scrollable_option_popup(
        self,
        menu: ctk.CTkOptionMenu,
        *,
        max_dropdown_height: int,
    ) -> None:
        values = [
            str(value or "").strip()
            for value in menu.cget("values")
            if str(value or "").strip()
        ]
        if not values:
            return

        self._close_active_option_popup()
        menu.update_idletasks()

        border = 1
        scrollbar_width = 14
        visible_rows = max(1, min(len(values), 8))
        width = max(160, int(menu.winfo_width()))

        popup = tk.Toplevel(self)
        self._active_option_popup = popup
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self)
        popup.configure(
            background=GLASS_BORDER,
            borderwidth=border,
            highlightthickness=0,
        )

        container = tk.Frame(
            popup,
            background=CARD_BG,
            borderwidth=0,
            highlightthickness=0,
        )
        container.pack(fill="both", expand=True)

        listbox = tk.Listbox(
            container,
            activestyle="none",
            background=CARD_BG,
            borderwidth=0,
            exportselection=False,
            foreground=TEXT_PRI,
            height=visible_rows,
            highlightthickness=0,
            relief="flat",
            selectbackground="#dfeeff",
            selectborderwidth=0,
            selectforeground=TEXT_PRI,
            font=("Microsoft YaHei UI", 10),
        )
        scrollbar = tk.Scrollbar(
            container,
            orient="vertical",
            width=scrollbar_width,
            command=listbox.yview,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        if len(values) > visible_rows:
            scrollbar.pack(side="right", fill="y")

        def _close_popup() -> None:
            if self._active_option_popup is popup:
                self._active_option_popup = None
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except Exception:
                pass

        def _select(value: str) -> None:
            _close_popup()
            menu._dropdown_callback(value)

        def _select_active(*_) -> str:
            selection = listbox.curselection()
            if selection:
                _select(values[int(selection[0])])
            return "break"

        def _scroll_dropdown(event) -> str:
            delta = getattr(event, "delta", 0)
            if delta:
                steps = max(1, abs(delta) // 120)
                units = -steps if delta > 0 else steps
            elif getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                units = 0
            if units:
                listbox.yview_scroll(units, "units")
            return "break"

        current = menu.get()
        for value in values:
            listbox.insert("end", f"  {value}")

        if current in values:
            current_index = values.index(current)
            listbox.selection_set(current_index)
            listbox.activate(current_index)
            listbox.see(current_index)
        elif values:
            listbox.selection_set(0)
            listbox.activate(0)

        def _focus_inside_popup() -> bool:
            focused = popup.focus_get()
            while focused is not None:
                if focused is popup:
                    return True
                focused = getattr(focused, "master", None)
            return False

        def _close_if_focus_left(*_) -> None:
            popup.after(80, lambda: None if _focus_inside_popup() else _close_popup())

        for widget in (popup, container, listbox, scrollbar):
            widget.bind("<MouseWheel>", _scroll_dropdown, add="+")
            widget.bind("<Button-4>", _scroll_dropdown, add="+")
            widget.bind("<Button-5>", _scroll_dropdown, add="+")
        popup.bind("<FocusOut>", _close_if_focus_left, add="+")
        popup.bind("<Escape>", lambda *_: (_close_popup(), "break")[1], add="+")
        listbox.bind("<Escape>", lambda *_: (_close_popup(), "break")[1], add="+")
        listbox.bind("<Return>", _select_active, add="+")
        listbox.bind("<ButtonRelease-1>", _select_active, add="+")
        listbox.bind(
            "<Motion>",
            lambda event: (
                listbox.selection_clear(0, "end"),
                listbox.selection_set(listbox.nearest(event.y)),
                listbox.activate(listbox.nearest(event.y)),
            ),
            add="+",
        )
        popup.update_idletasks()
        height = min(max_dropdown_height, popup.winfo_reqheight())
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = max(4, min(menu.winfo_rootx(), screen_width - width - 4))
        below_y = menu.winfo_rooty() + menu.winfo_height() + 3
        space_below = screen_height - below_y - 8
        space_above = menu.winfo_rooty() - 8
        if height > space_below and space_above > space_below:
            y = max(4, menu.winfo_rooty() - height - 3)
        else:
            y = below_y

        popup.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        popup.deiconify()
        popup.lift()
        listbox.focus_force()

    def _build_multiline_entry(
        self,
        parent,
        label_text: str,
        initial_text: str,
        *,
        height: int = 72,
        **pack_kwargs,
    ) -> ctk.CTkTextbox:
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(2, 0))
        box = ctk.CTkTextbox(
            parent,
            height=height,
            fg_color=GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            text_color=TEXT_PRI,
            wrap="word",
        )
        box.pack(**pack_kwargs, fill="x")
        if initial_text:
            box.insert("1.0", initial_text)
        return box

    def _build_switch_entry(
        self,
        parent,
        label_text: str,
        variable: ctk.StringVar,
        *,
        command=None,
        **pack_kwargs,
    ) -> ctk.CTkSwitch:
        switch = ctk.CTkSwitch(
            parent,
            text=label_text,
            variable=variable,
            onvalue="1",
            offvalue="0",
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color=ACCENT,
            command=command,
        )
        switch.pack(anchor="w", padx=pack_kwargs.get("padx", 12), pady=pack_kwargs.get("pady", (2, 0)))
        return switch

    def sync_vrc_listen_state(
        self,
        *,
        enabled: bool | None = None,
        show_overlay: bool | None = None,
        send_to_chatbox: bool | None = None,
    ) -> None:
        listen_cfg = self._config.setdefault("vrc_listen", {})
        if enabled is not None:
            listen_cfg["enabled"] = bool(enabled)
            if hasattr(self, "_vrc_listen_enabled_var") and self._vrc_listen_enabled_var is not None:
                self._vrc_listen_enabled_var.set("1" if enabled else "0")
        if show_overlay is not None:
            listen_cfg["show_overlay"] = bool(show_overlay)
            if hasattr(self, "_listen_overlay_enabled_var") and self._listen_overlay_enabled_var is not None:
                self._listen_overlay_enabled_var.set("1" if show_overlay else "0")
        if send_to_chatbox is not None:
            listen_cfg["send_to_chatbox"] = bool(send_to_chatbox)
            if hasattr(self, "_listen_send_to_chatbox_var") and self._listen_send_to_chatbox_var is not None:
                self._listen_send_to_chatbox_var.set("1" if send_to_chatbox else "0")

    def _on_vrc_listen_enabled_toggled(self) -> None:
        if self._on_listen_state_changed is None:
            return
        try:
            enabled = self._vrc_listen_enabled_var.get() == "1"
        except Exception:
            return
        try:
            self._on_listen_state_changed(enabled, None)
        except Exception:
            logger.exception("Live vrc_listen.enabled callback failed")

    def _on_listen_overlay_toggled(self) -> None:
        if self._on_listen_state_changed is None:
            return
        try:
            show_overlay = self._listen_overlay_enabled_var.get() == "1"
        except Exception:
            return
        try:
            self._on_listen_state_changed(None, show_overlay)
        except Exception:
            logger.exception("Live vrc_listen.show_overlay callback failed")

    def _on_listen_send_to_chatbox_toggled(self) -> None:
        if self._on_listen_state_changed is None:
            return
        try:
            send_to_chatbox = self._listen_send_to_chatbox_var.get() == "1"
        except Exception:
            return
        try:
            self._on_listen_state_changed(None, None, send_to_chatbox)
        except Exception:
            logger.exception("Live vrc_listen.send_to_chatbox callback failed")

    def sync_tts_state(
        self,
        *,
        enabled: bool | None = None,
        output_to_vrchat: bool | None = None,
        output_device: object | None = None,
        output_device_name: str | None = None,
        monitor_enabled: bool | None = None,
    ) -> None:
        tts_cfg = self._config.setdefault("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
            self._config["tts"] = tts_cfg
        if enabled is not None:
            tts_cfg["enabled"] = bool(enabled)
            if hasattr(self, "_tts_enabled_var") and self._tts_enabled_var is not None:
                self._tts_enabled_var.set("1" if enabled else "0")
            self._apply_tts_enabled_state()
        if output_to_vrchat is not None:
            tts_cfg["output_to_vrchat"] = bool(output_to_vrchat)
            if hasattr(self, "_tts_output_to_vrchat_var"):
                self._tts_output_to_vrchat_var.set("1" if output_to_vrchat else "0")
        if output_device is not None:
            tts_cfg["output_device"] = output_device
            self._tts_virtual_device_id = output_device
        if output_device_name is not None:
            tts_cfg["output_device_name"] = output_device_name
            self._tts_virtual_device_name = str(output_device_name or "").strip() or None
        if monitor_enabled is not None:
            tts_cfg["monitor_enabled"] = bool(monitor_enabled)
            if hasattr(self, "_tts_monitor_var"):
                self._tts_monitor_var.set("1" if monitor_enabled else "0")

    def destroy(self):
        on_close = getattr(self, "_on_close", None)
        self._on_close = None
        manager = getattr(self, "_tts_test_manager", None)
        self._tts_test_manager = None
        self._close_active_option_popup()
        layout_after_id = getattr(self, "_settings_layout_after_id", None)
        if layout_after_id:
            try:
                self.after_cancel(str(layout_after_id))
            except Exception:
                pass
            self._settings_layout_after_id = None
        for state in getattr(self, "_section_cards", []):
            for key in ("after_id", "measure_after_id"):
                after_id = state.get(key)
                if after_id:
                    try:
                        self.after_cancel(str(after_id))
                    except Exception:
                        pass
                    state[key] = None
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass
        try:
            super().destroy()
        finally:
            if on_close:
                try:
                    on_close()
                except Exception:
                    pass

    def _refresh_custom_tts_voice_status(self) -> None:
        label = getattr(self, "_tts_custom_voice_status_label", None)
        if label is None:
            return
        count = len(list_imported_style_bert_models())
        label.configure(
            text=self._ui_copy("tts_custom_voice_count").format(count=count)
        )

    def _selected_tts_bert_language(self) -> str:
        variable = getattr(self, "_tts_bert_language_var", None)
        selected = str(variable.get() if variable is not None else "").strip()
        return getattr(self, "_tts_bert_language_codes", {}).get(selected, "jp")

    def _selected_tts_bert_model_id(self) -> str:
        return style_bert_bert_model_id(self._selected_tts_bert_language())

    def _selected_tts_bert_language_label(self) -> str:
        language = self._selected_tts_bert_language()
        label = getattr(self, "_tts_bert_language_reverse", {}).get(language)
        if not label:
            label = STYLE_BERT_LANGUAGE_NAMES.get(language, language.upper())
        for separator in ("（", "("):
            if separator in label:
                label = label.split(separator, 1)[0]
        return label.strip()

    def _refresh_bert_model_prompt(self) -> None:
        frame = getattr(self, "_bert_info_frame", None)
        label = getattr(self, "_bert_info_label", None)
        button = getattr(self, "_bert_download_btn", None)
        if frame is None or label is None or button is None:
            return

        engine = self._tts_engine_codes.get(
            self._tts_engine_var.get(),
            DEFAULT_TTS_ENGINE,
        ) if hasattr(self, "_tts_engine_codes") else DEFAULT_TTS_ENGINE
        model_id = self._selected_tts_bert_model_id()
        if engine != "style_bert_vits2" or model_is_complete(model_id):
            try:
                frame.pack_forget()
            except Exception:
                pass
            return

        language_name = self._selected_tts_bert_language_label()
        try:
            label.configure(
                text=self._ui_copy("tts_bert_model_info").format(
                    language=language_name,
                    model_id=model_id,
                )
            )
            button.configure(text=self._ui_copy("tts_bert_download_btn"))
        except Exception:
            pass
        if not frame.winfo_ismapped():
            try:
                frame.pack(**getattr(self, "_bert_info_pack_options", {}))
            except Exception:
                pass

    def _import_custom_tts_voice_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title=self._ui_copy("tts_import_custom_voice"),
        )
        if not selected:
            return

        try:
            imported = import_style_bert_model_path(Path(selected))
        except StyleBertVits2ModelError as exc:
            messagebox.showerror(
                self._t("error_title"),
                self._ui_copy("tts_custom_voice_import_failed").format(
                    message=str(exc)
                ),
            )
            return

        self._refresh_custom_tts_voice_status()
        current_engine = self._tts_engine_codes.get(
            self._tts_engine_var.get(),
            DEFAULT_TTS_ENGINE,
        )
        if current_engine == "style_bert_vits2":
            self._refresh_tts_voice_options(current_engine, preferred="")

        messagebox.showinfo(
            self._ui_copy("tts_import_custom_voice"),
            self._ui_copy("tts_custom_voice_import_done").format(
                count=len(imported)
            ),
        )





    def _tts_engine_options(self) -> list[tuple[str, str]]:
        return [
            (self._ui_copy(f"tts_engine_{engine}"), engine)
            for engine in TTS_ENGINE_IDS
        ]

    @staticmethod
    def _normalized_tts_engine(value: object) -> str:
        engine = str(value or "").strip().lower()
        return engine if engine in TTS_ENGINE_IDS else DEFAULT_TTS_ENGINE

    @staticmethod
    def _tts_engine_config(tts_cfg: dict, engine: str) -> dict:
        engine_cfg = tts_cfg.get(engine, {}) if isinstance(tts_cfg, dict) else {}
        return engine_cfg if isinstance(engine_cfg, dict) else {}

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

    @staticmethod
    def _default_tts_voice_options(engine: str) -> tuple[str, ...]:
        return TTS_DEFAULT_VOICES.get(engine, ())

    def _tts_placeholder_values(self) -> set[str]:
        return {
            self._ui_copy("tts_voice_loading"),
            self._ui_copy("tts_voice_none"),
            self._ui_copy("tts_voice_local_service_unavailable"),
            self._ui_copy("tts_voice_style_runtime_missing"),
            self._ui_copy("tts_voice_custom_missing"),
        }

    def _is_tts_placeholder(self, value: str) -> bool:
        return str(value or "").strip() in self._tts_placeholder_values()

    def _tts_unavailable_voice_label(self, engine: str) -> str:
        if engine in {"voicevox", "aivis_speech"}:
            return self._ui_copy("tts_voice_local_service_unavailable")
        if engine == "style_bert_vits2":
            return self._ui_copy(
                "tts_voice_custom_missing"
                if style_bert_runtime_available()
                else "tts_voice_style_runtime_missing"
            )
        return self._ui_copy("tts_voice_none")

    def _set_tts_voice_options(
        self,
        values: list[str],
        *,
        preferred: str = "",
        available: bool,
        display_names: dict[str, str] | None = None,
    ) -> None:
        menu = getattr(self, "_tts_voice_menu", None)
        variable = getattr(self, "_tts_voice_var", None)
        if menu is None or variable is None:
            return

        display_names = display_names or {}
        clean_values = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                clean_values.append(text)
                seen.add(text)
        if not clean_values:
            clean_values = [self._ui_copy("tts_voice_none")]
            available = False

        display_values: list[str] = []
        display_to_id: dict[str, str] = {}
        id_to_display: dict[str, str] = {}
        for value in clean_values:
            label = str(display_names.get(value) or value).strip() or value
            if label in display_to_id and display_to_id[label] != value:
                label = f"{label} [{value}]"
            display_values.append(label)
            display_to_id[label] = value
            id_to_display[value] = label

        self._tts_voice_display_to_id = display_to_id
        self._tts_voice_id_to_display = id_to_display
        menu.configure(values=display_values)
        preferred = str(preferred or "").strip()
        current_label = str(variable.get() or "").strip()
        current = display_to_id.get(current_label, current_label)
        default_voice = self._default_tts_voice(
            self._tts_engine_codes.get(self._tts_engine_var.get(), DEFAULT_TTS_ENGINE)
            if hasattr(self, "_tts_engine_codes")
            else DEFAULT_TTS_ENGINE
        )
        if preferred and preferred in clean_values:
            variable.set(id_to_display.get(preferred, preferred))
        elif current and current in clean_values and not self._is_tts_placeholder(current):
            variable.set(id_to_display.get(current, current))
        elif default_voice and default_voice in clean_values:
            variable.set(id_to_display.get(default_voice, default_voice))
        else:
            variable.set(id_to_display.get(clean_values[0], clean_values[0]))
        self._tts_engine_available = bool(available) and not self._is_tts_placeholder(
            self._selected_tts_voice_id()
        )
        self._apply_tts_enabled_state()

    def _refresh_tts_voice_options(self, engine: str, *, preferred: str = "") -> None:
        bert_language = self._selected_tts_bert_language() if engine == "style_bert_vits2" else "jp"
        fallback_values = list(self._default_tts_voice_options(engine))
        if preferred and preferred not in fallback_values:
            fallback_values.insert(0, preferred)
        if fallback_values:
            self._set_tts_voice_options(
                fallback_values,
                preferred=preferred,
                available=True,
            )
        else:
            self._set_tts_voice_options(
                [self._ui_copy("tts_voice_loading")],
                preferred=preferred,
                available=False,
            )

        self._tts_voice_load_generation += 1
        generation = self._tts_voice_load_generation
        threading.Thread(
            target=self._load_tts_voice_options,
            args=(engine, preferred, generation, bert_language),
            daemon=True,
        ).start()

    def _load_tts_voice_options(
        self,
        engine: str,
        preferred: str,
        generation: int,
        bert_language: str = "jp",
    ) -> None:
        values: list[str] = []
        available = False
        display_names: dict[str, str] = {}
        try:
            tts_engine = create_tts_engine(
                engine,
                bert_language=bert_language,
            )
            available = bool(tts_engine and tts_engine.is_available())
            if available and tts_engine is not None:
                voices = tts_engine.get_available_voices()
                values = [str(getattr(voice, "id", "") or "").strip() for voice in voices]
                values = [value for value in values if value]
                display_names = {
                    str(getattr(voice, "id", "") or "").strip(): str(
                        getattr(voice, "name", "") or getattr(voice, "id", "") or ""
                    ).strip()
                    for voice in voices
                    if str(getattr(voice, "id", "") or "").strip()
                }

                # Sort and prioritize voices for Edge TTS
                if engine == "edge" and values:
                    values = self._sort_and_filter_edge_voices(values)
            elif engine == "style_bert_vits2":
                voices = StyleBertVits2TTS(bert_language=bert_language).get_available_voices()
                values = [str(getattr(voice, "id", "") or "").strip() for voice in voices]
                values = [value for value in values if value]
                display_names = {
                    str(getattr(voice, "id", "") or "").strip(): str(
                        getattr(voice, "name", "") or getattr(voice, "id", "") or ""
                    ).strip()
                    for voice in voices
                    if str(getattr(voice, "id", "") or "").strip()
                }
        except Exception:
            logger.exception("Failed to load TTS voices for engine=%s", engine)
            available = False
            values = []

        if available and not values:
            values = list(self._default_tts_voice_options(engine))
        elif not available and not values:
            values = [self._tts_unavailable_voice_label(engine)]

        self._schedule_if_alive(
            lambda: self._apply_loaded_tts_voice_options(
                engine,
                preferred,
                generation,
                values,
                available=available,
                display_names=display_names,
            )
        )

    def _sort_and_filter_edge_voices(self, voices: list[str]) -> list[str]:
        """Sort and filter Edge TTS voices to prioritize Chinese, Japanese, and English.

        Keep only top 3 female voices for each priority language.
        """
        # Define priority languages and their top female voices
        priority_voices = {
            "zh-CN": ["XiaoxiaoNeural", "XiaoyiNeural", "YunyangNeural"],
            "ja-JP": ["NanamiNeural", "AoiNeural", "MayuNeural"],
            "en-US": ["JennyNeural", "AriaNeural", "MichelleNeural"],
        }

        # Separate voices into priority and others
        priority_list = []
        other_list = []

        for voice in voices:
            matched = False
            for locale, preferred_names in priority_voices.items():
                if voice.startswith(f"{locale}-"):
                    # Extract the voice name (e.g., "XiaoxiaoNeural" from "zh-CN-XiaoxiaoNeural")
                    voice_name = voice.split("-", 2)[-1] if "-" in voice else ""
                    if voice_name in preferred_names:
                        priority_list.append(voice)
                        matched = True
                        break

            if not matched:
                other_list.append(voice)

        # Sort priority voices by the order defined in priority_voices
        sorted_priority = []
        for locale, preferred_names in priority_voices.items():
            for name in preferred_names:
                full_voice = f"{locale}-{name}"
                if full_voice in priority_list:
                    sorted_priority.append(full_voice)

        # Combine: priority voices first, then others
        return sorted_priority + sorted(other_list)

    def _apply_loaded_tts_voice_options(
        self,
        engine: str,
        preferred: str,
        generation: int,
        values: list[str],
        *,
        available: bool,
        display_names: dict[str, str] | None = None,
    ) -> None:
        if generation != self._tts_voice_load_generation:
            return
        current_engine = self._tts_engine_codes.get(
            self._tts_engine_var.get(),
            DEFAULT_TTS_ENGINE,
        )
        if engine != current_engine:
            return
        self._set_tts_voice_options(
            values,
            preferred=preferred,
            available=available,
            display_names=display_names,
        )

    def _selected_tts_voice_id(self) -> str:
        variable = getattr(self, "_tts_voice_var", None)
        selected = str(variable.get() if variable is not None else "").strip()
        return getattr(self, "_tts_voice_display_to_id", {}).get(selected, selected)

    def _on_tts_engine_change(self, selected_label: str) -> None:
        engine = self._tts_engine_codes.get(selected_label, DEFAULT_TTS_ENGINE)
        tts_cfg = self._config.get("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
        engine_cfg = self._tts_engine_config(tts_cfg, engine)
        if hasattr(self, "_tts_rate_var"):
            self._tts_rate_var.set(self._safe_tts_rate(engine_cfg.get("rate"), engine=engine))
            self._update_tts_rate_label(self._tts_rate_var.get())
        if hasattr(self, "_tts_volume_var"):
            self._tts_volume_var.set(self._safe_tts_volume(engine_cfg.get("volume")))
            self._update_tts_volume_label(self._tts_volume_var.get())
        preferred_voice = str(engine_cfg.get("voice") or self._default_tts_voice(engine) or "")
        self._refresh_tts_voice_options(engine, preferred=preferred_voice)
        self._refresh_bert_model_prompt()
        self._apply_tts_enabled_state()

    def _on_tts_bert_language_change(self, *_args) -> None:
        self._refresh_bert_model_prompt()
        self._schedule_settings_layout_refresh()
        current_engine = self._tts_engine_codes.get(
            self._tts_engine_var.get(),
            DEFAULT_TTS_ENGINE,
        )
        if current_engine == "style_bert_vits2":
            self._refresh_tts_voice_options(
                current_engine,
                preferred=self._selected_tts_voice_id(),
            )

    def _on_tts_enabled_change(self) -> None:
        self._apply_tts_enabled_state()

    def _apply_tts_enabled_state(self) -> None:
        enabled = hasattr(self, "_tts_enabled_var") and self._tts_enabled_var.get() == "1"
        for attr_name in (
            "_tts_enabled_switch",
            "_tts_engine_menu",
            "_tts_voice_menu",
            "_tts_rate_slider",
            "_tts_volume_slider",
            "_tts_output_to_vrchat_switch",
            "_tts_monitor_switch",
            "_tts_auto_read_switch",
        ):
            control = getattr(self, attr_name, None)
            if control is None:
                continue
            try:
                control.configure(state="normal")
            except Exception:
                pass
        test_button = getattr(self, "_tts_test_button", None)
        if test_button is not None:
            state = (
                "normal"
                if enabled and self._tts_engine_available and not self._tts_testing
                else "disabled"
            )
            try:
                test_button.configure(state=state)
            except Exception:
                pass

    def _update_tts_rate_label(self, value: float) -> None:
        label = getattr(self, "_tts_rate_label", None)
        if label is not None:
            label.configure(text=f"{float(value):.1f}x")

    def _update_tts_volume_label(self, value: float) -> None:
        label = getattr(self, "_tts_volume_label", None)
        if label is not None:
            label.configure(text=f"{int(round(float(value) * 100))}%")

    def _set_tts_testing(self, testing: bool) -> None:
        self._tts_testing = testing
        button = getattr(self, "_tts_test_button", None)
        if button is not None:
            button.configure(
                text=self._ui_copy("tts_testing") if testing else self._ui_copy("tts_test"),
            )
        self._apply_tts_enabled_state()

    def _tts_test_text(self) -> str:
        return {
            "zh-CN": "你好，这是语音测试。",
            "en": "Hello, this is a voice test.",
            "ja": "こんにちは。これは音声テストです。",
            "ru": "Здравствуйте, это проверка голоса.",
            "ko": "안녕하세요. 음성 테스트입니다.",
        }.get(self._ui_lang, "Hello, this is a voice test.")

    def _test_tts(self) -> None:
        if self._tts_testing:
            return
        if self._tts_enabled_var.get() != "1":
            return
        engine = self._tts_engine_codes.get(self._tts_engine_var.get(), DEFAULT_TTS_ENGINE)
        voice = self._selected_tts_voice_id()
        if self._is_tts_placeholder(voice):
            messagebox.showerror(self._t("error_title"), self._ui_copy("tts_unavailable"))
            return

        # Get output device based on switch state
        output_to_vrchat = self._tts_output_to_vrchat_var.get() == "1"
        output_device = self._tts_virtual_device_id if output_to_vrchat else None
        output_device_name = self._tts_virtual_device_name if output_to_vrchat else ""
        monitor_output = self._tts_monitor_var.get() == "1"
        device_var = getattr(self, "_tts_device_var", None)
        device_label = str(device_var.get() if device_var is not None else "").strip()
        device = getattr(self, "_tts_device_codes", {}).get(device_label, "cpu")
        bert_language = self._selected_tts_bert_language()

        try:
            manager = TTSManager(
                engine_name=engine,
                cache_enabled=False,
                allow_fallback=False,
                output_device=output_device,
                output_device_name=output_device_name,
                prefer_virtual_output=output_to_vrchat,
                monitor_output=monitor_output,
                sbv2_device=device,
                sbv2_bert_language=bert_language,
            )
            if not manager.is_available():
                raise RuntimeError(self._ui_copy("tts_unavailable"))
            manager.start()
        except Exception as exc:
            messagebox.showerror(
                self._t("error_title"),
                self._ui_copy("tts_test_failed").format(message=str(exc)),
            )
            return

        self._tts_test_manager = manager
        self._set_tts_testing(True)

        def _callback(success: bool, message: str) -> None:
            self._schedule_if_alive(
                lambda: self._finish_tts_test(success, message)
            )

        test_text = (
            "こんにちは。これは音声テストです。"
            if engine in {"voicevox", "aivis_speech", "style_bert_vits2"}
            else self._tts_test_text()
        )
        accepted = manager.speak(
            test_text,
            voice,
            self._safe_tts_rate(self._tts_rate_var.get(), engine=engine),
            self._safe_tts_volume(self._tts_volume_var.get()),
            callback=_callback,
        )
        if not accepted:
            self._finish_tts_test(False, self._ui_copy("tts_unavailable"))

    def _finish_tts_test(self, success: bool, message: str) -> None:
        manager = self._tts_test_manager
        self._tts_test_manager = None
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass
        self._set_tts_testing(False)
        if not success:
            messagebox.showerror(
                self._t("error_title"),
                self._ui_copy("tts_test_failed").format(message=message or ""),
            )

    def _download_bert_model(self) -> None:
        """Launch download dialog for the selected Style-Bert-VITS2 BERT model."""

        model_id = self._selected_tts_bert_model_id()
        language_name = self._selected_tts_bert_language_label()

        # Check if already downloaded
        if model_is_complete(model_id):
            messagebox.showinfo(
                self._t("info_title"),
                self._ui_copy("tts_bert_already_downloaded").format(
                    language=language_name,
                ),
            )
            return

        # Create downloader
        downloader = get_downloader(model_id)
        if downloader is None:
            messagebox.showerror(
                self._t("error_title"),
                "Failed to create downloader for BERT model.",
            )
            return

        # Create a simple download progress dialog
        dialog = ctk.CTkToplevel(self)
        dialog.title(self._ui_copy("tts_bert_download_btn"))
        dialog.geometry("500x200")
        dialog.transient(self)
        dialog.grab_set()
        apply_window_icon(dialog)

        # Center dialog
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            content,
            text=self._ui_copy("tts_bert_downloading").format(
                language=language_name,
            ),
            font=ctk.CTkFont(size=13),
        ).pack(pady=(0, 15))

        from src.ui.model_download_dialog import DownloadProgressWidget

        def _on_download_completed() -> None:
            try:
                dialog.destroy()
            finally:
                self._refresh_bert_model_prompt()

        progress_widget = DownloadProgressWidget(
            content,
            engine="style_bert_vits2_bert",
            downloader=downloader,
            model_id=model_id,
            on_completed=_on_download_completed,
            on_cancelled=lambda: dialog.destroy(),
            ui_lang=self._ui_lang,
        )
        progress_widget.pack(fill="x")
        downloader.start()

    def _find_best_virtual_device(
        self, devices: list[tuple[int, str]]
    ) -> tuple[int, str] | None:
        """Find the best virtual audio device from the list."""
        best = find_best_virtual_output_device()
        if best is not None:
            return best

        virtual_keywords = [
            "mixline",
            "mix line",
        ]

        for device_id, device_name in devices:
            name_lower = device_name.lower()
            if any(keyword in name_lower for keyword in virtual_keywords):
                return (device_id, device_name)

        return None

    def _open_mixline_download(self) -> None:
        import webbrowser

        webbrowser.open_new_tab(MIXLINE_DOWNLOAD_URL)

    def _show_virtual_device_guide(self) -> None:
        """Show guide for installing virtual audio device."""

        # Create custom dialog
        dialog = ctk.CTkToplevel(self)
        dialog.title(self._ui_copy("tts_no_virtual_device"))
        dialog.geometry("500x450")
        dialog.transient(self)
        dialog.grab_set()
        apply_window_icon(dialog)

        # Center the dialog
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        # Content frame
        content_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        content_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # Guide text
        guide_text = ctk.CTkTextbox(
            content_frame,
            wrap="word",
            height=300,
            fg_color=GLASS_BG,
            border_width=1,
            border_color=GLASS_BORDER,
        )
        guide_text.pack(fill="both", expand=True, pady=(0, 15))
        guide_text.insert("1.0", self._ui_copy("tts_install_virtual_device"))
        guide_text.configure(state="disabled")

        # Buttons frame
        buttons_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        buttons_frame.pack(fill="x")

        # Download button
        download_btn = ctk.CTkButton(
            buttons_frame,
            text=self._ui_copy("tts_download_mixline"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._open_mixline_download,
        )
        download_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Close button
        close_btn = ctk.CTkButton(
            buttons_frame,
            text=self._ui_copy("close"),
            fg_color=GLASS_BG,
            hover_color=GLASS_HOVER,
            border_width=1,
            border_color=GLASS_BORDER,
            text_color=TEXT_PRI,
            height=40,
            command=dialog.destroy,
        )
        close_btn.pack(side="left", fill="x", expand=True)

    def _apply_roleplay_preset(self, preset_id: str) -> None:
        preset = ROLEPLAY_PRESETS.get(preset_id)
        if preset is None or preset_id == "custom":
            return
        self._persona_name_var.set(str(preset.get("persona_name", "")).strip())
        if self._persona_prompt_box is not None:
            self._persona_prompt_box.delete("1.0", "end")
            self._persona_prompt_box.insert("1.0", str(preset.get("persona_prompt", "")).strip())
        if self._persona_glossary_box is not None:
            self._persona_glossary_box.delete("1.0", "end")
            self._persona_glossary_box.insert("1.0", str(preset.get("persona_glossary", "")).strip())

    def _on_roleplay_preset_change(self, selected_label: str) -> None:
        preset_id = self._roleplay_preset_codes.get(selected_label, "custom")
        self._apply_roleplay_preset(preset_id)

    def _translation_locked(self) -> bool:
        output_format = self._fmt_codes.get(self._fmt_var.get(), OUTPUT_FORMAT_OPTIONS[0][1])
        return output_format == "original_only"

    def _add_backend_field(
        self,
        label_text: str,
        value: str,
        *,
        secret: bool = False,
        readonly: bool = False,
        bind_key: str | None = None,
    ) -> None:
        ctk.CTkLabel(
            self._fields_frame,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=6, pady=(6, 0))

        variable = ctk.StringVar(value=value)
        entry = ctk.CTkEntry(
            self._fields_frame,
            textvariable=variable,
            show="*" if secret else "",
            fg_color=BG_SECONDARY if readonly else GLASS_BG,
            border_color=GLASS_BORDER,
            corner_radius=12,
            height=36,
            text_color=TEXT_SEC if readonly else TEXT_PRI,
            state="disabled" if readonly else "normal",
        )
        entry.pack(fill="x", padx=4, pady=(0, 4))
        if bind_key is not None:
            self._field_vars[bind_key] = variable
            self._editable_backend_entries.append(entry)
        else:
            self._readonly_backend_entries.append(entry)

    def _add_backend_option_field(
        self,
        label_text: str,
        values: tuple[str, ...],
        value: str,
        *,
        bind_key: str,
        command=None,
    ) -> None:
        ctk.CTkLabel(
            self._fields_frame,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=6, pady=(6, 0))

        options = list(values or ((value or "").strip(),))
        selected = value if value in options else options[0]
        variable = ctk.StringVar(value=selected)
        menu = ctk.CTkOptionMenu(
            self._fields_frame,
            values=options,
            variable=variable,
            fg_color=GLASS_BG,
            button_color=GLASS_BORDER,
            button_hover_color=GLASS_HOVER,
            dropdown_fg_color=CARD_BG,
            dropdown_hover_color="#e8f2ff",
            dropdown_text_color=TEXT_PRI,
            corner_radius=12,
            text_color=TEXT_PRI,
            height=36,
            dynamic_resizing=False,
            command=command,
        )
        menu.pack(fill="x", padx=4, pady=(0, 4))
        self._field_vars[bind_key] = variable
        self._editable_backend_menus.append(menu)

    def _set_backend_model_hint(self, text: str) -> None:
        if not self._backend_model_hint_box or not self._backend_model_hint_label:
            return
        hint = str(text or "").strip()
        if hint:
            self._backend_model_hint_label.configure(text=hint)
            if not self._backend_model_hint_box.winfo_manager():
                self._backend_model_hint_box.pack(
                    padx=SETTINGS_FIELD_PADX,
                    pady=(0, 6),
                    fill="x",
                )
        else:
            self._backend_model_hint_label.configure(text="")
            if self._backend_model_hint_box.winfo_manager():
                self._backend_model_hint_box.pack_forget()
        self._schedule_settings_layout_refresh()

    def _on_backend_change(self, selected_label: str):
        for widget in self._fields_frame.winfo_children():
            widget.destroy()

        self._field_vars = {}
        self._editable_backend_entries = []
        self._editable_backend_menus = []
        self._readonly_backend_entries = []
        self._model_info_title_label = None
        self._model_info_body_label = None
        self._model_info_badges = None
        self._set_backend_model_hint("")

        backend = self._backend_codes.get(selected_label, BACKEND_ORDER[0])
        trans_cfg = self._config.get("translation", {})
        backend_cfg = trans_cfg.get(backend, {})
        current_model = str(backend_cfg.get("model") or get_backend_value(backend, "model"))
        base_url_editable = backend_base_url_is_editable(backend)

        self._add_backend_field(
            self._t("api_key"),
            str(backend_cfg.get("api_key", "")),
            secret=True,
            bind_key="api_key",
        )
        self._add_backend_field(
            self._t("base_url"),
            get_backend_config_value(trans_cfg, backend, "base_url"),
            readonly=not base_url_editable,
            bind_key="base_url" if base_url_editable else None,
        )
        if backend_model_is_selectable(backend):
            self._add_backend_option_field(
                self._t("model"),
                get_backend_model_options(backend, current_model),
                current_model,
                bind_key="model",
                command=self._on_model_change,
            )
        else:
            self._add_backend_field(
                self._t("model"),
                current_model,
                bind_key="model",
            )
        self._build_model_info_card(backend, current_model)
        hints = [
            hint
            for hint in (
                get_backend_api_key_hint(backend),
                get_backend_model_hint(backend),
            )
            if hint
        ]
        self._set_backend_model_hint("\n".join(hints))
        self._apply_translation_mode_state()
        self._schedule_settings_layout_refresh()

    def _on_output_format_change(self, _selected_label: str):
        self._apply_translation_mode_state()

    def _apply_translation_mode_state(self):
        locked = self._translation_locked()
        state = "disabled" if locked else "normal"

        self._backend_menu.configure(state=state)
        for entry in self._editable_backend_entries:
            entry.configure(
                state=state,
                fg_color=BG_SECONDARY if locked else GLASS_BG,
                text_color=TEXT_SEC if locked else TEXT_PRI,
            )
        for menu in self._editable_backend_menus:
            menu.configure(
                state=state,
                fg_color=BG_SECONDARY if locked else GLASS_BG,
                button_color=BG_SECONDARY if locked else GLASS_BORDER,
                button_hover_color=BG_SECONDARY if locked else GLASS_HOVER,
                text_color=TEXT_SEC if locked else TEXT_PRI,
            )

        if self._translation_lock_box is not None and self._translation_lock_label is not None:
            if locked:
                self._translation_lock_label.configure(text=self._ui_copy("translation_lock_on"))
                if not self._translation_lock_box.winfo_manager():
                    self._translation_lock_box.pack(padx=SETTINGS_FIELD_PADX, pady=(0, 6), fill="x")
            else:
                self._translation_lock_label.configure(text="")
                if self._translation_lock_box.winfo_manager():
                    self._translation_lock_box.pack_forget()
        self._schedule_settings_layout_refresh()

    def _set_dictionary_custom_status(self, message: str, *, error: bool = False) -> None:
        if self._dictionary_custom_status_label is None:
            return
        self._dictionary_custom_status_label.configure(
            text=message,
            text_color="#a63d2f" if error else "#1f6b3d",
        )

    def _save_dictionary_custom_entry(self) -> None:
        replacement_var = self._dictionary_custom_replacement_var
        patterns_box = self._dictionary_custom_patterns_box
        if replacement_var is None or patterns_box is None:
            return

        replacement = replacement_var.get().strip()
        try:
            patterns_text = patterns_box.get("1.0", "end").strip()
        except tk.TclError:
            return

        if not replacement:
            message = self._ui_copy("settings_dictionary_custom_missing_replacement")
            self._set_dictionary_custom_status(message, error=True)
            messagebox.showwarning(self._t("error_title"), message)
            return

        if not patterns_text:
            message = self._ui_copy("settings_dictionary_custom_missing_patterns")
            self._set_dictionary_custom_status(message, error=True)
            messagebox.showwarning(self._t("error_title"), message)
            return

        if self._dictionary_custom_save_button is not None:
            self._dictionary_custom_save_button.configure(state="disabled")

        try:
            result = upsert_user_dictionary_entry(replacement, patterns_text)
        except Exception as exc:
            message = self._ui_copy("settings_dictionary_custom_failed").format(
                message=str(exc)
            )
            self._set_dictionary_custom_status(message, error=True)
            messagebox.showerror(self._t("error_title"), message)
            return
        finally:
            if self._dictionary_custom_save_button is not None:
                self._dictionary_custom_save_button.configure(state="normal")

        total = int(result.get("pattern_count", 0))
        saved_replacement = str(result.get("replacement") or replacement)
        message = self._ui_copy("settings_dictionary_custom_saved").format(
            replacement=saved_replacement,
            total=total,
        )
        self._set_dictionary_custom_status(message)
        try:
            patterns_box.delete("1.0", "end")
        except tk.TclError:
            pass
        self._refresh_dictionary_status()
        messagebox.showinfo(self._t("info_title"), message)

    def _refresh_dictionary_status(self) -> None:
        if self._dictionary_status_label is None:
            return
        status = dictionary_status()
        layer_bits: list[str] = []
        for layer in status.get("layers", []):
            name = str(layer.get("name", "")).capitalize()
            count = int(layer.get("entry_count", 0))
            version = str(layer.get("version", "")).strip()
            if version:
                layer_bits.append(f"{name}: {count} ({version})")
            else:
                layer_bits.append(f"{name}: {count}")
        summary = " | ".join(layer_bits) if layer_bits else self._t("dictionary_status_empty")
        user_path = status.get("user_path", "")
        self._dictionary_status_label.configure(
            text=self._t(
                "dictionary_status_summary",
                summary=summary,
                path=user_path,
            )
        )

    def _set_dictionary_updating(self, updating: bool) -> None:
        self._dictionary_updating = updating
        if self._dictionary_update_button is not None:
            self._dictionary_update_button.configure(
                state="disabled" if updating else "normal",
                text=self._t("dictionary_updating") if updating else self._ui_copy("settings_dictionary_update"),
            )

    def _start_dictionary_update(self) -> None:
        if self._dictionary_updating:
            return
        self._set_dictionary_updating(True)
        threading.Thread(target=self._run_dictionary_update, daemon=True).start()

    def _schedule_if_alive(self, callback) -> bool:
        try:
            if not self.winfo_exists():
                return False
        except Exception:
            return False
        try:
            self.after(0, callback)
            return True
        except Exception:
            return False

    def _run_dictionary_update(self) -> None:
        try:
            result = update_official_dictionary(self._config)
        except Exception as exc:
            self._schedule_if_alive(
                lambda m=str(exc): self._on_dictionary_update_failed(m)
            )
            return
        self._schedule_if_alive(
            lambda r=result: self._on_dictionary_update_finished(r)
        )

    def _on_dictionary_update_finished(self, result: dict) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._set_dictionary_updating(False)
        self._refresh_dictionary_status()
        if result.get("changed"):
            messagebox.showinfo(
                self._t("dictionary_update_done_title"),
                self._t(
                    "dictionary_update_done_message",
                    count=int(result.get("entry_count", 0)),
                    version=str(result.get("version", "")).strip() or "latest",
                ),
            )
            return
        messagebox.showinfo(
            self._t("dictionary_update_done_title"),
            self._t("dictionary_update_no_change_message"),
        )

    def _on_dictionary_update_failed(self, message: str) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._set_dictionary_updating(False)
        messagebox.showerror(
            self._t("dictionary_update_failed_title"),
            message,
        )

    # ── Check for updates (manual) ──────────────────────────────────────────

    def _start_check_update(self) -> None:
        btn = getattr(self, "_check_update_btn", None)
        if btn is None:
            return
        btn.configure(
            state="disabled",
            text=self._ui_copy("settings_checking_update"),
        )

        def _on_update(update_info: UpdateInfo) -> None:
            self._schedule_if_alive(
                lambda: self._on_check_update_found(update_info)
            )

        def _on_no_update() -> None:
            self._schedule_if_alive(self._on_check_update_none)

        def _on_error(_msg: str) -> None:
            self._schedule_if_alive(self._on_check_update_error)

        check_for_update(
            _on_update,
            on_no_update=_on_no_update,
            on_error=_on_error,
        )

    def _reset_check_update_btn(self) -> None:
        btn = getattr(self, "_check_update_btn", None)
        if btn is None:
            return
        btn.configure(state="normal", text=self._ui_copy("settings_check_update"))

    def _on_check_update_found(self, update_info: UpdateInfo) -> None:
        self._reset_check_update_btn()
        parent = self.master
        if parent is not None:
            if hasattr(parent, "_handle_update_available"):
                try:
                    parent._handle_update_available(update_info, auto_open=False)
                except Exception:
                    parent._pending_update = update_info
            else:
                parent._pending_update = update_info
            try:
                parent._open_update_window()
            except Exception:
                pass

    def _on_check_update_none(self) -> None:
        self._reset_check_update_btn()
        btn = getattr(self, "_check_update_btn", None)
        if btn is not None:
            btn.configure(text=self._ui_copy("settings_up_to_date"))
            self.after(3000, self._reset_check_update_btn)

    def _on_check_update_error(self) -> None:
        self._reset_check_update_btn()
        btn = getattr(self, "_check_update_btn", None)
        if btn is not None:
            btn.configure(text=self._ui_copy("settings_check_update_failed"))
            self.after(4000, self._reset_check_update_btn)

    # ── Validation helpers ────────────────────────────────────────────────

    def _parse_positive_float(self, value: str, field_name: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_number", field=field_name)) from exc
        if parsed <= 0:
            raise ValueError(self._t("must_be_positive", field=field_name))
        return parsed

    def _parse_positive_int(self, value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_integer", field=field_name)) from exc
        if parsed <= 0:
            raise ValueError(self._t("must_be_positive", field=field_name))
        return parsed

    def _parse_int_range(self, value: str, field_name: str, min_val: int, max_val: int) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_integer", field=field_name)) from exc
        if parsed < min_val or parsed > max_val:
            raise ValueError(f"{field_name}: must be between {min_val} and {max_val}")
        return parsed

    def _parse_float_range(self, value: str, field_name: str, min_val: float, max_val: float) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError(self._t("must_be_number", field=field_name)) from exc
        if parsed < min_val or parsed > max_val:
            raise ValueError(f"{field_name}: must be between {min_val} and {max_val}")
        return parsed

    def _save(self):
        self._ensure_all_lazy_sections_built()
        target_language_options = get_target_language_options(ui_language=self._ui_lang)
        backend = self._backend_codes.get(self._backend_var.get(), BACKEND_ORDER[0])
        target_lang = self._lang_codes.get(
            self._lang_var.get(), target_language_options[0][1]
        )
        output_format = self._fmt_codes.get(
            self._fmt_var.get(), OUTPUT_FORMAT_OPTIONS[0][1]
        )
        asr_engine = self._asr_codes.get(self._asr_var.get(), DEFAULT_ASR_ENGINE)
        ui_language = self._ui_lang_codes.get(
            self._ui_lang_var.get(), UI_LANGUAGE_OPTIONS[0][1]
        )
        try:
            text_input_hotkey = normalize_hotkey(self._text_input_hotkey_var.get())
        except HotkeyError as exc:
            messagebox.showerror(
                self._t("error_title"),
                f"{self._ui_copy('text_input_hotkey')}: {exc}",
            )
            return

        try:
            vad_threshold = self._parse_positive_float(
                self._vad_var.get(), self._ui_copy("settings_vad_seconds")
            )
            chunk_interval_ms = self._parse_positive_int(
                self._chunk_interval_var.get(),
                self._ui_copy("settings_partial_refresh_interval"),
            )
            chunk_window_s = self._parse_positive_float(
                self._chunk_window_var.get(),
                self._ui_copy("settings_recognition_window_length"),
            )
            partial_hits = self._parse_positive_int(
                self._partial_hits_var.get(),
                self._ui_copy("settings_partial_hits"),
            )
            listen_self_suppress_seconds = self._parse_positive_float(
                self._listen_self_suppress_seconds_var.get(),
                self._ui_copy("vrc_listen_self_suppress_seconds"),
            )
            listen_segment_duration_s = self._parse_positive_float(
                self._listen_segment_duration_var.get(),
                self._ui_copy("vrc_listen_segment_duration"),
            )
            listen_tail_silence_s = self._parse_positive_float(
                self._listen_tail_silence_var.get(),
                self._ui_copy("vrc_listen_tail_silence"),
            )
        except ValueError as exc:
            messagebox.showerror(self._t("error_title"), str(exc))
            return

        if chunk_window_s * 1000 < chunk_interval_ms:
            messagebox.showerror(
                self._t("error_title"),
                self._t("window_must_not_be_less_than_interval"),
            )
            return

        cfg = self._config
        translation_cfg = cfg.setdefault("translation", {})
        translation_cfg["backend"] = backend
        translation_cfg["backend_source"] = "manual"
        translation_cfg["language_pair_source"] = "manual"
        translation_cfg["target_language"] = target_lang
        translation_cfg["output_format"] = normalize_output_format(output_format)
        translation_cfg["send_to_chatbox"] = self._mic_send_to_chatbox_var.get() == "1"
        social_cfg = translation_cfg.setdefault("social", {})
        selected_preset = self._roleplay_preset_codes.get(
            self._roleplay_preset_var.get(),
            "custom",
        )
        preset_profile = ROLEPLAY_PRESETS.get(selected_preset, ROLEPLAY_PRESETS["custom"])
        social_cfg["mode"] = "roleplay" if self._roleplay_enabled_var.get() == "1" else "standard"
        social_cfg["persona_preset"] = selected_preset
        social_cfg["politeness"] = str(preset_profile.get("politeness", "neutral"))
        social_cfg["tone"] = str(preset_profile.get("tone", "natural"))
        social_cfg["persona_name"] = self._persona_name_var.get().strip()
        social_cfg["persona_prompt"] = (
            self._persona_prompt_box.get("1.0", "end").strip()
            if self._persona_prompt_box is not None
            else ""
        )
        social_cfg["persona_glossary"] = (
            self._persona_glossary_box.get("1.0", "end").strip()
            if self._persona_glossary_box is not None
            else ""
        )
        translation_cfg.setdefault(backend, {})
        translation_cfg[backend]["api_key"] = self._field_vars["api_key"].get().strip()
        base_url_var = self._field_vars.get("base_url")
        translation_cfg[backend]["base_url"] = (
            base_url_var.get().strip()
            if base_url_var is not None
            else get_backend_config_value(translation_cfg, backend, "base_url")
        )
        translation_cfg[backend]["model"] = (
            self._field_vars["model"].get().strip() or get_backend_value(backend, "model")
        )

        audio_cfg = cfg.setdefault("audio", {})
        audio_cfg["vad_silence_threshold"] = vad_threshold
        audio_cfg["denoise_strength"] = self._denoise_codes.get(
            self._denoise_var.get(),
            0.0,
        )

        # Input device settings
        input_device_mode = self._input_mode_codes.get(
            self._input_device_mode_var.get(),
            "auto",
        )
        audio_cfg["input_device_mode"] = input_device_mode

        input_device = self._input_device_var.get().strip()
        if input_device in {
            self._ui_copy("settings_input_device_default"),
            self._ui_copy("settings_input_device_missing"),
        }:
            input_device = ""
        audio_cfg["input_device"] = input_device or None

        # VAD advanced settings
        try:
            vad_sensitivity = self._parse_int_range(
                self._vad_sensitivity_var.get(),
                self._ui_copy("settings_vad_sensitivity"),
                0,
                3,
            )
            vad_speech_ratio = self._parse_float_range(
                self._vad_speech_ratio_var.get(),
                self._ui_copy("settings_vad_speech_ratio"),
                0.0,
                1.0,
            )
            vad_activation_threshold = self._parse_positive_float(
                self._vad_activation_threshold_var.get(),
                self._ui_copy("settings_vad_activation_threshold"),
            )
            vad_min_rms = self._parse_positive_float(
                self._vad_min_rms_var.get(),
                self._ui_copy("settings_vad_min_rms"),
            )
            min_segment_s = self._parse_positive_float(
                self._min_segment_var.get(),
                self._ui_copy("settings_min_segment"),
            )
            max_segment_s = self._parse_positive_float(
                self._max_segment_var.get(),
                self._ui_copy("settings_max_segment"),
            )
            partial_min_speech_s = self._parse_positive_float(
                self._partial_min_speech_var.get(),
                self._ui_copy("settings_partial_min_speech"),
            )
        except ValueError as exc:
            messagebox.showerror(self._t("error_title"), str(exc))
            return

        audio_cfg["vad_sensitivity"] = vad_sensitivity
        audio_cfg["vad_speech_ratio"] = vad_speech_ratio
        audio_cfg["vad_activation_threshold_s"] = vad_activation_threshold
        audio_cfg["vad_min_rms"] = vad_min_rms
        audio_cfg["min_segment_s"] = min_segment_s
        audio_cfg["max_segment_s"] = max_segment_s
        audio_cfg["partial_min_speech_s"] = partial_min_speech_s
        vrc_cfg = cfg.setdefault("vrc_listen", {})
        loopback_device = self._loopback_device_var.get().strip()
        if loopback_device in {
            self._ui_copy("vrc_listen_device_default"),
            self._ui_copy("vrc_listen_device_missing"),
        }:
            loopback_device = ""
        vrc_cfg["enabled"] = self._vrc_listen_enabled_var.get() == "1"
        vrc_cfg["show_overlay"] = self._listen_overlay_enabled_var.get() == "1"
        vrc_cfg["send_to_chatbox"] = self._listen_send_to_chatbox_var.get() == "1"
        vrc_cfg["loopback_device"] = loopback_device or None
        vrc_cfg["asr_engine"] = getattr(self, "_listen_asr_engine_codes", {}).get(
            self._listen_asr_engine_var.get(),
            ASR_ENGINE_FOLLOW_MAIN,
        )
        vrc_cfg["source_language"] = self._listen_src_codes.get(
            self._listen_source_lang_var.get(),
            "auto",
        )
        vrc_cfg["target_language"] = self._listen_lang_codes.get(
            self._listen_target_lang_var.get(),
            target_language_options[0][1],
        )
        vrc_cfg["segment_duration_s"] = listen_segment_duration_s
        vrc_cfg["tail_silence_s"] = listen_tail_silence_s
        vrc_cfg["self_suppress"] = self._listen_self_suppress_var.get() == "1"
        vrc_cfg["self_suppress_seconds"] = listen_self_suppress_seconds

        asr_cfg = cfg.setdefault("asr", {})
        asr_cfg["engine"] = asr_engine
        asr_cfg["engine_source"] = "manual"
        asr_cfg["user_selected_engine"] = True
        asr_cfg.setdefault("sensevoice", {})
        if hasattr(self, "_qwen_api_key_var"):
            qwen_cfg = asr_cfg.setdefault("qwen3_asr", {})
            qwen_region = self._selected_qwen_region()
            qwen_cfg["api_key"] = self._qwen_api_key_var.get().strip()
            qwen_cfg["region"] = qwen_region
            qwen_cfg["base_url"] = (
                get_qwen3_asr_base_url(qwen_region)
                if qwen_region != "custom"
                else self._qwen_base_url_var.get().strip().rstrip("/")
            )
            qwen_cfg["model"] = self._selected_qwen_model()
            qwen_cfg.setdefault("language", "ja")
            qwen_cfg.setdefault("mode", "vad_chunked")
            qwen_cfg.setdefault("sample_rate", 16000)
            qwen_cfg.setdefault("max_segment_seconds", 6.0)
            qwen_cfg.setdefault("tail_silence_seconds", 0.7)
            qwen_cfg.setdefault("overlap_ms", 300)
            qwen_cfg.setdefault("timeout_seconds", 15)
        if hasattr(self, "_gemini_api_key_var"):
            gemini_cfg = asr_cfg.setdefault("gemini_live", {})
            gemini_cfg["api_key"] = self._gemini_api_key_var.get().strip()
            gemini_cfg["model"] = self._gemini_model_var.get().strip() or "gemini-3.1-flash-live-preview"
            gemini_cfg.setdefault("language", "ja-JP")
            gemini_cfg.setdefault("transcribe_only", True)
            gemini_cfg.setdefault(
                "system_instruction",
                (
                    "You are a Japanese speech-to-text engine. Output only the transcription. "
                    "Do not translate, summarize, explain, or answer."
                ),
            )
            gemini_cfg.setdefault("timeout_seconds", 20)
            gemini_cfg.setdefault("use_live_api", True)
            gemini_cfg.setdefault("live_silence_duration_ms", 600)

        streaming_cfg = asr_cfg.setdefault("streaming", {})
        streaming_cfg["chunk_interval_ms"] = chunk_interval_ms
        streaming_cfg["chunk_window_s"] = chunk_window_s
        streaming_cfg["partial_stability_hits"] = partial_hits
        streaming_cfg["ring_buffer_s"] = max(
            float(streaming_cfg.get("ring_buffer_s", 4.0)),
            chunk_window_s,
        )
        streaming_cfg.setdefault("recent_speech_hold_s", 0.8)

        ui_cfg = cfg.setdefault("ui", {})
        ui_cfg["language"] = ui_language
        ui_cfg["language_source"] = "manual"
        ui_cfg.setdefault("osc_guide_seen", False)

        tts_engine = self._tts_engine_codes.get(
            self._tts_engine_var.get(),
            DEFAULT_TTS_ENGINE,
        )
        tts_cfg = cfg.get("tts", {})
        if not isinstance(tts_cfg, dict):
            tts_cfg = {}
            cfg["tts"] = tts_cfg
        tts_enabled = self._tts_enabled_var.get() == "1"
        tts_cfg["enabled"] = tts_enabled
        tts_cfg["engine"] = tts_engine
        tts_cfg["auto_read"] = self._tts_auto_read_var.get() == "1"
        tts_cfg["monitor_enabled"] = self._tts_monitor_var.get() == "1"
        cfg["app_mode"] = "simultaneous" if tts_enabled else "translation"
        simul_cfg = cfg.setdefault("simul_mode", {})
        if isinstance(simul_cfg, dict):
            simul_cfg["tts_backend"] = tts_engine
        tts_engine_cfg = tts_cfg.get(tts_engine, {})
        if not isinstance(tts_engine_cfg, dict):
            tts_engine_cfg = {}
            tts_cfg[tts_engine] = tts_engine_cfg
        tts_voice = self._selected_tts_voice_id()
        if self._is_tts_placeholder(tts_voice):
            tts_voice = self._default_tts_voice(tts_engine)
        tts_engine_cfg["voice"] = tts_voice or None
        tts_engine_cfg["rate"] = self._safe_tts_rate(
            self._tts_rate_var.get(),
            engine=tts_engine,
        )
        tts_engine_cfg["volume"] = self._safe_tts_volume(
            self._tts_volume_var.get()
        )

        # Save Style-Bert-VITS2-only settings even when another engine is selected.
        style_bert_cfg = tts_cfg.setdefault("style_bert_vits2", {})
        if isinstance(style_bert_cfg, dict):
            device_var = getattr(self, "_tts_device_var", None)
            if device_var is not None:
                device_code = getattr(self, "_tts_device_codes", {}).get(
                    device_var.get(),
                    "cpu",
                )
                style_bert_cfg["device"] = device_code
            bert_language_var = getattr(self, "_tts_bert_language_var", None)
            if bert_language_var is not None:
                style_bert_cfg["bert_language"] = self._selected_tts_bert_language()

        # Save TTS output device - auto-select virtual device if enabled
        output_to_vrchat = self._tts_output_to_vrchat_var.get() == "1"
        tts_cfg["output_to_vrchat"] = output_to_vrchat
        if output_to_vrchat:
            resolved_tts_device = resolve_output_device(
                self._tts_virtual_device_id,
                self._tts_virtual_device_name,
                prefer_virtual=True,
            )
            if resolved_tts_device is None:
                resolved_tts_device = find_best_virtual_output_device()
            if resolved_tts_device is not None:
                tts_cfg["output_device"], tts_cfg["output_device_name"] = resolved_tts_device
            else:
                tts_cfg["output_device"] = None
                tts_cfg["output_device_name"] = ""
        else:
            tts_cfg["output_device"] = None
            tts_cfg["output_device_name"] = ""

        text_input_cfg = cfg.setdefault("text_input_window", {})
        text_input_cfg["hotkey"] = text_input_hotkey

        osc_cfg = cfg.setdefault("osc", {})
        avatar_cfg = osc_cfg.setdefault("avatar_sync", {})
        avatar_params = avatar_cfg.setdefault("params", {})
        if hasattr(self, "_avatar_sync_enabled_var"):
            avatar_cfg["enabled"] = self._avatar_sync_enabled_var.get() == "1"
        avatar_fields = {
            "translating": "_avatar_translating_var",
            "speaking": "_avatar_speaking_var",
            "error": "_avatar_error_var",
            "target_language": "_avatar_target_language_var",
        }
        for key, attr_name in avatar_fields.items():
            variable = getattr(self, attr_name, None)
            if variable is not None:
                avatar_params[key] = variable.get().strip()

        try:
            config_manager.save_config(cfg)
        except Exception as exc:
            logger.exception("Failed to save settings")
            messagebox.showerror(self._t("error_title"), str(exc))
            return

        on_save = getattr(self, "_on_save", None)
        parent = self.master

        try:
            if self.winfo_exists():
                self.destroy()
        except Exception:
            pass

        if not on_save:
            return

        def _apply_saved_config() -> None:
            try:
                on_save(cfg)
            except Exception as exc:
                logger.exception("Failed to apply saved settings")
                try:
                    messagebox.showerror(self._t("error_title"), str(exc), parent=parent)
                except Exception:
                    pass

        try:
            if parent is not None and parent.winfo_exists():
                parent.after(0, _apply_saved_config)
                return
        except Exception:
            pass
        _apply_saved_config()

