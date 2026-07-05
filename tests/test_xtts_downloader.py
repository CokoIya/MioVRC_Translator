from __future__ import annotations

from src.asr.hf_model_downloader import DownloadProgress, DownloadState
from src.tts import xtts_downloader


def test_xtts_model_exists_uses_hf_manifest_completeness(monkeypatch, tmp_path):
    checked: list[str] = []
    model_path = tmp_path / "xtts"
    model_path.mkdir()
    for filename in xtts_downloader.XTTS_V2_FILES:
        (model_path / filename).write_bytes(b"x")
    monkeypatch.setattr(
        xtts_downloader,
        "XTTS_V2_FILE_MIN_BYTES",
        {filename: 1 for filename in xtts_downloader.XTTS_V2_FILES},
    )

    def fake_model_is_complete(model_id: str) -> bool:
        checked.append(model_id)
        return model_id == xtts_downloader.XTTS_V2_REPO

    monkeypatch.setattr(xtts_downloader, "model_is_complete", fake_model_is_complete)
    monkeypatch.setattr(xtts_downloader, "xtts_model_path", lambda: model_path)

    assert xtts_downloader.xtts_model_exists() is True
    assert checked == [xtts_downloader.XTTS_V2_REPO]


def test_xtts_coqui_model_kwargs_uses_managed_local_model(monkeypatch, tmp_path):
    model_path = tmp_path / "xtts"
    model_path.mkdir()
    for filename in xtts_downloader.XTTS_V2_FILES:
        (model_path / filename).write_bytes(b"x")

    monkeypatch.setattr(xtts_downloader, "model_is_complete", lambda model_id: True)
    monkeypatch.setattr(xtts_downloader, "xtts_model_path", lambda: model_path)
    monkeypatch.setattr(
        xtts_downloader,
        "XTTS_V2_FILE_MIN_BYTES",
        {filename: 1 for filename in xtts_downloader.XTTS_V2_FILES},
    )

    assert xtts_downloader.xtts_coqui_model_kwargs() == {
        "model_path": str(model_path),
        "config_path": str(model_path / "config.json"),
        "progress_bar": False,
    }


def test_xtts_model_integrity_rejects_suspiciously_small_files(monkeypatch, tmp_path):
    model_path = tmp_path / "xtts"
    model_path.mkdir()
    for filename in xtts_downloader.XTTS_V2_FILES:
        (model_path / filename).write_bytes(b"x")

    monkeypatch.setattr(xtts_downloader, "model_is_complete", lambda model_id: True)
    monkeypatch.setattr(xtts_downloader, "xtts_model_path", lambda: model_path)

    assert xtts_downloader.xtts_model_exists() is False
    assert any("model.pth" in error for error in xtts_downloader.xtts_model_integrity_errors())


def test_xtts_downloader_constructs_hf_downloader_with_model_id(monkeypatch):
    created: list[str] = []

    class FakeHFDownloader:
        def __init__(self, model_id: str):
            created.append(model_id)
            self.progress = DownloadProgress(state=DownloadState.IDLE)
            self.state = DownloadState.IDLE
            self.started = False

        def start(self):
            self.started = True
            self.state = DownloadState.DOWNLOADING

    monkeypatch.setattr(xtts_downloader, "HFModelDownloader", FakeHFDownloader)

    downloader = xtts_downloader.XTTSDownloader()
    downloader.start()

    assert created == [xtts_downloader.XTTS_V2_REPO]
    assert downloader.is_downloading() is True


def test_xtts_downloader_forwards_shared_progress_api(monkeypatch):
    listeners = []

    class FakeHFDownloader:
        def __init__(self, model_id: str):
            self.model_id = model_id
            self._progress = DownloadProgress(state=DownloadState.IDLE)

        def add_listener(self, cb):
            listeners.append(cb)

        @property
        def progress(self):
            return self._progress

        @property
        def state(self):
            return self._progress.state

        def start(self):
            self._progress = DownloadProgress(
                state=DownloadState.DOWNLOADING,
                total_bytes=5,
                total_total=10,
            )
            for listener in list(listeners):
                listener(self._progress)

    monkeypatch.setattr(xtts_downloader, "HFModelDownloader", FakeHFDownloader)

    observed = []
    downloader = xtts_downloader.XTTSDownloader()
    downloader.add_listener(observed.append)
    downloader.start()

    assert observed[-1].state == DownloadState.DOWNLOADING
    assert observed[-1].overall_fraction == 0.5
    assert downloader.progress.overall_fraction == 0.5
