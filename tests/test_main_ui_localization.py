from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QFileDialog

from src.ui_qt.audio_diagnostics_window import AudioDiagnosticsWindow
from src.ui_qt.main_window import MainWindow
from src.ui_qt.mode_wizard_dialog import ModeWizardDialog
from src.ui_qt.qt_localization import configure_file_dialog
from src.ui_qt.sponsor_window import SponsorWindow
from src.ui_qt.vad_calibration_window import VadCalibrationWindow
from src.ui_qt.voice_recording_dialog import VoiceRecordingDialog




def test_mode_wizard_switches_language_without_recreation(qtbot):
    dialog = ModeWizardDialog(ui_language="en")
    qtbot.addWidget(dialog)

    dialog.update_language("ko-KR")

    assert dialog.windowTitle() == "Mio 첫 설정 가이드"
    assert dialog._apply_btn.text() == "추천 적용"
    assert dialog._mode_title_labels["listen"].text() == "다른 사람 말 듣기"


def test_mode_wizard_action_buttons_fit_long_system_font_labels(qtbot):
    dialog = ModeWizardDialog(ui_language="ru")
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(10)

    for button in (dialog._skip_btn, dialog._apply_btn, dialog._open_settings_btn):
        assert button.width() >= button.sizeHint().width()


def test_audio_diagnostics_switches_language_and_formats_locale_numbers(qtbot):
    dialog = AudioDiagnosticsWindow(
        None,
        target="mic",
        snapshot_provider=lambda _target: {
            "running": True,
            "vad_min_rms": 0.0123,
            "frames_processed": 12345,
            "segments_emitted": 3,
            "capture_rate": 48000,
            "last_error": "Invalid device selection",
        },
        ui_language="en",
    )
    qtbot.addWidget(dialog)

    dialog.update_language("ru-RU")

    assert dialog.windowTitle() == "Диагностика микрофона"
    assert dialog._labels["threshold"].text() == "0,0123"
    assert dialog._labels["frames"].text() == "12\u202f345"
    assert "Invalid device selection" not in dialog._labels["error"].text()


def test_vad_calibration_localizes_confidence_and_runtime_language(qtbot):
    dialog = VadCalibrationWindow(
        None,
        target="mic",
        snapshot_provider=lambda _target: {},
        apply_callback=lambda _target, _result: None,
        ui_language="en",
    )
    qtbot.addWidget(dialog)

    dialog.update_language("ru")

    assert dialog.windowTitle() == "Калибровка авторазделения микрофона"
    assert dialog._labels["confidence"].text() == "Низкая"


def test_sponsor_window_uses_app_language_and_can_switch(qtbot, monkeypatch):
    monkeypatch.setattr(
        "src.ui_qt.sponsor_window.get_sponsors",
        lambda callback, **_kwargs: callback({"sponsors": []}),
    )
    dialog = SponsorWindow(None, ui_language="ru")
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Спонсоры · Поддержка"
    assert dialog._refresh_btn.text() == "Обновить"

    dialog.update_language("ko")

    assert dialog.windowTitle() == "후원자 · 지원"
    assert dialog._donate_btn.text() == "후원 페이지"


def test_voice_quality_errors_use_dialog_language_and_locale_numbers(qtbot):
    dialog = VoiceRecordingDialog(ui_lang="ru")
    qtbot.addWidget(dialog)
    stats = SimpleNamespace(
        duration_seconds=1.25,
        peak=0.5,
        rms=0.2,
        active_duration_seconds=1.0,
        dynamic_range=0.5,
        dc_offset=0.0,
        clipped_ratio=0.0,
    )

    message = dialog._localized_quality_problem(stats)

    assert message is not None
    assert "1,2 с" in message
    assert "Reference audio" not in message


def test_voice_recording_title_wraps_in_long_locales(qtbot):
    dialog = VoiceRecordingDialog(ui_lang="ru")
    qtbot.addWidget(dialog)

    assert dialog._title_label.wordWrap() is True


def test_file_dialog_standard_labels_follow_selected_app_language(qtbot):
    dialog = QFileDialog()
    qtbot.addWidget(dialog)

    configure_file_dialog(dialog, "ru-RU", title="Импорт аудио")

    assert dialog.windowTitle() == "Импорт аудио"
    assert dialog.labelText(QFileDialog.DialogLabel.LookIn) == "Папка:"
    assert dialog.labelText(QFileDialog.DialogLabel.FileType) == "Тип файла:"
    assert dialog.labelText(QFileDialog.DialogLabel.Reject) == "Отмена"


def test_main_osc_guide_paths_are_localized():
    window = MainWindow.__new__(MainWindow)
    window._ui_lang = "ru"
    window._config = {"ui": {"language": "ru"}}

    pages = MainWindow._guide_pages(window)

    assert pages[0][2] == ["Меню действий", "Параметры"]
    assert pages[2][2] == ["OSC", "Включено"]
    assert not pages[0][0].startswith("1. 1.")


def test_main_language_switch_updates_an_open_settings_window():
    received: list[str] = []
    settings = SimpleNamespace(update_language=received.append)
    window = MainWindow.__new__(MainWindow)
    window._ui_lang = "ko"
    window._settings_window = settings
    window._floating_window = None
    window._text_input_window = None
    window._tweaks_panel = None
    window._mode_wizard_dialog = None
    window._sponsor_window = None
    window._update_win = None
    window._audio_diagnostics_windows = {}
    window._vad_calibration_windows = {}
    window._asr = None
    window._listen_asr = None
    window._refresh_osc_guide_language = lambda: None

    MainWindow._refresh_open_window_languages(window)

    assert received == ["ko"]
