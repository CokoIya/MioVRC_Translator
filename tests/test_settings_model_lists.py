"""Settings: provider model lists, relay protocol and cloud voice cloning.

Covers the settings-window side of the 2026-10 model update: model lists read
from the provider (and when a request may be sent at all), the relay
placeholder, refresh failures, overlapping refreshes, and the voice-cloning
review/region/error handling. ``fetch_backend_models`` is always replaced, so
no test touches the network, and QMessageBox is patched so none blocks.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox

from src.core import voice_clone_service
from src.translators.model_listing import ModelListResult
from src.tts.api_tts_config import QWEN_TTS_BASE_URL_MAINLAND, QWEN_VC_DEFAULT_MODEL
from src.tts.base import TTSVoice
from src.tts.qwen_voice_enrollment import ClonedVoice, QwenVoiceEnrollmentError
from src.ui_qt import settings_window as settings_module
from src.ui_qt.settings_window import SettingsWindow
from src.ui_qt.widgets import COMBO_MAX_VISIBLE_ITEMS, NoWheelComboBox
from src.utils.i18n import tr
from src.utils.model_family import model_family_id, same_model_family
from src.utils.ui_config import (
    RELAY_BACKEND,
    get_backend_label,
    get_backend_region_base_url,
    get_backend_spec,
    model_list_source_key,
    relay_protocol_default,
)

RELAY_A = "https://relay-a.example.com/v1"
RELAY_B = "https://relay-b.example.net/v1"
SECRET_A = "relay-A-secret-key"


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


class _DummyTTS:
    def get_available_voices(self):
        return []


class _CloneTTS:
    """Lists the stored cloned voices the way QwenVoiceCloneTTS does."""

    def __init__(self, config):
        voices = config.get("custom_voices", []) if isinstance(config, dict) else []
        self._voices = [
            TTSVoice(
                id=voice["voice_id"],
                name=voice.get("display_name") or voice["voice_id"],
                language="multi",
                gender="",
                locale="multi",
            )
            for voice in voices
        ]

    def get_available_voices(self):
        return list(self._voices)


class _HttpError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeFetch:
    """Stand-in for model_list_service.fetch_backend_models.

    Records where every request went and with which credentials. Results are
    scripted per Base URL; a gate makes the answer for one URL arrive late.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.completed: list[str] = []
        self.results: dict[str, object] = {}
        self.gates: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def __call__(self, config, **_kwargs):
        trans = config["translation"]
        backend = str(trans["backend"])
        section = trans.get(backend, {})
        call = {
            "backend": backend,
            "base_url": section.get("base_url"),
            "api_key": section.get("api_key"),
            "protocol": section.get("protocol"),
            "custom_headers": section.get("custom_headers"),
        }
        with self._lock:
            self.calls.append(call)
        gate = self.gates.get(str(call["base_url"]))
        if gate is not None:
            gate.wait(5)
        result = self.results.get(str(call["base_url"]))
        if result is None:
            result = _remote(backend, ("model-a", "model-b"))
        with self._lock:
            self.completed.append(str(call["base_url"]))
        return result


def _remote(backend: str, models: tuple[str, ...]) -> ModelListResult:
    return ModelListResult(backend=backend, models=tuple(models), source="remote")


def _failed(backend: str, error: BaseException) -> ModelListResult:
    return ModelListResult(backend=backend, models=(), source="builtin", error=error)


@pytest.fixture(autouse=True)
def fetch(monkeypatch):
    fake = _FakeFetch()
    monkeypatch.setattr("src.translators.model_list_service.fetch_backend_models", fake)
    return fake


@pytest.fixture(autouse=True)
def boxes(monkeypatch):
    """Patch every QMessageBox entry point and record what would be shown."""

    shown: list[tuple[str, str, str]] = []

    def recorder(kind):
        def record(_parent, title, message, *_args, **_kwargs):
            shown.append((kind, str(title), str(message)))
            return settings_module.QMessageBox.StandardButton.Yes

        return record

    for kind in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(settings_module.QMessageBox, kind, recorder(kind))
    return shown


@pytest.fixture(autouse=True)
def _dialog_deps(monkeypatch):
    monkeypatch.setattr("src.utils.config_manager.save_config", lambda _config: None)
    monkeypatch.setattr(settings_module.AudioRecorder, "list_devices", lambda: [])
    monkeypatch.setattr(settings_module, "_list_desktop_output_devices", lambda: [])
    monkeypatch.setattr(settings_module, "find_best_virtual_output_device", lambda: None)
    monkeypatch.setattr(
        settings_module,
        "create_tts_engine",
        lambda engine, config=None: _CloneTTS(config) if engine == "qwen_vc" else _DummyTTS(),
    )
    monkeypatch.setattr(
        settings_module,
        "first_missing_required_credential",
        lambda _cfg, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(settings_module, "show_missing_credential_prompt", lambda *_a, **_k: False)
    monkeypatch.setattr("src.asr.model_manager.model_exists", lambda _spec: True)
    monkeypatch.setattr(
        settings_module,
        "dictionary_status",
        lambda: {"layers": [], "user_path": ""},
    )


def _config(backend: str = RELAY_BACKEND, *, language: str = "en", **section) -> dict:
    config = {
        "ui": {"language": language, "main_window_theme": "system"},
        "translation": {
            "backend": backend,
            "target_language": "ja",
            "source_language": "auto",
            "output_format": "translated_with_original",
        },
        "asr": {"engine": "sensevoice-small"},
        "tts": {"engine": "edge", "rate": 1.0, "volume": 0.8},
        "vrc_listen": {"enabled": False, "source_language": "auto", "target_language": "zh"},
        "hotkeys": {"mic_mute": "Ctrl+Alt+F2"},
        "text_input_window": {"hotkey": "Alt+X"},
        "osc": {"avatar_sync": {"enabled": False, "params": {}}},
    }
    if section:
        config["translation"][backend] = dict(section)
    return config


def _relay_config(**overrides) -> dict:
    section = {
        "api_key": SECRET_A,
        "base_url": RELAY_A,
        "model": "gpt-6-luna",
        "protocol": "openai",
    }
    section.update(overrides)
    return _config(RELAY_BACKEND, **section)


def _flush_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _open(qtbot, config: dict, *, show: bool = True, page: str = "api_config") -> SettingsWindow:
    dialog = SettingsWindow(None, config)
    qtbot.addWidget(dialog)
    if show:
        dialog.show()
    dialog.select_page(page)
    qtbot.wait(20)
    return dialog


def _wait_idle(qtbot, dialog: SettingsWindow, fetch: _FakeFetch, calls: int) -> None:
    """Wait until ``calls`` requests were answered and the form shows it."""

    qtbot.waitUntil(lambda: len(fetch.completed) >= calls, timeout=3000)
    qtbot.waitUntil(
        lambda: dialog._model_list_refresh_btn.isEnabled()
        and _status(dialog) != dialog._copy("model_list_refreshing"),
        timeout=3000,
    )
    qtbot.wait(10)


def _combo_items(combo: QComboBox) -> list[str]:
    return [combo.itemText(index) for index in range(combo.count())]


def _combo_with_item(dialog: SettingsWindow, text: str) -> QComboBox:
    _flush_deletes()
    for combo in dialog._backend_fields_frame.findChildren(QComboBox):
        if combo.findText(text) >= 0:
            return combo
    raise AssertionError(f"no combo offers {text!r}")


def _switch_backend(dialog: SettingsWindow, backend: str) -> None:
    dialog._backend_var.set(get_backend_label(backend, dialog._ui_lang))
    dialog._render_backend_fields()


def _status(dialog: SettingsWindow) -> str:
    return dialog._model_list_status_label.text()


# --------------------------------------------------------------------------
# S1: requests go only to the saved endpoint, edits wait for Refresh
# --------------------------------------------------------------------------


def test_opening_the_api_page_reads_the_saved_relay_list_once(qtbot, fetch):
    config = _relay_config(custom_headers={"X-Team": "mio"})
    dialog = _open(qtbot, config)
    _wait_idle(qtbot, dialog, fetch, 1)

    assert fetch.calls == [
        {
            "backend": RELAY_BACKEND,
            "base_url": RELAY_A,
            "api_key": SECRET_A,
            "protocol": "openai",
            "custom_headers": {"X-Team": "mio"},
        }
    ]
    # Re-rendering the same saved endpoint does not read it again.
    dialog._render_backend_fields()
    dialog.select_page("api_config")
    qtbot.wait(50)
    assert len(fetch.calls) == 1


def test_editing_the_base_url_never_sends_the_saved_key_to_the_new_host(qtbot, fetch):
    dialog = _open(qtbot, _relay_config())
    _wait_idle(qtbot, dialog, fetch, 1)

    entry = dialog._backend_base_url_entry
    entry.setText(RELAY_B)
    entry.editingFinished.emit()
    qtbot.wait(50)

    assert len(fetch.calls) == 1
    assert all(call["base_url"] != RELAY_B for call in fetch.calls)
    # The status asks for Refresh and the relay-A list is not offered for B.
    assert _status(dialog) == dialog._copy("model_list_relay_empty")
    assert "model-a" not in _combo_items(dialog._backend_model_combo)

    # A re-render with the edited (unsaved) URL stays quiet too.
    dialog._render_backend_fields()
    dialog.select_page("api_config")
    qtbot.wait(50)
    assert len(fetch.calls) == 1

    # Refresh is explicit consent: now the request goes to relay B.
    fetch.results[RELAY_B] = _remote(RELAY_BACKEND, ("relay-b-model",))
    dialog._model_list_refresh_btn.click()
    _wait_idle(qtbot, dialog, fetch, 2)
    assert fetch.calls[1]["base_url"] == RELAY_B
    assert "relay-b-model" in _combo_items(dialog._backend_model_combo)


def test_protocol_switch_swaps_untouched_defaults_and_sends_nothing(qtbot, fetch):
    config = _config(RELAY_BACKEND, api_key=SECRET_A)
    dialog = _open(qtbot, config)
    _wait_idle(qtbot, dialog, fetch, 1)
    calls_before = len(fetch.calls)
    assert dialog._backend_base_url_var.value() == relay_protocol_default("openai", "base_url")
    assert dialog._backend_model_var.value() == relay_protocol_default("openai", "model")

    anthropic_label = dialog._copy("relay_protocol_anthropic")
    protocol_combo = _combo_with_item(dialog, anthropic_label)
    protocol_combo.setCurrentText(anthropic_label)
    qtbot.wait(50)

    assert dialog._selected_relay_protocol() == "anthropic"
    assert dialog._backend_base_url_var.value() == relay_protocol_default("anthropic", "base_url")
    assert dialog._backend_base_url_entry.text() == relay_protocol_default("anthropic", "base_url")
    assert dialog._backend_model_var.value() == relay_protocol_default("anthropic", "model")
    assert len(fetch.calls) == calls_before

    # Values the player typed are kept when the protocol changes back.
    dialog._backend_base_url_var.set("https://claude-relay.example/v1")
    dialog._backend_model_var.set("claude-custom-relay-id")
    protocol_combo.setCurrentText(dialog._copy("relay_protocol_openai"))
    qtbot.wait(50)
    assert dialog._selected_relay_protocol() == "openai"
    assert dialog._backend_base_url_var.value() == "https://claude-relay.example/v1"
    assert dialog._backend_model_var.value() == "claude-custom-relay-id"
    assert len(fetch.calls) == calls_before


def test_empty_relay_fields_fall_back_to_the_protocol_defaults(qtbot, fetch):
    """Refresh, Test connection and Save ask the endpoint the list is filed under."""

    config = _config(RELAY_BACKEND, api_key=SECRET_A, protocol="anthropic")
    dialog = _open(qtbot, config, show=False)
    dialog._backend_base_url_var.set("")
    dialog._backend_model_var.set("")

    snapshot = dialog._translation_connection_config()["translation"][RELAY_BACKEND]
    assert snapshot["base_url"] == relay_protocol_default("anthropic", "base_url")
    assert snapshot["model"] == relay_protocol_default("anthropic", "model")
    assert snapshot["protocol"] == "anthropic"
    assert dialog._current_model_list_source_key(RELAY_BACKEND) == model_list_source_key(
        RELAY_BACKEND, relay_protocol_default("anthropic", "base_url"), "anthropic"
    )

    dialog._save()
    saved = config["translation"][RELAY_BACKEND]
    assert saved["base_url"] == relay_protocol_default("anthropic", "base_url")
    assert saved["model"] == relay_protocol_default("anthropic", "model")
    assert saved["protocol"] == "anthropic"
    assert fetch.calls == []


def test_changed_custom_headers_or_key_block_the_automatic_request(qtbot, fetch):
    config = _relay_config(custom_headers={"X-Team": "mio"})
    dialog = _open(qtbot, config, show=False)
    dialog._backend_custom_headers_var.set('{"X-Team":"someone-else"}')
    dialog.show()
    dialog.select_page("api_config")
    qtbot.wait(80)
    assert fetch.calls == []

    dialog._backend_custom_headers_var.set('{"X-Team":"mio"}')
    dialog._backend_api_key_var.set("a-new-unsaved-key")
    dialog._render_backend_fields()
    qtbot.wait(80)
    assert fetch.calls == []


def test_a_hidden_preloaded_window_waits_until_the_api_page_is_shown(qtbot, fetch):
    dialog = _open(qtbot, _relay_config(), show=False)
    qtbot.wait(80)
    assert fetch.calls == []

    dialog.show()
    _wait_idle(qtbot, dialog, fetch, 1)
    assert fetch.calls[0]["base_url"] == RELAY_A


def test_region_change_follows_the_new_endpoint_without_sending(qtbot, fetch):
    singapore = get_backend_region_base_url("qianwen", "singapore")
    fetch.results[singapore] = _remote("qianwen", ("qwen-mt-flash", "qwen-remote-only"))
    config = _config(
        "qianwen",
        api_key="qwen-key",
        region="singapore",
        base_url=singapore,
        model="qwen-mt-flash",
    )
    dialog = _open(qtbot, config)
    _wait_idle(qtbot, dialog, fetch, 1)
    assert fetch.calls[0]["base_url"] == singapore
    assert "qwen-remote-only" in _combo_items(dialog._backend_model_combo)

    mainland_label = dialog._copy("qwen_region_china_mainland")
    region_combo = _combo_with_item(dialog, mainland_label)
    region_combo.setCurrentText(mainland_label)
    qtbot.wait(50)

    assert len(fetch.calls) == 1
    assert dialog._backend_base_url_var.value() == get_backend_region_base_url(
        "qianwen", "china_mainland"
    )
    # The Singapore list no longer shows; the built-in list asks for Refresh.
    assert "qwen-remote-only" not in _combo_items(dialog._backend_model_combo)
    assert _status(dialog) == dialog._copy("model_list_builtin")


def test_editing_the_api_key_clears_a_failed_refresh_and_sends_nothing(qtbot, fetch):
    fetch.results[RELAY_A] = _failed(
        RELAY_BACKEND,
        _HttpError("Error code: 401 - incorrect API key provided", 401),
    )
    dialog = _open(qtbot, _relay_config())
    _wait_idle(qtbot, dialog, fetch, 1)
    failed = dialog._copy(
        "model_list_failed_manual",
        reason=dialog._copy("model_list_reason_auth"),
    )
    assert _status(dialog) == failed

    entry = dialog._backend_api_key_entry
    entry.editingFinished.emit()  # focus left without an edit
    assert _status(dialog) == failed

    entry.setText("relay-A-new-key")
    entry.setModified(True)
    entry.editingFinished.emit()
    qtbot.wait(50)
    assert _status(dialog) == dialog._copy("model_list_relay_empty")
    assert len(fetch.calls) == 1


# --------------------------------------------------------------------------
# S3: the relay placeholder is only replaced by a sensible model
# --------------------------------------------------------------------------


def test_relay_placeholder_takes_the_same_model_under_the_relay_spelling(qtbot, fetch):
    fetch.results[RELAY_A] = _remote(
        RELAY_BACKEND,
        ("text-embedding-3-large", "openai/gpt-6-luna", "anthropic/claude-haiku-5.5"),
    )
    dialog = _open(qtbot, _relay_config(model=""))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == "openai/gpt-6-luna"
    assert dialog._backend_model_combo.currentText() == "openai/gpt-6-luna"
    assert dialog._copy("model_list_pick_model", model="gpt-6-luna") not in _status(dialog)


def test_relay_placeholder_falls_back_to_the_priority_list_not_the_first_entry(qtbot, fetch):
    placeholder = relay_protocol_default("openai", "model")
    priority = [
        str(model)
        for model in get_backend_spec(RELAY_BACKEND).get("model_list_priority") or ()
        if not same_model_family(model, placeholder)
    ]
    assert len(priority) >= 2
    preferred, later = f"vendor/{priority[0]}", f"other/{priority[1]}"
    fetch.results[RELAY_A] = _remote(
        RELAY_BACKEND,
        ("text-embedding-3-large", later, "random/aggregator-model", preferred),
    )
    dialog = _open(qtbot, _relay_config(model=placeholder))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == preferred


def test_relay_placeholder_is_kept_and_the_player_asked_to_pick(qtbot, fetch):
    fetch.results[RELAY_A] = _remote(
        RELAY_BACKEND,
        ("text-embedding-3-large", "random/aggregator-model"),
    )
    dialog = _open(qtbot, _relay_config(model=""))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == "gpt-6-luna"
    assert dialog._backend_model_combo.currentText() == "gpt-6-luna"
    assert dialog._copy("model_list_pick_model", model="gpt-6-luna") in _status(dialog)


def test_a_typed_relay_model_is_never_replaced(qtbot, fetch):
    fetch.results[RELAY_A] = _remote(RELAY_BACKEND, ("openai/gpt-6-luna", "model-x"))
    dialog = _open(qtbot, _relay_config(model="my-own-model"))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == "my-own-model"
    assert _combo_items(dialog._backend_model_combo)[0] == "my-own-model"


def _relay_priority() -> list[str]:
    return [str(model) for model in get_backend_spec(RELAY_BACKEND).get("model_list_priority") or ()]


def _is_claude(model: str) -> bool:
    return model_family_id(model).startswith("claude-")


def test_anthropic_relay_placeholder_is_replaced_only_by_a_claude_model(qtbot, fetch):
    """Over Anthropic, claude-opus-5 never becomes the GPT head of the priority list."""

    placeholder = relay_protocol_default("anthropic", "model")
    priority = _relay_priority()
    claude_priority = [model for model in priority if _is_claude(model)]
    assert claude_priority, "the relay spec recommends no Claude model"
    assert not _is_claude(priority[0])
    preferred = f"anthropic/{claude_priority[0]}"
    other_models = [model for model in priority[1:] if not _is_claude(model)]
    fetch.results[RELAY_A] = _remote(
        RELAY_BACKEND,
        (priority[0], "claude-sonnet-unlisted-9", preferred, *other_models[:2]),
    )
    dialog = _open(qtbot, _relay_config(model=placeholder, protocol="anthropic"))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == preferred
    assert dialog._backend_model_combo.currentText() == preferred
    assert dialog._copy("model_list_pick_model", model=placeholder) not in _status(dialog)


def test_anthropic_relay_placeholder_is_kept_without_a_recommended_claude_model(qtbot, fetch):
    placeholder = relay_protocol_default("anthropic", "model")
    other_models = [model for model in _relay_priority() if not _is_claude(model)]
    fetch.results[RELAY_A] = _remote(
        RELAY_BACKEND,
        (*other_models, "claude-sonnet-unlisted-9"),
    )
    dialog = _open(qtbot, _relay_config(model=placeholder, protocol="anthropic"))
    _wait_idle(qtbot, dialog, fetch, 1)

    assert dialog._backend_model_var.value() == placeholder
    assert dialog._backend_model_combo.currentText() == placeholder
    assert dialog._copy("model_list_pick_model", model=placeholder) in _status(dialog)
    # The OpenAI protocol keeps using the whole priority list.
    assert SettingsWindow._relay_placeholder_replacement(
        relay_protocol_default("openai", "model"),
        tuple(other_models[1:]),
        "openai",
    ) == other_models[1]


@pytest.mark.parametrize("protocol", ("anthropic", "openai"))
def test_an_emptied_relay_model_box_offers_the_protocol_default(qtbot, protocol):
    config = _config(RELAY_BACKEND, api_key=SECRET_A, base_url=RELAY_A, protocol=protocol)
    dialog = _open(qtbot, config, show=False)
    default_model = relay_protocol_default(protocol, "model")
    other_default = relay_protocol_default(
        "openai" if protocol == "anthropic" else "anthropic", "model"
    )

    dialog._backend_model_combo.setEditText("")
    assert dialog._backend_model_var.value() == ""
    dialog._repopulate_backend_model_combo(RELAY_BACKEND)

    assert _combo_items(dialog._backend_model_combo) == [default_model]
    title = dialog._backend_model_info_title_label.text()
    assert f" · {default_model} · " in title
    assert other_default not in title


# --------------------------------------------------------------------------
# S4/S5: failures stay visible and are explained correctly
# --------------------------------------------------------------------------


def test_a_failed_refresh_stays_visible_while_the_older_list_is_shown(qtbot, fetch):
    fetch.results[RELAY_A] = _remote(RELAY_BACKEND, ("model-a", "model-b"))
    dialog = _open(qtbot, _relay_config(model="model-a"))
    _wait_idle(qtbot, dialog, fetch, 1)

    fetch.results[RELAY_A] = _failed(
        RELAY_BACKEND,
        _HttpError("Error code: 503 - service unavailable", 503),
    )
    dialog._model_list_refresh_btn.click()
    _wait_idle(qtbot, dialog, fetch, 2)

    assert _status(dialog) == dialog._copy(
        "model_list_refresh_failed_kept",
        reason=dialog._copy("model_list_reason_provider"),
    )
    assert "model-b" in _combo_items(dialog._backend_model_combo)

    fetch.results[RELAY_A] = _remote(RELAY_BACKEND, ("model-a", "model-b", "model-c"))
    dialog._model_list_refresh_btn.click()
    _wait_idle(qtbot, dialog, fetch, 3)
    assert _status(dialog) == dialog._copy("model_list_loaded", count="3")


def test_failure_reasons_cover_provider_errors_and_non_json_answers(qtbot):
    dialog = _open(qtbot, _relay_config(), show=False)
    reason = dialog._model_list_failure_reason_key

    assert reason(RELAY_BACKEND, _HttpError("Error code: 503 - service unavailable", 503)) == (
        "model_list_reason_provider"
    )
    assert reason(
        RELAY_BACKEND,
        RuntimeError("当前分组 default 下对于模型 x 无可用渠道 (no available channel)"),
    ) == "model_list_reason_provider"
    # The relay's web console answered with HTML: the Base URL lacks /v1.
    assert reason(
        RELAY_BACKEND,
        json.JSONDecodeError("Expecting value", "<!doctype html>", 0),
    ) == "model_list_reason_endpoint"
    assert reason(RELAY_BACKEND, _HttpError("Error code: 401 - unauthorized", 401)) == (
        "model_list_reason_auth"
    )
    assert dialog._model_list_failure_reason(RELAY_BACKEND, ValueError("x")) == dialog._copy(
        "model_list_reason_other"
    )


@pytest.mark.parametrize(
    ("backend", "section", "failed_key", "idle_key"),
    [
        (RELAY_BACKEND, {"api_key": "k", "base_url": RELAY_A}, "model_list_failed_manual", "model_list_relay_empty"),
        ("local_ai", {"base_url": "http://127.0.0.1:11434/v1"}, "model_list_failed_manual", "model_list_manual"),
        ("openai", {"api_key": "k"}, "model_list_failed", "model_list_builtin"),
    ],
)
def test_only_backends_with_a_built_in_list_claim_to_show_it(
    qtbot, fetch, backend, section, failed_key, idle_key
):
    dialog = _open(qtbot, _config(backend, **section), show=False)
    assert _status(dialog) == dialog._copy(idle_key)

    key = dialog._current_model_list_source_key(backend)
    dialog._finish_model_list_fetch(
        1,
        (backend, key, _failed(backend, _HttpError("Error code: 404 - not found", 404))),
    )
    assert _status(dialog) == dialog._copy(
        failed_key,
        reason=dialog._copy("model_list_reason_endpoint"),
    )
    if failed_key == "model_list_failed_manual":
        assert "built-in" not in _status(dialog)


# --------------------------------------------------------------------------
# S6: overlapping refreshes keep each endpoint's own list
# --------------------------------------------------------------------------


def test_a_late_answer_for_an_old_url_never_replaces_the_current_list(qtbot, fetch):
    gate = threading.Event()
    fetch.gates[RELAY_A] = gate
    fetch.results[RELAY_A] = _remote(RELAY_BACKEND, ("a-1", "a-2"))
    fetch.results[RELAY_B] = _remote(RELAY_BACKEND, ("b-1", "b-2", "b-3"))
    try:
        dialog = _open(qtbot, _relay_config(model="a-1"))
        qtbot.waitUntil(lambda: len(fetch.calls) == 1, timeout=3000)

        dialog._backend_base_url_entry.setText(RELAY_B)
        dialog._backend_base_url_entry.editingFinished.emit()
        assert dialog._model_list_refresh_btn.isEnabled()
        dialog._model_list_refresh_btn.click()
        qtbot.waitUntil(lambda: len(fetch.calls) == 2, timeout=3000)
        qtbot.waitUntil(lambda: "b-3" in _combo_items(dialog._backend_model_combo), timeout=3000)
    finally:
        gate.set()
    qtbot.waitUntil(lambda: len(fetch.completed) == 2, timeout=3000)
    qtbot.wait(50)

    # Relay A's late answer did not touch the relay-B form ...
    assert "a-2" not in _combo_items(dialog._backend_model_combo)
    assert _status(dialog) == dialog._copy("model_list_loaded", count="3")

    # ... and was kept for relay A: going back shows it without a new request.
    dialog._backend_base_url_entry.setText(RELAY_A)
    dialog._backend_base_url_entry.editingFinished.emit()
    assert "a-2" in _combo_items(dialog._backend_model_combo)
    assert _status(dialog) == dialog._copy("model_list_loaded", count="2")
    assert len(fetch.calls) == 2

    key_a = model_list_source_key(RELAY_BACKEND, RELAY_A, "openai")
    key_b = model_list_source_key(RELAY_BACKEND, RELAY_B, "openai")
    assert dialog._remote_model_lists[(RELAY_BACKEND, key_a)] == (("a-1", "a-2"), "remote")
    assert dialog._remote_model_lists[(RELAY_BACKEND, key_b)][0] == ("b-1", "b-2", "b-3")
    assert not dialog._model_list_inflight


def test_an_answer_for_another_backend_is_stored_for_that_backend(qtbot, fetch):
    config = _relay_config()
    config["translation"]["openai"] = {"api_key": "sk-openai", "model": "gpt-6-luna"}
    dialog = _open(qtbot, config, show=False)
    _switch_backend(dialog, "openai")
    openai_key = dialog._current_model_list_source_key("openai")
    _switch_backend(dialog, RELAY_BACKEND)
    dialog._model_list_fetch_generation = 7  # newer requests ran meanwhile

    dialog._finish_model_list_fetch(
        2,
        ("openai", openai_key, _remote("openai", ("gpt-6-luna", "gpt-remote-only"))),
    )
    _switch_backend(dialog, "openai")

    assert "gpt-remote-only" in _combo_items(dialog._backend_model_combo)
    assert _status(dialog) == dialog._copy("model_list_loaded", count="2")


def test_a_stale_answer_is_dropped_and_the_endpoint_may_be_read_again(qtbot, fetch):
    dialog = _open(qtbot, _relay_config(), show=False)
    key = dialog._current_model_list_source_key(RELAY_BACKEND)
    dialog._model_list_auto_attempted.add(key)

    dialog._finish_model_list_fetch(5, (RELAY_BACKEND, key, _remote(RELAY_BACKEND, ("new",))))
    dialog._finish_model_list_fetch(3, (RELAY_BACKEND, key, _remote(RELAY_BACKEND, ("old",))))

    assert dialog._remote_model_lists[(RELAY_BACKEND, key)] == (("new",), "remote")
    assert key not in dialog._model_list_auto_attempted


# --------------------------------------------------------------------------
# S7: a keyless local relay only over the OpenAI protocol
# --------------------------------------------------------------------------


def test_a_local_relay_needs_no_key_only_over_the_openai_protocol(qtbot, fetch):
    config = _config(RELAY_BACKEND, base_url="http://127.0.0.1:3000/v1", protocol="openai")
    dialog = _open(qtbot, config, show=False)
    assert dialog._model_list_credentials_ready(RELAY_BACKEND) is True

    dialog._backend_protocol_var.set(dialog._copy("relay_protocol_anthropic"))
    assert dialog._model_list_credentials_ready(RELAY_BACKEND) is False

    dialog._backend_protocol_var.set(dialog._copy("relay_protocol_openai"))
    dialog._backend_base_url_var.set(RELAY_A)
    assert dialog._model_list_credentials_ready(RELAY_BACKEND) is False


# --------------------------------------------------------------------------
# Model selector, scrolling popups and saving the list
# --------------------------------------------------------------------------


def test_listed_model_combo_is_editable_searchable_and_scrolls(qtbot, fetch):
    dialog = _open(qtbot, _relay_config(), show=False)
    combo = dialog._backend_model_combo

    assert isinstance(combo, NoWheelComboBox)
    assert combo.isEditable()
    completer = combo.completer()
    assert completer is not None
    assert completer.filterMode() == Qt.MatchFlag.MatchContains
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert COMBO_MAX_VISIBLE_ITEMS == 12
    assert combo.maxVisibleItems() == 12
    assert NoWheelComboBox().maxVisibleItems() == 12
    assert "combobox-popup: 0" in dialog.styleSheet()


def test_saving_stores_the_read_list_only_for_its_own_endpoint(qtbot, fetch):
    fetch.results[RELAY_A] = _remote(RELAY_BACKEND, ("gpt-6-luna", "relay-model"))
    config = _relay_config()
    dialog = _open(qtbot, config)
    _wait_idle(qtbot, dialog, fetch, 1)
    dialog._save()

    saved = config["translation"][RELAY_BACKEND]
    assert saved["remote_models"] == ["gpt-6-luna", "relay-model"]
    assert saved["remote_models_source"] == model_list_source_key(RELAY_BACKEND, RELAY_A, "openai")

    # Reopen with the saved list, point the relay elsewhere and save again:
    # relay A's list must not be stored for relay B.
    dialog = _open(qtbot, config, show=False)
    assert "relay-model" in _combo_items(dialog._backend_model_combo)
    assert _status(dialog) == dialog._copy("model_list_saved", count="2")
    dialog._backend_base_url_entry.setText(RELAY_B)
    dialog._backend_base_url_entry.editingFinished.emit()
    dialog._save()

    saved = config["translation"][RELAY_BACKEND]
    assert saved["base_url"] == RELAY_B
    assert "remote_models" not in saved
    assert "remote_models_source" not in saved
    assert len(fetch.calls) == 1


# --------------------------------------------------------------------------
# TTS/ASR selectors and the Hunyuan sites
# --------------------------------------------------------------------------


def test_persona_switch_only_shows_for_instruct_models(qtbot):
    config = _config("microsoft_edge_web")
    config["tts"] = {
        "enabled": True,
        "engine": "qwen_tts",
        "qwen_tts": {
            "api_key": "tts-key",
            "region": "singapore",
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
        },
    }
    dialog = _open(qtbot, config, show=False, page="tts")

    assert dialog._tts_persona_frame.isHidden()
    dialog._tts_api_model_entry.setEditText("qwen3-tts-instruct-flash-player")
    assert not dialog._tts_persona_frame.isHidden()
    dialog._tts_api_model_entry.setEditText("qwen3-tts-flash")
    assert dialog._tts_persona_frame.isHidden()


def test_qwen3_asr_selector_offers_only_the_flash_alias(qtbot):
    dialog = _open(qtbot, _config("microsoft_edge_web"), show=False)
    assert list(dialog._qwen_model_codes.values()) == ["qwen3-asr-flash"]


@pytest.mark.parametrize("language", ("en", "zh-CN", "ja", "ru", "ko"))
def test_hunyuan_region_selector_shows_localized_sites(qtbot, language):
    config = _config("hunyuan", language=language, api_key="th-key")
    dialog = _open(qtbot, config, show=False)
    _switch_backend(dialog, "hunyuan")
    copy = settings_module.QT_SETTINGS_COPY

    assert list(dialog._qwen_translation_region_codes.items()) == [
        (copy["hunyuan_region_china"][language], "china"),
        (copy["hunyuan_region_global"][language], "global"),
        (copy["hunyuan_region_custom"][language], "custom"),
    ]
    if language != "en":
        for key in ("hunyuan_region_china", "hunyuan_region_global", "hunyuan_region_custom"):
            assert copy[key][language] != copy[key]["en"], key
    region_combo = _combo_with_item(dialog, copy["hunyuan_region_global"][language])
    assert _combo_items(region_combo) == [
        copy["hunyuan_region_china"][language],
        copy["hunyuan_region_global"][language],
        copy["hunyuan_region_custom"][language],
    ]


# --------------------------------------------------------------------------
# S8-S11: cloud voice cloning
# --------------------------------------------------------------------------

READY = {
    "voice_id": "qwen-audio-3.0-tts-flash-ready-111111",
    "display_name": "Ready Voice",
    "target_model": "qwen-audio-3.0-tts-flash",
    "region": "china_mainland",
    "status": "OK",
}
REVIEWING = {
    "voice_id": "qwen-audio-3.0-tts-flash-review-222222",
    "display_name": "New Voice",
    "target_model": "qwen-audio-3.0-tts-flash",
    "region": "china_mainland",
    "status": "DEPLOYING",
}


def _clone_config(*, selected: str, voices=(READY, REVIEWING), target: str = "ja", **engine):
    config = _config("microsoft_edge_web")
    config["translation"]["target_language"] = target
    engine_cfg = {
        "api_key": "sk-beijing",
        "region": "china_mainland",
        "base_url": QWEN_TTS_BASE_URL_MAINLAND,
        "model": "qwen-audio-3.0-tts-flash",
        "voice": selected,
        "upload_consent": True,
        "custom_voices": [dict(voice) for voice in voices],
    }
    engine_cfg.update(engine)
    config["tts"] = {"enabled": True, "engine": "qwen_vc", "qwen_vc": engine_cfg}
    return config


def _open_clone_page(qtbot, config) -> SettingsWindow:
    dialog = _open(qtbot, config, show=False, page="tts")
    expected = len(config["tts"]["qwen_vc"]["custom_voices"]) or 1
    qtbot.waitUntil(lambda: dialog._tts_voice_combo.count() == expected, timeout=3000)
    return dialog


def _card(dialog: SettingsWindow) -> str:
    return dialog._tts_runtime_status_label.text()


def test_voice_labels_mark_voices_in_review_and_keep_their_ids(qtbot):
    config = _clone_config(selected=REVIEWING["voice_id"])
    dialog = _open_clone_page(qtbot, config)
    reviewing_label = f"New Voice ({dialog._copy('voice_clone_status_reviewing')})"

    assert _combo_items(dialog._tts_voice_combo) == ["Ready Voice", reviewing_label]
    assert dialog._tts_voice_combo.currentText() == reviewing_label
    assert dialog._selected_tts_voice_id() == REVIEWING["voice_id"]
    # The card was refreshed after the list was applied.
    assert _card(dialog) == dialog._copy("voice_clone_reviewing")

    dialog._save()
    assert config["tts"]["qwen_vc"]["voice"] == REVIEWING["voice_id"]


def test_runtime_card_follows_the_voice_selection(qtbot):
    dialog = _open_clone_page(qtbot, _clone_config(selected=REVIEWING["voice_id"]))

    dialog._tts_voice_combo.setCurrentText("Ready Voice")
    assert dialog._selected_tts_voice_id() == READY["voice_id"]
    assert _card(dialog) == dialog._copy("voice_clone_ready")

    dialog._tts_voice_combo.setCurrentIndex(1)
    assert _card(dialog) == dialog._copy("voice_clone_reviewing")


def test_review_status_ignores_the_combo_placeholder(qtbot):
    dialog = _open(qtbot, _clone_config(selected=REVIEWING["voice_id"]), show=False)
    dialog._tts_voice_var.set(dialog._copy("tts_voice_loading"))

    assert dialog._selected_cloned_voice_status() == "DEPLOYING"


def test_preflight_waits_for_a_voice_in_review(qtbot):
    dialog = _open(qtbot, _clone_config(selected=REVIEWING["voice_id"]), show=False)
    assert dialog._voice_clone_preflight_error() == dialog._copy("voice_clone_reviewing")

    dialog = _open(qtbot, _clone_config(selected=READY["voice_id"]), show=False)
    assert dialog._voice_clone_preflight_error() is None


def test_enrollment_success_selects_the_new_voice_and_reports_the_review(
    qtbot, monkeypatch, boxes
):
    dialog = _open_clone_page(qtbot, _clone_config(selected=READY["voice_id"]))
    new_voice = ClonedVoice(
        voice_id="qwen-audio-3.0-tts-flash-fresh-333333",
        display_name="Fresh",
        target_model="qwen-audio-3.0-tts-flash",
        region="china_mainland",
        status="DEPLOYING",
    )

    def fake_create_voice(config, _audio, _name):
        engine = config["tts"]["qwen_vc"]
        engine["custom_voices"] = list(engine.get("custom_voices", [])) + [new_voice.as_dict()]
        engine["voice"] = new_voice.voice_id
        return new_voice

    cards: list[str] = []
    monkeypatch.setattr(voice_clone_service, "create_voice", fake_create_voice)
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "information",
        lambda _parent, title, message, *_a, **_k: (
            cards.append(_card(dialog)),
            boxes.append(("information", str(title), str(message))),
        ),
    )

    dialog._enroll_cloned_voice(b"RIFF0000WAVEfmt ", "Fresh")
    qtbot.waitUntil(lambda: bool(boxes), timeout=3000)

    assert boxes[0][2] == dialog._copy("voice_clone_created_reviewing", voice="Fresh")
    # The card already described the new voice when the dialog appeared.
    assert cards == [dialog._copy("voice_clone_reviewing")]
    qtbot.waitUntil(lambda: dialog._tts_voice_combo.count() == 3, timeout=3000)
    assert dialog._selected_tts_voice_id() == new_voice.voice_id


def test_failed_cloned_voice_test_synthesis_is_explained(qtbot, boxes):
    dialog = _open(qtbot, _clone_config(selected=READY["voice_id"]), show=False)

    def finish(message: str) -> None:
        dialog._tts_testing = True
        dialog._tts_test_manager = object()
        dialog._finish_tts_test(dialog._tts_test_generation, False, message)

    finish("tts_error:authentication")
    finish("tts_error:billing")
    finish("tts_error:unsupported_model")
    finish("tts_error:configuration")
    finish("tts_error:cancelled")

    title = tr(dialog._ui_lang, "tts_test")
    assert boxes == [
        ("warning", title, dialog._copy("voice_clone_auth_failed")),
        ("warning", title, dialog._copy("voice_clone_access_denied")),
        (
            "warning",
            title,
            dialog._copy("voice_clone_model_unsupported", model=QWEN_VC_DEFAULT_MODEL),
        ),
        (
            "warning",
            title,
            dialog._copy(
                "tts_engine_test_failed",
                engine=settings_module.get_tts_engine_label("qwen_vc", dialog._ui_lang),
                reason=dialog._copy("tts_reason_configuration"),
            ),
        ),
    ]


def test_a_qwen_tts_account_problem_is_not_reported_as_temporary(qtbot, boxes):
    """A 403 for the account (billing token) needs the account, not a retry."""

    config = _config("microsoft_edge_web")
    config["tts"] = {
        "enabled": True,
        "engine": "qwen_tts",
        "qwen_tts": {
            "api_key": "sk-beijing",
            "region": "china_mainland",
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
        },
    }
    dialog = _open(qtbot, config, show=False, page="tts")

    def finish(message: str) -> None:
        dialog._tts_testing = True
        dialog._tts_test_manager = object()
        dialog._finish_tts_test(dialog._tts_test_generation, False, message)

    finish("tts_error:billing")
    finish("tts_error:provider")

    title = tr(dialog._ui_lang, "tts_test")
    assert boxes == [
        ("warning", title, dialog._copy("qwen_tts_billing_failed")),
        ("warning", title, dialog._copy("qwen_tts_provider_failed")),
    ]
    assert dialog._copy("qwen_tts_billing_failed") != dialog._copy("qwen_tts_provider_failed")


def test_a_cloned_voice_test_that_raises_is_explained(qtbot, monkeypatch, boxes):
    class _RaisingManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def is_available(self):
            return True

        def start(self):
            pass

        def speak(self, *_args, **_kwargs):
            raise RuntimeError("Qwen TTS account access denied (HTTP 403): AccessDenied")

        def stop(self):
            pass

    monkeypatch.setattr(settings_module, "TTSManager", _RaisingManager)
    dialog = _open(qtbot, _clone_config(selected=READY["voice_id"]), show=False)

    dialog._on_tts_test()

    assert boxes == [
        ("warning", tr(dialog._ui_lang, "tts_test"), dialog._copy("voice_clone_access_denied"))
    ]
    assert dialog._tts_testing is False


def test_voice_clone_errors_tell_401_from_403_and_review_and_upload_failures(qtbot):
    dialog = _open(qtbot, _clone_config(selected=READY["voice_id"]), show=False)
    localized = dialog._localized_voice_clone_error

    def error(message, **kwargs):
        return QwenVoiceEnrollmentError(message, **kwargs)

    assert localized(
        error(
            "Qwen voice cloning rejected the API key: InvalidApiKey",
            status_code=401,
            provider_code="InvalidApiKey",
        )
    ) == f"{dialog._copy('voice_clone_auth_failed')}\n\n(HTTP 401 / InvalidApiKey)"
    for code in ("AccessDenied", "AccessDenied.Unpurchased", "AllocationQuota.FreeTierOnly"):
        assert localized(
            error(
                "Qwen voice cloning rejected the API key. Check that the key is valid",
                status_code=403,
                provider_code=code,
            )
        ) == f"{dialog._copy('voice_clone_access_denied')}\n\n(HTTP 403 / {code})"
    assert localized(error("Forbidden", status_code=403)) == (
        f"{dialog._copy('voice_clone_access_denied')}\n\n(HTTP 403)"
    )
    assert localized(
        error("The service rejected this recording during review", provider_code="UNDEPLOYED")
    ) == f"{dialog._copy('voice_clone_review_rejected')}\n\n(UNDEPLOYED)"
    # The temporary-storage host's 403 is an upload problem, not a bad key.
    assert localized(
        error("Uploading the reference recording failed (HTTP 403)", status_code=403)
    ) == f"{dialog._copy('voice_clone_upload_failed')}\n\n(HTTP 403)"
    assert localized(
        voice_clone_service.VoiceCloneModelUnsupportedError("Model 'x' cannot use cloned voices")
    ) == dialog._copy("voice_clone_model_unsupported", model=QWEN_VC_DEFAULT_MODEL)


def test_a_cloning_key_copied_from_a_singapore_qwen_tts_key_is_flagged(qtbot):
    config = _clone_config(selected=READY["voice_id"], api_key="sk-shared")
    config["tts"]["qwen_tts"] = {"api_key": "sk-shared", "region": "singapore"}
    dialog = _open_clone_page(qtbot, config)

    warning = dialog._voice_clone_region_warning()
    assert warning == dialog._copy(
        "voice_clone_region_fallback",
        region=dialog._tts_api_region_label("qwen_vc", "china_mainland"),
    )
    assert _card(dialog) == warning
    assert dialog._voice_clone_preflight_error() == warning


def test_a_separate_cloning_key_is_not_flagged(qtbot):
    config = _clone_config(selected=READY["voice_id"], api_key="sk-beijing")
    config["tts"]["qwen_tts"] = {"api_key": "sk-singapore", "region": "singapore"}
    dialog = _open_clone_page(qtbot, config)

    assert dialog._voice_clone_region_warning() is None
    assert _card(dialog) == dialog._copy("voice_clone_ready")
    assert dialog._voice_clone_preflight_error() is None


BEIJING_OR_UNKNOWN_HOSTS = (
    "https://dashscope.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.cn-beijing.maas.aliyuncs.com/api/v1",
    "https://trial.cn-beijing.maas.aliyuncs.com/api/v1",
    "https://tts-proxy.example.com/api/v1",
    "https://dashscope-intl.aliyuncs.com.example.net/api/v1",
    "http://127.0.0.1:8080/api/v1",
    "",
    "not a url",
)
OTHER_REGION_HOSTS = (
    "https://dashscope-intl.aliyuncs.com/api/v1",
    "HTTPS://DASHSCOPE-INTL.ALIYUNCS.COM/api/v1",
    "https://dashscope-us.aliyuncs.com/api/v1",
    "https://cn-hongkong.dashscope.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.ap-southeast-1.maas.aliyuncs.com/api/v1",
    "https://trial.ap-southeast-1.maas.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.ap-northeast-1.maas.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.eu-central-1.maas.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.us-east-1.maas.aliyuncs.com/api/v1",
    "https://llm-1a2b3c.cn-hongkong.maas.aliyuncs.com:443/api/v1",
)


@pytest.mark.parametrize("base_url", BEIJING_OR_UNKNOWN_HOSTS)
def test_beijing_and_unknown_dashscope_hosts_are_not_called_foreign(base_url):
    assert settings_module._dashscope_url_is_outside_beijing(base_url) is False


@pytest.mark.parametrize("base_url", OTHER_REGION_HOSTS)
def test_dashscope_hosts_of_other_regions_are_recognised(base_url):
    assert settings_module._dashscope_url_is_outside_beijing(base_url) is True


@pytest.mark.parametrize(
    "base_url",
    (
        "https://llm-1a2b3c.cn-beijing.maas.aliyuncs.com/api/v1",
        "https://dashscope.aliyuncs.com/api/v1",
        "https://tts-proxy.example.com/api/v1",
    ),
)
def test_a_key_shared_with_a_custom_beijing_or_unknown_qwen_tts_address_may_clone(
    qtbot, base_url
):
    config = _clone_config(selected=READY["voice_id"], api_key="sk-same")
    config["tts"]["qwen_tts"] = {"api_key": "sk-same", "region": "custom", "base_url": base_url}
    dialog = _open_clone_page(qtbot, config)

    assert dialog._voice_clone_region_warning() is None
    assert _card(dialog) == dialog._copy("voice_clone_ready")
    # The preflight is what blocks the cloning test.
    assert dialog._voice_clone_preflight_error() is None


def test_a_key_shared_with_a_custom_foreign_qwen_tts_address_is_flagged(qtbot):
    config = _clone_config(selected=READY["voice_id"], api_key="sk-same")
    config["tts"]["qwen_tts"] = {
        "api_key": "sk-same",
        "region": "custom",
        "base_url": "https://llm-1a2b3c.ap-southeast-1.maas.aliyuncs.com/api/v1",
    }
    dialog = _open_clone_page(qtbot, config)

    warning = dialog._copy(
        "voice_clone_region_fallback",
        region=dialog._tts_api_region_label("qwen_vc", "china_mainland"),
    )
    assert dialog._voice_clone_region_warning() == warning
    assert _card(dialog) == warning
    assert dialog._voice_clone_preflight_error() == warning


def test_a_missing_cloning_key_next_to_a_custom_beijing_address_just_asks_for_a_key(qtbot):
    config = _clone_config(selected="", voices=(), api_key="")
    config["tts"]["qwen_tts"] = {
        "api_key": "sk-workspace",
        "region": "custom",
        "base_url": "https://llm-1a2b3c.cn-beijing.maas.aliyuncs.com/api/v1",
    }
    dialog = _open(qtbot, config, show=False, page="tts")
    required = dialog._copy("voice_clone_api_key_required")

    assert dialog._voice_clone_region_warning() is None
    qtbot.waitUntil(lambda: _card(dialog) == required, timeout=3000)
    assert dialog._voice_clone_preflight_error() == required


@pytest.mark.parametrize(("target", "expected"), (("ja", "Japanese"), ("en", "English"), ("zh", "Chinese")))
def test_cloned_voice_tests_send_the_language_of_the_test_text(qtbot, target, expected):
    dialog = _open(qtbot, _clone_config(selected=READY["voice_id"], target=target), show=False)

    engine_cfg = dialog._current_tts_engine_config("qwen_vc")

    assert engine_cfg["language_type"] == expected
    assert engine_cfg["model"] == "qwen-audio-3.0-tts-flash"
    assert engine_cfg["base_url"] == QWEN_TTS_BASE_URL_MAINLAND
