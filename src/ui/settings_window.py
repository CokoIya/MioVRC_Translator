from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

from src.utils import config_manager
from src.utils.i18n import tr
from src.utils.ui_config import (
    BACKEND_ORDER,
    DEFAULT_ASR_ENGINE,
    OUTPUT_FORMAT_OPTIONS,
    TARGET_LANGUAGE_OPTIONS,
    UI_LANGUAGE_OPTIONS,
    get_backend_label,
    get_backend_model_options,
    get_backend_model_profile,
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

WINDOW_COPY = {
    "header_title": {
        "zh-CN": "插件设置",
        "en": "Plugin Settings",
        "ja": "プラグイン設定",
    },
    "header_subtitle": {
        "zh-CN": "针对实时翻译做了默认整理，优先照顾延迟、稳定性和普通用户的可用性。",
        "en": "Curated for live translation with an emphasis on low latency, stability, and simple defaults.",
        "ja": "リアルタイム翻訳向けに、遅延と安定性、使いやすさを優先して整理しています。",
    },
    "translation_section": {
        "zh-CN": "翻译",
        "en": "Translation",
        "ja": "翻訳",
    },
    "translation_subtitle": {
        "zh-CN": "这里决定翻译后端、目标语言和模型。模型说明会直接告诉你速度和是否适合插件实时翻译。",
        "en": "Choose the backend, target language, and model here. The model notes call out speed and live-plugin suitability.",
        "ja": "翻訳バックエンド、対象言語、モデルをここで設定します。モデルの説明には速度とリアルタイム向きかどうかを表示します。",
    },
    "translation_provider": {
        "zh-CN": "翻译服务",
        "en": "Translation Service",
        "ja": "翻訳サービス",
    },
    "translation_provider_subtitle": {
        "zh-CN": "这里决定翻译服务、目标语言和模型。模型说明会直接告诉你速度和是否适合插件实时翻译。",
        "en": "Choose the translation service, target language, and model here. The model notes call out speed and live-plugin suitability.",
        "ja": "翻訳サービス、対象言語、モデルをここで設定します。モデルの説明には速度とリアルタイム向きかどうかを表示します。",
    },
    "translation_provider_params": {
        "zh-CN": "服务参数",
        "en": "Service Settings",
        "ja": "サービス設定",
    },
    "translation_lock_on": {
        "zh-CN": "仅原文模式下不会调用翻译 API，服务和 API Key 已锁定。",
        "en": "Original-only mode skips translation calls, so the service and API key are locked.",
        "ja": "原文のみモードでは翻訳 API を使わないため、サービスと API Key は固定されます。",
    },
    "voice_section": {
        "zh-CN": "语音输入",
        "en": "Speech Input",
        "ja": "音声入力",
    },
    "voice_subtitle": {
        "zh-CN": "这些参数影响实时监听的响应速度、句尾切分和杂音抑制。",
        "en": "These controls affect live-listening responsiveness, sentence splitting, and noise suppression.",
        "ja": "これらの設定は、リアルタイム音声入力の反応速度、文末判定、ノイズ抑制に影響します。",
    },
    "model_title": {
        "zh-CN": "模型说明",
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

    def _build_section_card(self, parent, title: str, subtitle: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=CARD_BG,
            corner_radius=22,
            border_width=1,
            border_color=CARD_BORDER,
        )
        card.pack(padx=SETTINGS_CARD_PADX, pady=(0, 10), fill="x")

        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRI,
        ).pack(anchor="w", padx=12, pady=(12, 2))

        ctk.CTkLabel(
            card,
            text=subtitle,
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=SETTINGS_TEXT_WRAP,
        ).pack(anchor="w", padx=12, pady=(0, 10))
        return card

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
        pad = {"padx": SETTINGS_FIELD_PADX, "pady": 6}
        trans_cfg = self._config.get("translation", {})
        asr_cfg = self._config.get("asr", {})
        streaming_cfg = asr_cfg.get("streaming", {})
        audio_cfg = self._config.get("audio", {})

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        def section_label(parent, text: str):
            ctk.CTkLabel(
                parent,
                text=text,
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=TEXT_SEC,
            ).pack(padx=12, pady=(8, 2), anchor="w")

        self._build_header_card(scroll)

        translation_card = self._build_section_card(
            scroll,
            self._ui_copy("translation_section"),
            self._ui_copy("translation_provider_subtitle"),
        )

        section_label(translation_card, self._t("app_language"))
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

        section_label(translation_card, self._t("target_language"))
        lang_labels = [label for label, _ in TARGET_LANGUAGE_OPTIONS]
        self._lang_codes = {label: code for label, code in TARGET_LANGUAGE_OPTIONS}
        self._lang_reverse = {code: label for label, code in TARGET_LANGUAGE_OPTIONS}
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

        section_label(translation_card, self._t("output_format"))
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
        self._build_hint_box(translation_card, self._t("output_hint"))

        section_label(translation_card, self._ui_copy("translation_provider_params"))
        self._fields_frame = ctk.CTkFrame(
            translation_card,
            fg_color=BG_SECONDARY,
            corner_radius=16,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self._fields_frame.pack(padx=SETTINGS_FIELD_PADX, pady=(4, 6), fill="x")
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
        )

        section_label(voice_card, self._t("asr_backend"))
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
            text=self._t("asr_hint_sensevoice"),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=SETTINGS_HINT_WRAP,
        )
        self._asr_hint_label.pack(padx=10, pady=8, anchor="w")

        section_label(voice_card, self._t("streaming_params"))
        self._chunk_interval_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_interval_ms", 250))
        )
        self._chunk_window_var = ctk.StringVar(
            value=str(streaming_cfg.get("chunk_window_s", 1.6))
        )
        self._partial_hits_var = ctk.StringVar(
            value=str(streaming_cfg.get("partial_stability_hits", 2))
        )

        self._build_entry(voice_card, self._t("partial_refresh_interval"), self._chunk_interval_var, **pad)
        self._build_entry(voice_card, self._t("recognition_window_length"), self._chunk_window_var, **pad)
        self._build_entry(voice_card, self._t("partial_hits"), self._partial_hits_var, **pad)
        self._build_hint_box(voice_card, self._t("streaming_hint"))

        section_label(voice_card, self._t("vad_silence_threshold"))
        self._vad_var = ctk.StringVar(
            value=str(audio_cfg.get("vad_silence_threshold", 0.8))
        )
        self._build_entry(voice_card, self._t("vad_silence_label"), self._vad_var, **pad)

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
            frame.pack(padx=SETTINGS_FIELD_PADX, pady=(0, 6), fill="x")
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
        ).pack(**pack_kwargs, fill="x")

    def _translation_locked(self) -> bool:
        return self._fmt_codes.get(self._fmt_var.get(), OUTPUT_FORMAT_OPTIONS[0][1]) == "original_only"

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
        self._add_backend_option_field(
            self._t("model"),
            get_backend_model_options(backend, current_model),
            current_model,
            bind_key="model",
            command=self._on_model_change,
        )
        self._build_model_info_card(backend, current_model)
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
        backend = self._backend_codes.get(self._backend_var.get(), BACKEND_ORDER[0])
        target_lang = self._lang_codes.get(
            self._lang_var.get(), TARGET_LANGUAGE_OPTIONS[0][1]
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
                self._vad_var.get(), self._t("vad_silence_label")
            )
            chunk_interval_ms = self._parse_positive_int(
                self._chunk_interval_var.get(),
                self._t("partial_refresh_interval"),
            )
            chunk_window_s = self._parse_positive_float(
                self._chunk_window_var.get(),
                self._t("recognition_window_length"),
            )
            partial_hits = self._parse_positive_int(
                self._partial_hits_var.get(),
                self._t("partial_hits"),
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

        config_manager.save_config(cfg)
        if self._on_save:
            self._on_save(cfg)
        self.destroy()

