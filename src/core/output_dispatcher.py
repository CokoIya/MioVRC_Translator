from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.utils.ui_config import normalize_output_format

logger = logging.getLogger(__name__)


SECOND_TARGET_OUTPUT_FORMATS = {
    "original_with_translated1_translated2",
    "translated1_with_translated2",
    "translated1_with_translated2_original",
}

_CHATBOX_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


@dataclass(frozen=True)
class OutputMessage:
    """A translated text payload ready for user-visible output sinks."""

    source: str
    original_text: str = ""
    translated_text: str = ""
    translated_text_2: str = ""
    translated_text_3: str = ""
    display_text: str = ""
    chatbox_text: str = ""
    is_error: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


OutputSink = Callable[[OutputMessage], bool | None]


class OutputDispatcher:
    """Formats and sends translated text to user-visible output sinks."""

    def __init__(
        self,
        config: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        sinks: Mapping[str, OutputSink] | None = None,
    ) -> None:
        self._config = config
        self._sinks: dict[str, OutputSink] = {}
        for name, sink in (sinks or {}).items():
            self.register_sink(name, sink)

    def register_sink(self, name: str, sink: OutputSink) -> None:
        key = str(name or "").strip()
        if not key:
            raise ValueError("Output sink name must not be empty")
        if not callable(sink):
            raise TypeError("Output sink must be callable")
        self._sinks[key] = sink

    def unregister_sink(self, name: str) -> None:
        self._sinks.pop(str(name or "").strip(), None)

    def dispatch(
        self,
        message: OutputMessage,
        *,
        sinks: tuple[str, ...] | list[str] | set[str] | None = None,
    ) -> dict[str, bool]:
        selected = list(sinks) if sinks is not None else list(self._sinks.keys())
        results: dict[str, bool] = {}
        for name in selected:
            key = str(name or "").strip()
            sink = self._sinks.get(key)
            if sink is None:
                results[key] = False
                continue
            try:
                results[key] = sink(message) is not False
            except Exception:
                logger.warning("Output sink failed: %s", key, exc_info=True)
                results[key] = False
        return results

    def _current_config(self) -> Mapping[str, Any]:
        config = self._config() if callable(self._config) else self._config
        return config if isinstance(config, Mapping) else {}

    def _translation_config(self) -> Mapping[str, Any]:
        trans_cfg = self._current_config().get("translation", {})
        return trans_cfg if isinstance(trans_cfg, Mapping) else {}

    def output_format(self) -> str:
        return normalize_output_format(self._translation_config().get("output_format"))

    def output_format_uses_second_target(self) -> bool:
        return self.output_format() in SECOND_TARGET_OUTPUT_FORMATS

    def chatbox_template(self) -> str:
        return str(self._translation_config().get("chatbox_template", "") or "").strip()

    def chatbox_template_uses_second_target(self) -> bool:
        template = self.chatbox_template()
        if not template:
            return False
        placeholders = set(_CHATBOX_TEMPLATE_PLACEHOLDER_RE.findall(template))
        return bool(placeholders & {"translatedText2", "translation2"})

    def chatbox_template_uses_third_target(self) -> bool:
        template = self.chatbox_template()
        if not template:
            return False
        placeholders = set(_CHATBOX_TEMPLATE_PLACEHOLDER_RE.findall(template))
        return bool(placeholders & {"translatedText3", "translation3"})

    def format_chatbox_template(
        self,
        src_text: str,
        tgt_text: str,
        tgt2_text: str = "",
        tgt3_text: str = "",
    ) -> str:
        template = self.chatbox_template()
        if not template:
            return ""
        values = {
            "text": src_text,
            "originalText": src_text,
            "translatedText": tgt_text,
            "translatedText1": tgt_text,
            "translatedText2": tgt2_text,
            "translatedText3": tgt3_text,
            "translation": tgt_text,
            "translation1": tgt_text,
            "translation2": tgt2_text,
            "translation3": tgt3_text,
        }
        normalized_template = template.replace("\\r\\n", "\n").replace("\\n", "\n")
        rendered_lines: list[str] = []
        for raw_line in normalized_template.splitlines() or [normalized_template]:
            placeholders = _CHATBOX_TEMPLATE_PLACEHOLDER_RE.findall(raw_line)
            if placeholders and any(not values.get(name, "") for name in placeholders):
                continue
            rendered = raw_line
            for name, value in values.items():
                rendered = rendered.replace("{" + name + "}", value)
            rendered = rendered.strip()
            if rendered:
                rendered_lines.append(rendered)
        return "\n".join(rendered_lines).strip()

    def format_chatbox_output(
        self,
        src_text: str,
        tgt_text: str,
        tgt2_text: str = "",
        tgt3_text: str = "",
    ) -> str:
        src_text = str(src_text or "")
        tgt_text = str(tgt_text or "")
        tgt2_text = str(tgt2_text or "")
        tgt3_text = str(tgt3_text or "")
        fmt = self.output_format()
        if fmt == "original_only":
            return src_text or tgt_text

        templated = self.format_chatbox_template(src_text, tgt_text, tgt2_text, tgt3_text)
        if templated:
            return templated

        if fmt == "original_with_translated1_translated2":
            if tgt2_text:
                return (
                    f"{src_text}({tgt_text})({tgt2_text})"
                    if src_text and tgt_text
                    else src_text or tgt_text or tgt2_text
                )
            return f"{src_text}({tgt_text})" if src_text and tgt_text else src_text or tgt_text
        if fmt == "translated1_with_translated2":
            return f"{tgt_text}({tgt2_text})" if tgt_text and tgt2_text else tgt_text or tgt2_text or src_text
        if fmt == "translated1_with_translated2_original":
            if tgt2_text:
                return (
                    f"{tgt_text}({tgt2_text})({src_text})"
                    if src_text and tgt_text
                    else tgt_text or tgt2_text or src_text
                )
            return f"{tgt_text}({src_text})" if src_text and tgt_text else tgt_text or src_text
        if fmt == "translated_only":
            return tgt_text or src_text
        if fmt == "original_with_translated":
            return f"{src_text}({tgt_text})" if src_text and tgt_text else src_text or tgt_text
        return f"{tgt_text}({src_text})" if src_text and tgt_text else tgt_text or src_text

    def manual_display_text(
        self,
        translated_text: str,
        translated_text_2: str = "",
        translated_text_3: str = "",
    ) -> str:
        return "\n".join(
            part for part in (translated_text, translated_text_2, translated_text_3) if part
        )

    def build_message(
        self,
        *,
        source: str,
        original_text: str = "",
        translated_text: str = "",
        translated_text_2: str = "",
        translated_text_3: str = "",
        display_text: str | None = None,
        chatbox_text: str | None = None,
        is_error: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> OutputMessage:
        original = str(original_text or "")
        translated = str(translated_text or "")
        translated_2 = str(translated_text_2 or "")
        translated_3 = str(translated_text_3 or "")
        resolved_display = (
            str(display_text or "")
            if display_text is not None
            else self.manual_display_text(translated, translated_2, translated_3)
        )
        resolved_chatbox = (
            str(chatbox_text or "")
            if chatbox_text is not None
            else self.format_chatbox_output(original, translated, translated_2, translated_3)
        )
        return OutputMessage(
            source=str(source or ""),
            original_text=original,
            translated_text=translated,
            translated_text_2=translated_2,
            translated_text_3=translated_3,
            display_text=resolved_display,
            chatbox_text=resolved_chatbox,
            is_error=bool(is_error),
            metadata=dict(metadata or {}),
        )

    def send_chatbox_text(self, sender: Any, text: str) -> bool:
        clean = str(text or "").strip()
        if not clean:
            return False
        return bool(sender.send_chatbox(clean))

    def send_chatbox(
        self,
        sender: Any,
        *,
        original_text: str,
        translated_text: str,
        translated_text_2: str = "",
        translated_text_3: str = "",
    ) -> bool:
        if not translated_text and not original_text:
            return False
        chatbox_text = self.format_chatbox_output(
            original_text,
            translated_text,
            translated_text_2,
            translated_text_3,
        )
        return self.send_chatbox_text(sender, chatbox_text)
