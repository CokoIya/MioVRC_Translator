"""XTTS-v2 model downloader."""
from __future__ import annotations

import logging
import copy
import threading
from collections.abc import Callable
from pathlib import Path

from src.asr.hf_model_downloader import (
    HFModelDownloader,
    DownloadProgress,
    DownloadState,
    model_dir,
    model_is_complete,
)

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[DownloadProgress], None]

# XTTS-v2 model on Hugging Face
XTTS_V2_REPO = "coqui/XTTS-v2"
XTTS_V2_FILES = [
    "model.pth",
    "config.json",
    "vocab.json",
    "dvae.pth",
    "mel_stats.pth",
]

XTTS_V2_FILE_MIN_BYTES = {
    "model.pth": 1_500_000_000,
    "config.json": 1_000,
    "vocab.json": 100_000,
    "dvae.pth": 150_000_000,
    "mel_stats.pth": 512,
}

# XTTS speaker encoder on Hugging Face
XTTS_SPEAKER_ENCODER_REPO = "coqui/xtts_speaker_encoder"
XTTS_SPEAKER_ENCODER_FILES = [
    "model.pth",
    "config.json",
]


def xtts_model_path() -> Path:
    """Get XTTS-v2 model directory path."""
    return model_dir(XTTS_V2_REPO)


def xtts_speaker_encoder_path() -> Path:
    """Get XTTS speaker encoder directory path."""
    return model_dir(XTTS_SPEAKER_ENCODER_REPO)


def xtts_model_exists() -> bool:
    """Check if XTTS-v2 model is downloaded."""
    return not xtts_model_integrity_errors()


def xtts_model_integrity_errors() -> list[str]:
    """Return missing or suspicious XTTS-v2 model files."""
    errors: list[str] = []
    path = xtts_model_path()
    if not model_is_complete(XTTS_V2_REPO):
        errors.append("XTTS-v2 model manifest is incomplete")
    for filename in XTTS_V2_FILES:
        file_path = path / filename
        if not file_path.is_file():
            errors.append(f"Missing XTTS-v2 file: {filename}")
            continue
        size = file_path.stat().st_size
        minimum = XTTS_V2_FILE_MIN_BYTES.get(filename, 1)
        if size < minimum:
            errors.append(
                f"XTTS-v2 file looks incomplete: {filename} "
                f"({size} bytes, expected at least {minimum} bytes)"
            )
    return errors


def xtts_speaker_encoder_exists() -> bool:
    """Check if XTTS speaker encoder is downloaded."""
    return model_is_complete(XTTS_SPEAKER_ENCODER_REPO)


def xtts_models_ready() -> bool:
    """Check if all XTTS models are downloaded."""
    # Only check main model - speaker encoder is embedded in XTTS-v2
    return xtts_model_exists()


def xtts_coqui_model_kwargs() -> dict[str, str | bool] | None:
    """Return Coqui TTS kwargs for the managed local XTTS-v2 model."""
    if not xtts_models_ready():
        return None
    path = xtts_model_path()
    return {
        "model_path": str(path),
        "config_path": str(path / "config.json"),
        "progress_bar": False,
    }


class XTTSDownloader:
    """Downloader for XTTS-v2 models from Hugging Face."""

    def __init__(self):
        self._main_downloader: HFModelDownloader | None = None
        self._encoder_downloader: HFModelDownloader | None = None
        self._lock = threading.Lock()
        self._current_phase = "main"  # "main" or "encoder"
        self._progress = DownloadProgress()
        self._listeners: list[ProgressCallback] = []

    def add_listener(self, cb: ProgressCallback) -> None:
        """Subscribe to progress updates using the shared downloader API."""
        with self._lock:
            if cb not in self._listeners:
                self._listeners.append(cb)

    def remove_listener(self, cb: ProgressCallback) -> None:
        """Unsubscribe from progress updates."""
        with self._lock:
            self._listeners = [item for item in self._listeners if item is not cb]

    @property
    def progress(self) -> DownloadProgress:
        """Return the latest XTTS download progress."""
        with self._lock:
            if self._current_phase == "main" and self._main_downloader:
                return self._main_downloader.progress
            if self._current_phase == "encoder" and self._encoder_downloader:
                return self._encoder_downloader.progress
            return copy.copy(self._progress)

    @property
    def state(self) -> DownloadState:
        """Return the current XTTS download state."""
        return self.progress.state

    def start(self) -> None:
        """Start downloading XTTS-v2 models."""
        with self._lock:
            if self._main_downloader is None:
                self._main_downloader = HFModelDownloader(model_id=XTTS_V2_REPO)
                if hasattr(self._main_downloader, "add_listener"):
                    self._main_downloader.add_listener(self._on_progress)
            self._current_phase = "main"
            self._progress = DownloadProgress(state=DownloadState.DOWNLOADING)
            downloader = self._main_downloader

        downloader.start()
        logger.info("Started XTTS-v2 main model download")

    def _on_progress(self, progress: DownloadProgress) -> None:
        progress_snapshot = copy.copy(progress)
        with self._lock:
            self._progress = progress_snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(copy.copy(progress_snapshot))
            except Exception:
                logger.debug("XTTS download listener raised", exc_info=True)

    def get_progress(self) -> tuple[DownloadProgress, str]:
        """Get current download progress and phase.

        Returns:
            Tuple of (progress, phase) where phase is "main" or "encoder"
        """
        with self._lock:
            phase = self._current_phase

            if phase == "main" and self._main_downloader:
                return (self._main_downloader.progress, phase)
            elif phase == "encoder" and self._encoder_downloader:
                return (self._encoder_downloader.progress, phase)

            return (copy.copy(self._progress), phase)

    def is_downloading(self) -> bool:
        """Check if download is in progress."""
        with self._lock:
            if self._main_downloader:
                state = self._main_downloader.state
                if state in (DownloadState.DOWNLOADING, DownloadState.PAUSED):
                    return True

            if self._encoder_downloader:
                state = self._encoder_downloader.state
                if state in (DownloadState.DOWNLOADING, DownloadState.PAUSED):
                    return True

        return False

    def is_complete(self) -> bool:
        """Check if all downloads are complete."""
        return xtts_models_ready()

    def pause(self) -> None:
        """Pause current download."""
        with self._lock:
            if self._current_phase == "main" and self._main_downloader:
                self._main_downloader.pause()
            elif self._current_phase == "encoder" and self._encoder_downloader:
                self._encoder_downloader.pause()

    def resume(self) -> None:
        """Resume paused download."""
        with self._lock:
            if self._current_phase == "main" and self._main_downloader:
                self._main_downloader.resume()
            elif self._current_phase == "encoder" and self._encoder_downloader:
                self._encoder_downloader.resume()

    def cancel(self) -> None:
        """Cancel download."""
        with self._lock:
            main_downloader = self._main_downloader
            encoder_downloader = self._encoder_downloader
            self._main_downloader = None
            self._encoder_downloader = None
            self._progress = DownloadProgress(state=DownloadState.CANCELLED)
            listeners = list(self._listeners)

        if main_downloader:
            main_downloader.cancel()
        if encoder_downloader:
            encoder_downloader.cancel()
        for listener in listeners:
            try:
                listener(copy.copy(self._progress))
            except Exception:
                logger.debug("XTTS download listener raised", exc_info=True)

    def start_encoder_download(self) -> None:
        """Start downloading speaker encoder (call after main model completes)."""
        with self._lock:
            if self._encoder_downloader is not None:
                return  # Already downloading

            self._encoder_downloader = HFModelDownloader(model_id=XTTS_SPEAKER_ENCODER_REPO)
            if hasattr(self._encoder_downloader, "add_listener"):
                self._encoder_downloader.add_listener(self._on_progress)
            self._current_phase = "encoder"
            downloader = self._encoder_downloader

        downloader.start()
        logger.info("Started XTTS speaker encoder download")


def estimate_xtts_download_size() -> int:
    """Estimate total download size in bytes.

    Returns:
        Estimated size in bytes (~2.5GB)
    """
    # Rough estimate for the XTTS-v2 files used by Coqui. The external speaker
    # encoder repo is no longer required for this runtime path.
    return 2_100_000_000
