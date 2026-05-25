from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ui import settings_window


class _FakeFrame:
    def __init__(self, *_, mapped: bool = False, **__) -> None:
        self._mapped = mapped
        self.pack_calls: list[dict[str, object]] = []
        self.pack_forget_calls = 0
        self.bind_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.pack_propagate_calls: list[tuple[object, ...]] = []

    def winfo_ismapped(self) -> bool:
        return self._mapped

    def pack(self, **kwargs) -> None:
        self._mapped = True
        self.pack_calls.append(kwargs)

    def pack_forget(self) -> None:
        self._mapped = False
        self.pack_forget_calls += 1

    def bind(self, *args, **kwargs) -> None:
        self.bind_calls.append((args, kwargs))

    def pack_propagate(self, *args) -> None:
        self.pack_propagate_calls.append(args)


class _FakeLabel:
    def __init__(self) -> None:
        self.configured: dict[str, object] = {}

    def configure(self, **kwargs) -> None:
        self.configured.update(kwargs)


class _FakeButton(_FakeLabel):
    pass


def _make_window(language_label: str, engine_label: str = "Style-Bert-VITS2"):
    window = settings_window.SettingsWindow.__new__(settings_window.SettingsWindow)
    window._ui_copy = lambda key: (
        settings_window.TTS_COPY_OVERRIDES.get(key)
        or settings_window.WINDOW_COPY.get(key, {})
    ).get("zh-CN", "")
    window._tts_engine_codes = {engine_label: "style_bert_vits2"}
    window._tts_engine_var = SimpleNamespace(get=lambda: engine_label)
    window._tts_bert_language_codes = {
        "日文": "jp",
        "中文": "zh",
        "英文": "en",
    }
    window._tts_bert_language_reverse = {
        "jp": "日文",
        "zh": "中文",
        "en": "英文",
    }
    window._tts_bert_language_var = SimpleNamespace(get=lambda: language_label)
    window._bert_info_slot = _FakeFrame(mapped=True)
    window._bert_info_frame = _FakeFrame(mapped=False)
    window._bert_info_label = _FakeLabel()
    window._bert_download_btn = _FakeButton()
    window._bert_info_pack_options = {"fill": "x"}
    window._schedule_settings_layout_refresh = lambda: None
    return window


@pytest.mark.parametrize(
    ("language_label", "expected_model_id"),
    [("中文", "hfl/chinese-roberta-wwm-ext-large"), ("英文", "microsoft/deberta-v3-large")],
)
def test_bert_prompt_stays_under_language_row_when_model_missing(
    monkeypatch,
    language_label: str,
    expected_model_id: str,
):
    window = _make_window(language_label)
    monkeypatch.setattr(settings_window, "model_is_complete", lambda _model_id: False)

    settings_window.SettingsWindow._refresh_bert_model_prompt(window)

    assert window._bert_info_label.configured["text"].endswith(f"模型：{expected_model_id}")
    assert window._bert_download_btn.configured["text"] == settings_window.TTS_COPY_OVERRIDES["tts_bert_download_btn"]["zh-CN"]
    assert window._bert_info_frame.pack_calls == [{"fill": "x"}]
    assert window._bert_info_frame.pack_forget_calls == 0


def test_bert_prompt_hides_when_model_already_present(monkeypatch):
    window = _make_window("日文")
    monkeypatch.setattr(settings_window, "model_is_complete", lambda _model_id: True)

    settings_window.SettingsWindow._refresh_bert_model_prompt(window)

    assert window._bert_info_frame.pack_calls == []
    assert window._bert_info_frame.pack_forget_calls == 1


def test_section_cards_start_collapsed_by_default(monkeypatch):
    monkeypatch.setattr(settings_window.ctk, "CTkFrame", _FakeFrame)
    monkeypatch.setattr(settings_window.ctk, "CTkLabel", _FakeFrame)
    monkeypatch.setattr(settings_window.ctk, "CTkFont", lambda *args, **kwargs: None)

    window = settings_window.SettingsWindow.__new__(settings_window.SettingsWindow)
    window._section_cards = []
    window._bind_section_toggle = lambda *args, **kwargs: None

    content = settings_window.SettingsWindow._build_section_card(
        window,
        object(),
        "Title",
        "Subtitle",
    )

    state = window._section_cards[0]
    assert state["collapsed"] is True
    assert state["content_packed"] is False
    assert content.pack_calls == []


def test_asr_and_bert_prompts_say_model_is_required():
    asr_notice = settings_window.tr("zh-CN", "asr_engine_notice")
    sensevoice_hint = settings_window.tr("zh-CN", "asr_hint_sensevoice")
    bert_hint = settings_window.TTS_COPY_OVERRIDES["tts_bert_language_hint"]["zh-CN"]
    bert_info = settings_window.TTS_COPY_OVERRIDES["tts_bert_model_info"]["zh-CN"]

    assert "必选" in asr_notice
    assert "必须先下载" in sensevoice_hint
    assert "同声传译" in bert_hint
    assert "必须先下载" in bert_info
