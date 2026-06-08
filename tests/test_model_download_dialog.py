from __future__ import annotations

from src.ui_qt import model_download_dialog
from src.ui_qt.model_download_dialog import ModelMissingDialog, SetupWindow


def test_whisper_missing_dialog_uses_whisper_download_text(qtbot):
    started: list[bool] = []
    dialog = ModelMissingDialog(
        None,
        "whisper-large-v3-turbo",
        on_download_click=lambda: started.append(True),
        is_model_ready=lambda: False,
        is_download_running=lambda: True,
        ui_lang="zh-CN",
    )
    qtbot.addWidget(dialog)

    dialog._start_download()

    assert started == [True]
    assert "Whisper" in dialog._status_label.text()
    assert "SenseVoice" not in dialog._status_label.text()


def test_chinese_missing_dialog_uses_model_wording(qtbot):
    dialog = ModelMissingDialog(
        None,
        "sensevoice-small",
        on_download_click=lambda: None,
        is_model_ready=lambda: False,
        is_download_running=lambda: False,
        ui_lang="zh-CN",
    )
    qtbot.addWidget(dialog)

    texts = [label.text() for label in dialog.findChildren(model_download_dialog.QLabel)]

    assert dialog.windowTitle() == "模型未找到"
    assert any("模型未找到" in text for text in texts)
    assert not any("语音包" in text for text in texts)


def test_asr_setup_window_does_not_create_huggingface_downloader_for_whisper(qtbot, monkeypatch):
    monkeypatch.setattr(model_download_dialog, "model_exists", lambda _spec: True)

    def fail_get_downloader(_model_id):
        raise AssertionError("SetupWindow should use ModelScope progress, not HF downloader")

    monkeypatch.setattr(model_download_dialog, "get_downloader", fail_get_downloader)

    window = SetupWindow("whisper-large-v3-turbo", ui_lang="en")
    qtbot.addWidget(window)

    assert window._progress_widget._downloader is None
    assert "Whisper" in window._engine_label


def test_asr_setup_window_failed_download_keeps_retry_visible(qtbot, monkeypatch):
    monkeypatch.setattr(model_download_dialog, "model_exists", lambda _spec: True)
    window = SetupWindow("whisper-large-v3-turbo", ui_lang="en")
    qtbot.addWidget(window)

    window._on_failed("connection reset")

    assert window._retry_btn.isHidden() is False
    assert "connection reset" in window._bottom_label.text()
    assert "retry" in window._bottom_label.text().lower()
