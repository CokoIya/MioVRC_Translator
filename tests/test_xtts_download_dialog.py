from __future__ import annotations

from src.asr.hf_model_downloader import DownloadProgress, DownloadState
from src.ui_qt import xtts_download_dialog


def test_xtts_download_dialog_uses_shared_progress_widget(qtbot, monkeypatch):
    class FakeDownloader:
        state = DownloadState.IDLE
        progress = DownloadProgress()

        def add_listener(self, _cb):
            pass

        def start(self):
            self.state = DownloadState.DOWNLOADING

        def pause(self):
            self.state = DownloadState.PAUSED

        def resume(self):
            self.state = DownloadState.DOWNLOADING

        def cancel(self):
            self.state = DownloadState.CANCELLED

    monkeypatch.setattr(xtts_download_dialog, "XTTSDownloader", FakeDownloader)
    monkeypatch.setattr(xtts_download_dialog, "xtts_models_ready", lambda: False)
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, _cb: None)

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    assert dialog.maximumWidth() == 480
    assert dialog.minimumWidth() == 480
    assert dialog._progress_widget._downloader is dialog._downloader
    assert dialog.windowTitle() == "Download Voice Cloning Model"


def test_xtts_download_dialog_surfaces_downloader_errors(qtbot, monkeypatch):
    class FakeDownloader:
        def __init__(self):
            self.state = DownloadState.IDLE
            self.progress = DownloadProgress()
            self._listeners = []

        def add_listener(self, cb):
            self._listeners.append(cb)

        def start(self):
            self.state = DownloadState.ERROR
            self.progress = DownloadProgress(
                state=DownloadState.ERROR,
                error="404 Client Error: Not Found",
            )
            for listener in list(self._listeners):
                listener(self.progress)

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            self.state = DownloadState.CANCELLED

    monkeypatch.setattr(xtts_download_dialog, "XTTSDownloader", FakeDownloader)
    monkeypatch.setattr(xtts_download_dialog, "xtts_models_ready", lambda: False)
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, _cb: None)

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    dialog._auto_start()

    assert dialog._progress_widget._retry_btn.isHidden() is False
    assert "404 Client Error" in dialog._progress_widget._speed_label.text()
