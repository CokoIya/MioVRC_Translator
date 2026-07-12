from __future__ import annotations

import builtins
import hashlib

import pytest

from src.audio.desktop_recorder import DesktopAudioRecorder
from src.audio.vad_detector import SileroVADDetector, VADDetector
from tools import ensure_silero_vad


@pytest.mark.parametrize("sample_rate", [0, 11025, 44100])
def test_webrtc_vad_rejects_unsupported_sample_rates(sample_rate):
    with pytest.raises(ValueError, match="sample_rate"):
        VADDetector(sample_rate=sample_rate, use_envelope_follower=False)


@pytest.mark.parametrize("frame_duration_ms", [0, 15, 40])
def test_webrtc_vad_rejects_unsupported_frame_durations(frame_duration_ms):
    with pytest.raises(ValueError, match="frame_duration_ms"):
        VADDetector(
            frame_duration_ms=frame_duration_ms,
            use_envelope_follower=False,
        )


@pytest.mark.parametrize("sensitivity", [-1, 4])
def test_webrtc_vad_rejects_invalid_sensitivity(sensitivity):
    with pytest.raises(ValueError, match="sensitivity"):
        VADDetector(sensitivity=sensitivity, use_envelope_follower=False)


def test_missing_silero_model_never_imports_torch_or_runs_remote_code(
    monkeypatch,
    tmp_path,
):
    missing = tmp_path / "missing-silero.jit"
    monkeypatch.setattr("src.audio.vad_detector._SILERO_LOCAL_JIT", str(missing))
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "torch":
            raise AssertionError("torch must not be imported for a missing model")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    detector = SileroVADDetector(use_envelope_follower=False)

    with pytest.raises(RuntimeError, match="missing or failed integrity"):
        detector._get_model()


def test_silero_prewarm_activates_webrtc_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.audio.vad_detector._SILERO_LOCAL_JIT",
        str(tmp_path / "missing.jit"),
    )
    detector = SileroVADDetector(use_envelope_follower=False)

    detector.prewarm()

    assert isinstance(detector._fallback_vad, VADDetector)
    assert detector.process_frame(bytes(960)) is False


def test_desktop_recorder_uses_webrtc_when_pinned_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "src.audio.desktop_recorder.silero_vad_model_available",
        lambda: False,
    )

    recorder = DesktopAudioRecorder(
        on_segment=lambda _audio: None,
        vad_type="silero",
    )

    assert type(recorder.vad) is VADDetector


def test_release_model_installer_verifies_before_atomic_install(monkeypatch, tmp_path):
    payload = b"verified-silero-model"
    destination = tmp_path / "models" / "silero_vad.jit"
    monkeypatch.setattr(ensure_silero_vad, "SILERO_VAD_SIZE", len(payload))
    monkeypatch.setattr(
        ensure_silero_vad,
        "SILERO_VAD_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )

    class Response:
        headers = {"Content-Length": str(len(payload))}

        def read(self, _limit):
            return payload

        def close(self):
            pass

    class ResponseContext:
        def __enter__(self):
            return Response()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        ensure_silero_vad,
        "open_trusted_https_url",
        lambda *_args, **_kwargs: ResponseContext(),
    )

    installed = ensure_silero_vad.ensure_model(destination)

    assert installed == destination
    assert destination.read_bytes() == payload
    assert ensure_silero_vad.verify_model(destination)
