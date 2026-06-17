# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.core.output_dispatcher import OutputDispatcher, OutputMessage
from src.translators.factory import create_translator


@dataclass(frozen=True)
class RealtimeTranslationResult:
    original_text: str
    translated_text: str
    translated_text_2: str = ""
    translated_text_3: str = ""
    display_text: str = ""
    chatbox_text: str = ""
    output_message: OutputMessage | None = None
    api_translation_used: bool = False


@dataclass(frozen=True)
class MicTranslationPlan:
    original_text: str
    source_language: str
    target_language: str
    second_target_language: str
    third_target_language: str
    output_format: str
    include_second_target: bool
    include_third_target: bool
    needs_primary_translation: bool
    needs_second_api_translation: bool
    needs_third_api_translation: bool
    context_source: str = "mic"

    @property
    def needs_api_translation(self) -> bool:
        return bool(
            self.needs_primary_translation
            or self.needs_second_api_translation
            or self.needs_third_api_translation
        )


@dataclass(frozen=True)
class ListenTranslationPlan:
    original_text: str
    source_language: str
    target_language: str
    listen_prefix: str
    context_source: str = "listen"

    @property
    def needs_api_translation(self) -> bool:
        return not (self.source_language != "auto" and self.source_language == self.target_language)


class MicPipeline:
    """Build microphone final-translation output without owning audio threads."""

    def __init__(
        self,
        config: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        output_dispatcher: OutputDispatcher,
        *,
        translator_factory: Callable[[dict], Any] = create_translator,
    ) -> None:
        self._config = config
        self._output_dispatcher = output_dispatcher
        self._translator_factory = translator_factory

    def _current_config(self) -> dict:
        config = self._config() if callable(self._config) else self._config
        return config if isinstance(config, dict) else {}

    def create_plan(
        self,
        text: str,
        *,
        source_language: str | None,
        target_language: str,
        second_target_language: str = "en",
        third_target_language: str = "",
        context_source: str = "mic",
    ) -> MicTranslationPlan:
        original = str(text or "").strip()
        src_lang = str(source_language or "auto") or "auto"
        tgt_lang = str(target_language or "ja") or "ja"
        tgt2_lang = str(second_target_language or "en") or "en"
        tgt3_lang = str(third_target_language or "")
        output_format = self._output_dispatcher.output_format()
        include_second_target = (
            self._output_dispatcher.output_format_uses_second_target()
            or self._output_dispatcher.chatbox_template_uses_second_target()
        )
        include_third_target = bool(tgt3_lang) and self._output_dispatcher.chatbox_template_uses_third_target()
        needs_primary_translation = output_format != "original_only" and not (
            src_lang != "auto" and src_lang == tgt_lang
        )
        needs_second_api_translation = (
            output_format != "original_only"
            and include_second_target
            and tgt2_lang != tgt_lang
            and not (src_lang != "auto" and src_lang == tgt2_lang)
        )
        needs_third_api_translation = (
            output_format != "original_only"
            and include_third_target
            and tgt3_lang != tgt_lang
            and not (src_lang != "auto" and src_lang == tgt3_lang)
            and not (tgt3_lang == tgt2_lang and include_second_target)
        )
        return MicTranslationPlan(
            original_text=original,
            source_language=src_lang,
            target_language=tgt_lang,
            second_target_language=tgt2_lang,
            third_target_language=tgt3_lang,
            output_format=output_format,
            include_second_target=include_second_target,
            include_third_target=include_third_target,
            needs_primary_translation=needs_primary_translation,
            needs_second_api_translation=needs_second_api_translation,
            needs_third_api_translation=needs_third_api_translation,
            context_source=str(context_source or "mic"),
        )

    def translate_plan(self, plan: MicTranslationPlan, translator: Any = None) -> tuple[RealtimeTranslationResult, Any]:
        active_translator = translator
        if plan.needs_api_translation and active_translator is None:
            active_translator = self._translator_factory(self._current_config())

        translated = plan.original_text
        translated_2 = ""
        translated_3 = ""

        if plan.needs_primary_translation:
            translated = active_translator.translate(
                plan.original_text,
                plan.source_language,
                plan.target_language,
                context_source=plan.context_source,
            )
        if plan.output_format != "original_only" and plan.include_second_target:
            if plan.second_target_language == plan.target_language:
                translated_2 = translated
            elif plan.source_language != "auto" and plan.source_language == plan.second_target_language:
                translated_2 = plan.original_text
            else:
                translated_2 = active_translator.translate(
                    plan.original_text,
                    plan.source_language,
                    plan.second_target_language,
                    context_source=plan.context_source,
                )
        if plan.output_format != "original_only" and plan.include_third_target:
            if plan.third_target_language == plan.target_language:
                translated_3 = translated
            elif plan.third_target_language == plan.second_target_language and plan.include_second_target:
                translated_3 = translated_2
            elif plan.source_language != "auto" and plan.source_language == plan.third_target_language:
                translated_3 = plan.original_text
            else:
                translated_3 = active_translator.translate(
                    plan.original_text,
                    plan.source_language,
                    plan.third_target_language,
                    context_source=plan.context_source,
                )

        display_text = self._output_dispatcher.manual_display_text(translated, translated_2, translated_3)
        chatbox_text = self._output_dispatcher.format_chatbox_output(
            plan.original_text,
            translated,
            translated_2,
            translated_3,
        )
        output_message = self._output_dispatcher.build_message(
            source="mic",
            original_text=plan.original_text,
            translated_text=translated,
            translated_text_2=translated_2,
            translated_text_3=translated_3,
            display_text=display_text,
            chatbox_text=chatbox_text,
        )
        return (
            RealtimeTranslationResult(
                original_text=plan.original_text,
                translated_text=translated,
                translated_text_2=translated_2,
                translated_text_3=translated_3,
                display_text=display_text,
                chatbox_text=chatbox_text,
                output_message=output_message,
                api_translation_used=plan.needs_api_translation,
            ),
            active_translator,
        )


class ListenPipeline:
    """Build reverse/listen final-translation output without owning capture."""

    def __init__(
        self,
        config: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        output_dispatcher: OutputDispatcher,
        *,
        translator_factory: Callable[[dict], Any] = create_translator,
    ) -> None:
        self._config = config
        self._output_dispatcher = output_dispatcher
        self._translator_factory = translator_factory

    def _current_config(self) -> dict:
        config = self._config() if callable(self._config) else self._config
        return config if isinstance(config, dict) else {}

    @staticmethod
    def format_translation(original: str, translated: str) -> str:
        src = str(original or "").strip()
        tgt = str(translated or "").strip()
        if not src:
            return tgt
        if not tgt or src == tgt:
            return src
        return f"{src}（{tgt}）"

    @staticmethod
    def format_chatbox_text(prefix: str, text: str) -> str:
        clean = str(text or "").strip()
        label = str(prefix or "").strip()
        return f"{label} {clean}".strip() if clean else ""

    def create_plan(
        self,
        text: str,
        *,
        source_language: str | None,
        target_language: str,
        listen_prefix: str,
        context_source: str = "listen",
    ) -> ListenTranslationPlan:
        return ListenTranslationPlan(
            original_text=str(text or "").strip(),
            source_language=str(source_language or "auto") or "auto",
            target_language=str(target_language or "zh") or "zh",
            listen_prefix=str(listen_prefix or ""),
            context_source=str(context_source or "listen"),
        )

    def translate_plan(self, plan: ListenTranslationPlan, translator: Any = None) -> tuple[RealtimeTranslationResult, Any]:
        active_translator = translator
        if plan.needs_api_translation:
            if active_translator is None:
                active_translator = self._translator_factory(self._current_config())
            translated = active_translator.translate(
                plan.original_text,
                plan.source_language,
                plan.target_language,
                context_source=plan.context_source,
            )
        else:
            translated = plan.original_text

        display_text = self.format_translation(plan.original_text, translated)
        chatbox_text = self.format_chatbox_text(plan.listen_prefix, display_text)
        output_message = self._output_dispatcher.build_message(
            source="listen",
            original_text=plan.original_text,
            translated_text=translated,
            display_text=display_text,
            chatbox_text=chatbox_text,
        )
        return (
            RealtimeTranslationResult(
                original_text=plan.original_text,
                translated_text=translated,
                display_text=display_text,
                chatbox_text=chatbox_text,
                output_message=output_message,
                api_translation_used=plan.needs_api_translation,
            ),
            active_translator,
        )
