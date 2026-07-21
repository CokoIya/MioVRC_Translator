from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ui_qt import settings_window as settings_module
from src.ui_qt.settings_window import SettingsWindow


@pytest.mark.parametrize(
    ("token", "expected_key"),
    (
        ("tts_error:network", "qwen_tts_network_failed"),
        ("tts_error:timeout", "qwen_tts_timeout_failed"),
        ("tts_error:rate_limit", "qwen_tts_rate_limit_failed"),
        ("tts_error:configuration", "qwen_tts_configuration_failed"),
        ("tts_error:unsupported_model", "qwen_tts_configuration_failed"),
        ("tts_error:invalid_endpoint", "qwen_tts_configuration_failed"),
        ("tts_error:queue_full", "qwen_tts_busy_failed"),
        ("tts_error:pipeline_full", "qwen_tts_busy_failed"),
        ("tts_error:suspended", "qwen_tts_busy_failed"),
        ("tts_error:playback", "qwen_tts_playback_failed"),
        ("tts_error:safety", "qwen_tts_safety_failed"),
        ("tts_error:invalid_input", "qwen_tts_input_failed"),
        ("tts_error:provider", "qwen_tts_provider_failed"),
        ("tts_error:unavailable", "qwen_tts_configuration_failed"),
    ),
)
def test_qwen_tts_settings_test_maps_structured_errors_to_localized_copy(
    monkeypatch,
    token,
    expected_key,
):
    dialog = SettingsWindow.__new__(SettingsWindow)
    dialog._ui_lang = "en"
    dialog._tts_testing = True
    dialog._tts_test_generation = 3
    dialog._tts_test_manager = object()
    dialog._tts_test_timeout_timer = SimpleNamespace(stop=lambda: None)
    dialog._set_tts_testing = lambda testing: setattr(dialog, "_tts_testing", testing)
    dialog._stop_tts_test_manager = lambda: setattr(
        dialog,
        "_tts_test_manager",
        None,
    )
    dialog._selected_tts_engine = lambda: "qwen_tts"
    dialog._copy = lambda key, **_kwargs: key
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    dialog._finish_tts_test(3, False, token)

    assert warnings == [("Test", expected_key)]
