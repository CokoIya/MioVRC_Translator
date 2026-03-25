from __future__ import annotations

import threading
import time

import customtkinter as ctk
from tkinter import messagebox

from src.utils import config_manager
from src.audio.recorder import AudioRecorder
from src.asr.text_corrections import dictionary_status, update_official_dictionary
from src.utils.i18n import tr
from src.utils.ui_config import (
    BACKEND_ORDER,
    DEFAULT_ASR_ENGINE,
    OUTPUT_FORMAT_OPTIONS,
    UI_LANGUAGE_OPTIONS,
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
SECTION_ANIMATION_INTERVAL_MS = 16
SECTION_ANIMATION_DURATION_MS = 160

ASR_ENGINES = [("SenseVoice Small", DEFAULT_ASR_ENGINE)]
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
        "settings_app_language": {
            "zh-CN": "界面语言",
            "en": "Interface Language",
            "ja": "表示言語",
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
        "vrc_listen_self_suppress_seconds_hint": "По умолчанию 0.65 сек. Увеличьте это значение, если программа все еще цепляет ваш голос. Слишком большое значение может пропускать начало чужой речи.",
        "avatar_section": "Синхронизация Avatar",
        "avatar_subtitle": "Отправляет состояние перевода в Avatar. Если не нужно, можно не трогать.",
        "avatar_sync_enabled": "Включить синхронизацию Avatar",
        "avatar_sync_hint": "Рекомендуемые параметры: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
        "avatar_param_translating": "Параметр перевода",
        "avatar_param_speaking": "Параметр речи",
        "avatar_param_error": "Параметр ошибки",
        "avatar_param_target_language": "Параметр целевого языка",
        "settings_app_language": "Язык интерфейса",
        "settings_target_language": "На какой язык переводить",
        "settings_output_format": "Как отправлять в чат",
        "settings_output_format_hint": "Перевод (Оригинал): сначала перевод, потом оригинал\nТолько перевод: отправлять только перевод\nТолько оригинал: без AI, отправлять только оригинал\nОригинал (Перевод): сначала оригинал, потом перевод",
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
        "vrc_listen_self_suppress_seconds_hint": "기본값은 0.65초입니다. 아직도 내 목소리를 잡으면 조금 늘려 보세요. 너무 크면 다른 사람이 막 말하기 시작한 부분도 건너뛸 수 있습니다.",
        "avatar_section": "Avatar 동기화",
        "avatar_subtitle": "번역 상태를 Avatar로 보냅니다. 필요 없으면 건드리지 않아도 됩니다.",
        "avatar_sync_enabled": "Avatar 동기화 켜기",
        "avatar_sync_hint": "권장 파라미터: MioTranslating / MioSpeaking / MioError / MioTargetLanguage",
        "avatar_param_translating": "번역 중 파라미터",
        "avatar_param_speaking": "말하는 중 파라미터",
        "avatar_param_error": "오류 파라미터",
        "avatar_param_target_language": "목표 언어 파라미터",
        "settings_app_language": "화면 언어",
        "settings_target_language": "어떤 언어로 번역할지",
        "settings_output_format": "채팅창에 보내는 형식",
        "settings_output_format_hint": "번역문 (원문): 번역문을 먼저 보내고 뒤에 원문을 붙입니다\n번역문만: 번역 결과만 보냅니다\n원문만: AI 번역 없이 원문만 보냅니다\n원문 (번역문): 원문을 먼저 보내고 뒤에 번역문을 붙입니다",
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


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: dict, on_save=None):
        super().__init__(parent)
        self._config = config
        self._on_save = on_save
        self._ui_lang = get_ui_language(config)

        self.title(tr(self._ui_lang, "settings_window_title"))
        apply_window_icon(self)
        self.geometry(f"{SETTINGS_WINDOW_WIDTH}x{SETTINGS_WINDOW_HEIGHT}")
        self._popup_size = (SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(fg_color=BG_PRIMARY)

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
        self._persona_prompt_box: ctk.CTkTextbox | None = None
        self._persona_glossary_box: ctk.CTkTextbox | None = None
        self._section_cards: list[dict[str, object]] = []
        self._build()
        self.after(0, lambda: present_popup(self, parent=parent))

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _ui_copy(self, key: str) -> str:
        values = WINDOW_COPY.get(key, {})
        if self._ui_lang in values:
            return values[self._ui_lang]
        base_lang = self._ui_lang.split("-", 1)[0]
        for lang, text in values.items():
            if lang.split("-", 1)[0] == base_lang:
                return text
        return values.get("en", "")

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
        ).pack(anchor="w", padx=12, pady=(0, 12))

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

    def _apply_section_height(self, state: dict[str, object], height: int) -> None:
        wrap = state["content_wrap"]
        if not isinstance(wrap, ctk.CTkFrame):
            return
        height = max(0, int(height))
        if int(state.get("rendered_height", -1)) == height:
            return
        wrap.configure(height=height)
        state["rendered_height"] = height

    def _on_section_content_configure(self, state: dict[str, object]) -> None:
        target_height = self._section_content_height(state, force_measure=True)
        if bool(state.get("collapsed", False)) or bool(state.get("animating", False)):
            return
        self._apply_section_height(state, target_height)

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
                return
            state["after_id"] = self.after(SECTION_ANIMATION_INTERVAL_MS, step)

        step()

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
        target_height = 0 if collapsed else self._section_content_height(
            state,
            force_measure=True,
        )
        if not animate:
            state["animating"] = False
            self._apply_section_height(state, target_height)
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

    def _initialize_section_cards(self) -> None:
        self.update_idletasks()
        for state in self._section_cards:
            self._section_content_height(state, force_measure=True)
            self._set_section_collapsed(
                state,
                bool(state.get("collapsed", False)),
                animate=False,
            )

    def _build_section_card(
        self,
        parent,
        title: str,
        subtitle: str,
        *,
        collapsed: bool = False,
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
        content.pack(fill="x")

        state = {
            "card": card,
            "header": header,
            "arrow": arrow,
            "content_wrap": content_wrap,
            "content": content,
            "collapsed": collapsed,
            "after_id": None,
            "cached_height": 1,
            "rendered_height": -1,
            "animating": False,
            "animation_generation": 0,
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

    def _build(self):
        pad = {"padx": SETTINGS_FIELD_PADX, "pady": 4}
        trans_cfg = self._config.get("translation", {})
        asr_cfg = self._config.get("asr", {})
        streaming_cfg = asr_cfg.get("streaming", {})
        audio_cfg = self._config.get("audio", {})
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
            }
        else:
            vrc_cfg.setdefault("source_language", "auto")
            vrc_cfg.setdefault("target_language", "zh")
            vrc_cfg.setdefault("self_suppress", False)
            vrc_cfg.setdefault("self_suppress_seconds", 0.65)
            vrc_cfg.setdefault("show_overlay", False)
        osc_cfg = self._config.get("osc", {})
        avatar_cfg = osc_cfg.get("avatar_sync", {}) if isinstance(osc_cfg.get("avatar_sync", {}), dict) else {}
        avatar_params = avatar_cfg.get("params", {}) if isinstance(avatar_cfg.get("params", {}), dict) else {}

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        def _fast_scroll(event, canvas=scroll._parent_canvas):
            canvas.yview_scroll(int(-1 * (event.delta / 30)), "units")

        def _bind_scroll_recursive(widget):
            widget.bind("<MouseWheel>", _fast_scroll, add="+")
            for child in widget.winfo_children():
                _bind_scroll_recursive(child)

        scroll.bind("<Configure>", lambda *_: _bind_scroll_recursive(scroll), add="+")
        _bind_scroll_recursive(scroll)

        def section_label(parent, text: str):
            ctk.CTkLabel(
                parent,
                text=text,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=TEXT_SEC,
            ).pack(padx=12, pady=(4, 2), anchor="w")

        self._build_header_card(scroll)

        translation_card = self._build_section_card(
            scroll,
            self._ui_copy("translation_section"),
            self._ui_copy("translation_provider_subtitle"),
            collapsed=False,
        )

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
        )

        section_label(voice_card, self._ui_copy("settings_asr_backend"))
        asr_labels = [label for label, _ in ASR_ENGINES]
        self._asr_codes = {label: code for label, code in ASR_ENGINES}
        self._asr_reverse = {code: label for label, code in ASR_ENGINES}
        current_engine = asr_cfg.get("engine", DEFAULT_ASR_ENGINE)
        self._asr_var = ctk.StringVar(
            value=self._asr_reverse.get(current_engine, asr_labels[0])
        )
        self._asr_menu = ctk.CTkOptionMenu(
            voice_card,
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
            state="disabled",
        )
        self._asr_menu.pack(**pad, fill="x")

        self._asr_hint_label = ctk.CTkLabel(
            self._build_hint_box(voice_card, ""),
            text=self._ui_copy("settings_asr_hint"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._asr_hint_label.pack(padx=10, pady=8, anchor="w")

        vrc_listen_card = self._build_section_card(
            scroll,
            self._ui_copy("vrc_listen_section"),
            self._ui_copy("vrc_listen_subtitle"),
            collapsed=True,
        )

        self._vrc_listen_enabled_var = ctk.StringVar(
            value="1" if bool(vrc_cfg.get("enabled", False)) else "0"
        )
        self._build_switch_entry(
            vrc_listen_card,
            self._ui_copy("vrc_listen_enabled"),
            self._vrc_listen_enabled_var,
            **pad,
        )

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
        loopback_devices = AudioRecorder.list_loopback_devices()
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
            value=str(audio_cfg.get("vad_silence_threshold", 0.8))
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

        avatar_card = self._build_section_card(
            scroll,
            self._ui_copy("avatar_section"),
            self._ui_copy("avatar_subtitle"),
            collapsed=True,
        )

        self._avatar_sync_enabled_var = ctk.StringVar(
            value="1" if bool(avatar_cfg.get("enabled", False)) else "0"
        )
        self._build_switch_entry(
            avatar_card,
            self._ui_copy("avatar_sync_enabled"),
            self._avatar_sync_enabled_var,
        )
        self._build_hint_box(avatar_card, self._ui_copy("avatar_sync_hint"))

        self._avatar_translating_var = ctk.StringVar(
            value=str(avatar_params.get("translating", "MioTranslating"))
        )
        self._build_entry(
            avatar_card,
            self._ui_copy("avatar_param_translating"),
            self._avatar_translating_var,
            **pad,
        )
        self._avatar_speaking_var = ctk.StringVar(
            value=str(avatar_params.get("speaking", "MioSpeaking"))
        )
        self._build_entry(
            avatar_card,
            self._ui_copy("avatar_param_speaking"),
            self._avatar_speaking_var,
            **pad,
        )
        self._avatar_error_var = ctk.StringVar(
            value=str(avatar_params.get("error", "MioError"))
        )
        self._build_entry(
            avatar_card,
            self._ui_copy("avatar_param_error"),
            self._avatar_error_var,
            **pad,
        )
        self._avatar_target_language_var = ctk.StringVar(
            value=str(avatar_params.get("target_language", "MioTargetLanguage"))
        )
        self._build_entry(
            avatar_card,
            self._ui_copy("avatar_param_target_language"),
            self._avatar_target_language_var,
            **pad,
        )

        rp_card = self._build_section_card(
            scroll,
            self._ui_copy("rp_section"),
            self._ui_copy("rp_subtitle"),
            collapsed=True,
        )

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
        self._initialize_section_cards()

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
        **pack_kwargs,
    ):
        ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_SEC,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(2, 0))
        ctk.CTkOptionMenu(
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
        ).pack(**pack_kwargs, fill="x")

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
        )
        switch.pack(anchor="w", padx=pack_kwargs.get("padx", 12), pady=pack_kwargs.get("pady", (2, 0)))
        return switch

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

        self._add_backend_field(
            self._t("api_key"),
            str(backend_cfg.get("api_key", "")),
            secret=True,
            bind_key="api_key",
        )
        self._add_backend_field(
            self._t("base_url"),
            get_backend_value(backend, "base_url"),
            readonly=True,
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
        self._set_backend_model_hint(get_backend_model_hint(backend))
        self._apply_translation_mode_state()

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

    def _run_dictionary_update(self) -> None:
        try:
            result = update_official_dictionary(self._config)
        except Exception as exc:
            self.after(
                0,
                lambda m=str(exc): self._on_dictionary_update_failed(m),
            )
            return
        self.after(
            0,
            lambda r=result: self._on_dictionary_update_finished(r),
        )

    def _on_dictionary_update_finished(self, result: dict) -> None:
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
        self._set_dictionary_updating(False)
        messagebox.showerror(
            self._t("dictionary_update_failed_title"),
            message,
        )

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

    def _save(self):
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
        translation_cfg["target_language"] = target_lang
        translation_cfg["output_format"] = normalize_output_format(output_format)
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
        translation_cfg[backend]["base_url"] = get_backend_value(backend, "base_url")
        translation_cfg[backend]["model"] = (
            self._field_vars["model"].get().strip() or get_backend_value(backend, "model")
        )

        audio_cfg = cfg.setdefault("audio", {})
        audio_cfg["vad_silence_threshold"] = vad_threshold
        audio_cfg["denoise_strength"] = self._denoise_codes.get(
            self._denoise_var.get(),
            0.0,
        )
        vrc_cfg = cfg.setdefault("vrc_listen", {})
        loopback_device = self._loopback_device_var.get().strip()
        if loopback_device in {
            self._ui_copy("vrc_listen_device_default"),
            self._ui_copy("vrc_listen_device_missing"),
        }:
            loopback_device = ""
        vrc_cfg["enabled"] = (
            self._vrc_listen_enabled_var.get() == "1"
            and normalize_output_format(output_format) != "original_only"
        )
        vrc_cfg["loopback_device"] = loopback_device or None
        vrc_cfg["source_language"] = self._listen_src_codes.get(
            self._listen_source_lang_var.get(),
            "auto",
        )
        vrc_cfg["target_language"] = self._listen_lang_codes.get(
            self._listen_target_lang_var.get(),
            target_language_options[0][1],
        )
        vrc_cfg["self_suppress"] = self._listen_self_suppress_var.get() == "1"
        vrc_cfg["self_suppress_seconds"] = listen_self_suppress_seconds

        asr_cfg = cfg.setdefault("asr", {})
        asr_cfg["engine"] = asr_engine
        asr_cfg.setdefault("sensevoice", {})

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

        osc_cfg = cfg.setdefault("osc", {})
        avatar_cfg = osc_cfg.setdefault("avatar_sync", {})
        avatar_cfg["enabled"] = self._avatar_sync_enabled_var.get() == "1"
        avatar_params = avatar_cfg.setdefault("params", {})
        avatar_params["translating"] = self._avatar_translating_var.get().strip()
        avatar_params["speaking"] = self._avatar_speaking_var.get().strip()
        avatar_params["error"] = self._avatar_error_var.get().strip()
        avatar_params["target_language"] = self._avatar_target_language_var.get().strip()

        config_manager.save_config(cfg)
        if self._on_save:
            self._on_save(cfg)
        self.destroy()

