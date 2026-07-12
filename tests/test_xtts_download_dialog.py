from __future__ import annotations

from src.asr.hf_model_downloader import DownloadProgress, DownloadState
from src.updater.update_checker import UpdateInfo
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
    monkeypatch.setattr(
        xtts_download_dialog,
        "xtts_runtime_status",
        lambda **_kwargs: type("Status", (), {"ready": True, "missing_component_names": ()})(),
    )
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, _cb: None)

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    assert dialog.maximumWidth() == 500
    assert dialog.minimumWidth() == 500
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
    monkeypatch.setattr(
        xtts_download_dialog,
        "xtts_runtime_status",
        lambda **_kwargs: type("Status", (), {"ready": True, "missing_component_names": ()})(),
    )
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, _cb: None)

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    dialog._auto_start()

    assert dialog._progress_widget._retry_btn.isHidden() is False
    assert "404 Client Error" in dialog._progress_widget._speed_label.text()


def test_xtts_download_dialog_offers_full_installer_when_runtime_component_missing(qtbot, monkeypatch):
    class FakeDownloader:
        state = DownloadState.IDLE
        progress = DownloadProgress()

        def add_listener(self, _cb):
            pass

        def start(self):
            pass

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(xtts_download_dialog, "XTTSDownloader", FakeDownloader)
    monkeypatch.setattr(xtts_download_dialog, "xtts_models_ready", lambda: True)
    monkeypatch.setattr(
        xtts_download_dialog,
        "xtts_runtime_status",
        lambda **_kwargs: type(
            "Status",
            (),
            {
                "ready": False,
                "missing_component_names": ("MP3/audio decoder runtime",),
            },
        )(),
    )
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, cb: cb())

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    assert not dialog._release_btn.isHidden()
    assert not dialog._close_btn.isHidden()
    assert dialog._release_btn.text() == "Download Full Installer"
    assert "MP3/audio decoder runtime" in dialog._bottom_label.text()


def test_xtts_download_dialog_runtime_button_opens_update_window(qtbot, monkeypatch):
    opened: list[tuple[object, UpdateInfo, str]] = []

    class FakeDownloader:
        state = DownloadState.IDLE
        progress = DownloadProgress()

        def add_listener(self, _cb):
            pass

        def start(self):
            pass

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            pass

    class FakeUpdateWindow:
        def __init__(self, parent, info, ui_lang):
            opened.append((parent, info, ui_lang))

        def show(self):
            pass

        def raise_(self):
            pass

        def activateWindow(self):
            pass

    def fake_fetch(on_installer_available, **kwargs):
        assert kwargs["max_retries"] == 2
        assert kwargs["retry_delays"] == (2,)
        on_installer_available(
            UpdateInfo(
                version="v1.3.7.8",
                download_url="https://78hejiu.top/MioTranslator-Setup.exe",
                sha256="a" * 64,
            )
        )
        return None

    monkeypatch.setattr(xtts_download_dialog, "XTTSDownloader", FakeDownloader)
    monkeypatch.setattr(xtts_download_dialog, "xtts_models_ready", lambda: True)
    monkeypatch.setattr(
        xtts_download_dialog,
        "xtts_runtime_status",
        lambda **_kwargs: type(
            "Status",
            (),
            {
                "ready": False,
                "missing_component_names": ("scikit-learn runtime",),
            },
        )(),
    )
    monkeypatch.setattr(xtts_download_dialog, "fetch_latest_installer_info", fake_fetch)
    monkeypatch.setattr("src.ui_qt.update_window.UpdateWindow", FakeUpdateWindow)
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, cb: cb())

    dialog = xtts_download_dialog.XTTSDownloadDialog(ui_lang="en")
    qtbot.addWidget(dialog)

    dialog._download_full_installer()

    assert len(opened) == 1
    assert opened[0][1].version == "v1.3.7.8"
    assert opened[0][1].flow == "repair"
    assert "scikit-learn" in opened[0][1].localized_notes["en"]


def test_xtts_download_dialog_requires_api_runtime_preflight(qtbot, monkeypatch):
    requested: list[dict] = []

    class FakeDownloader:
        state = DownloadState.IDLE
        progress = DownloadProgress()

        def add_listener(self, _cb):
            pass

        def start(self):
            pass

        def pause(self):
            pass

        def resume(self):
            pass

        def cancel(self):
            pass

    def fake_status(**kwargs):
        requested.append(kwargs)
        return type(
            "Status",
            (),
            {
                "ready": False,
                "missing_component_names": ("scikit-learn runtime",),
            },
        )()

    monkeypatch.setattr(xtts_download_dialog, "XTTSDownloader", FakeDownloader)
    monkeypatch.setattr(xtts_download_dialog, "xtts_models_ready", lambda: False)
    monkeypatch.setattr(xtts_download_dialog, "xtts_runtime_status", fake_status)
    monkeypatch.setattr(xtts_download_dialog.QTimer, "singleShot", lambda _ms, _cb: None)

    dialog = xtts_download_dialog.XTTSDownloadDialog()
    qtbot.addWidget(dialog)

    assert requested == [{"require_api": True}]
    assert not dialog._release_btn.isHidden()
    assert not dialog._close_btn.isHidden()
    assert "scikit-learn runtime" in dialog._runtime_label.text()
