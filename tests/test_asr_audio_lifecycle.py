from __future__ import annotations

import gc
import hashlib
import sys
import threading
import time
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

from src.asr import model_manager
from src.utils import hf_model_downloader
from src.asr.base import ASRProvider
from src.asr.fallback_asr import FallbackASR
from src.utils.hf_model_downloader import HFModelDownloader
from src.asr.qwen3_asr import Qwen3ASRProvider
from src.asr.sensevoice_asr import SenseVoiceASR
from src.audio.desktop_recorder import DesktopAudioRecorder
from src.audio.recorder import AudioRecorder
from src.audio.vad_detector import SileroVADDetector


class _DummyASR(ASRProvider):
    provider_id = "dummy"

    def __init__(self) -> None:
        self.close_count = 0

    def transcribe(self, audio, sample_rate=16000, language=None, is_final=True):
        del audio, sample_rate, language, is_final
        return ""

    def close(self) -> None:
        self.close_count += 1


@pytest.mark.parametrize("provider_type", [SenseVoiceASR])
def test_local_asr_close_releases_model_and_cuda_cache(
    monkeypatch,
    provider_type,
):
    close_calls: list[bool] = []
    empty_cache_calls: list[bool] = []

    class Model:
        def close(self) -> None:
            close_calls.append(True)

    model = Model()
    model_ref = weakref.ref(model)
    provider = provider_type(device="cuda")
    provider._model = model
    provider._corrector = object()
    del model
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                empty_cache=lambda: empty_cache_calls.append(True),
            )
        ),
    )

    provider.close()
    provider.close()

    assert provider.is_loaded is False
    assert provider._model is None
    assert provider._corrector is None
    assert model_ref() is None
    assert close_calls == [True]
    assert empty_cache_calls == [True]
    with pytest.raises(RuntimeError, match="closed"):
        provider.load()


def test_qwen_close_closes_http_client_and_is_idempotent():
    closed: list[bool] = []

    class Client:
        def close(self) -> None:
            closed.append(True)

    provider = Qwen3ASRProvider(
        {"asr": {"qwen3_asr": {"api_key": "test-key"}}},
        corrector=object(),
    )
    provider._client = Client()

    provider.close()
    provider.close()

    assert closed == [True]
    assert provider.is_loaded is False
    assert provider._client is None
    assert provider._corrector is None




def test_fallback_close_releases_lazy_factory_closure_without_constructing_it():
    class Sentinel:
        pass

    sentinel = Sentinel()
    sentinel_ref = weakref.ref(sentinel)
    primary = _DummyASR()
    created: list[_DummyASR] = []
    provider = FallbackASR(
        primary,
        fallback_factory=lambda retained=sentinel: created.append(_DummyASR()) or created[-1],
    )
    del sentinel

    provider.close()
    provider.close()
    gc.collect()

    assert primary.close_count == 1
    assert created == []
    assert provider._fallback_factory is None
    assert sentinel_ref() is None
    assert provider.is_loaded is False


def test_shared_model_downloader_does_not_retain_bound_listener_owner():
    calls: list[bool] = []

    class Owner:
        def on_progress(self, _progress) -> None:
            calls.append(True)

    downloader = HFModelDownloader("owner/model")
    owner = Owner()
    owner_ref = weakref.ref(owner)
    downloader.add_listener(owner.on_progress)
    del owner
    gc.collect()

    assert owner_ref() is None
    downloader._emit(force=True)
    assert downloader._listeners == []
    assert calls == []


def test_model_hash_cache_is_lru_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(model_manager, "_FILE_HASH_CACHE_MAX_ENTRIES", 3)
    model_manager._FILE_HASH_CACHE.clear()

    for index in range(10):
        path = tmp_path / f"model-{index}.bin"
        path.write_bytes(f"payload-{index}".encode())
        assert model_manager._sha256_file(path)

    assert len(model_manager._FILE_HASH_CACHE) == 3
    assert all(
        key[0].endswith(("model-7.bin", "model-8.bin", "model-9.bin"))
        for key in model_manager._FILE_HASH_CACHE
    )


def test_verified_download_cache_is_lru_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hf_model_downloader,
        "_VERIFIED_FILE_CACHE_MAX_ENTRIES",
        3,
    )
    hf_model_downloader._VERIFIED_FILE_CACHE.clear()

    for index in range(10):
        payload = f"verified-{index}".encode()
        path = tmp_path / f"verified-{index}.bin"
        path.write_bytes(payload)
        assert hf_model_downloader._secure_file_matches_sha256(
            path,
            hashlib.sha256(payload).hexdigest(),
        )

    assert len(hf_model_downloader._VERIFIED_FILE_CACHE) == 3
    assert all(
        key[0].endswith(
            ("verified-7.bin", "verified-8.bin", "verified-9.bin")
        )
        for key in hf_model_downloader._VERIFIED_FILE_CACHE
    )


def test_process_downloader_registry_does_not_own_idle_downloaders():
    hf_model_downloader._downloaders.clear()
    downloader = hf_model_downloader.get_downloader("owner/ephemeral-model")
    downloader_ref = weakref.ref(downloader)
    del downloader
    gc.collect()

    assert downloader_ref() is None
    assert "owner/ephemeral-model" not in hf_model_downloader._downloaders






def test_repeated_audio_device_restarts_leave_no_worker_threads_or_recorders(
    monkeypatch,
):
    class Stream:
        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        AudioRecorder,
        "_open_stream",
        lambda self, device, extra_settings=None: Stream(),
    )
    baseline = sum(
        thread.name == "audio-recorder-worker" and thread.is_alive()
        for thread in threading.enumerate()
    )
    recorder_refs: list[weakref.ReferenceType[AudioRecorder]] = []

    for _ in range(25):
        recorder = AudioRecorder(lambda _audio: None)
        recorder.start()
        recorder._buffer.append(np.ones(16_000, dtype=np.float32))
        recorder._pre_speech_buffer.append(np.ones(480, dtype=np.float32))
        recorder._resample_output_buffer = np.ones(8_000, dtype=np.float32)
        recorder.stop()
        assert recorder._worker_thread is None
        assert recorder._buffer == []
        assert len(recorder._pre_speech_buffer) == 0
        assert recorder._resample_output_buffer.size == 0
        recorder_refs.append(weakref.ref(recorder))
        del recorder

    gc.collect()
    remaining = sum(
        thread.name == "audio-recorder-worker" and thread.is_alive()
        for thread in threading.enumerate()
    )
    assert remaining == baseline
    assert all(reference() is None for reference in recorder_refs)


def test_audio_stop_retains_a_worker_that_has_not_exited():
    class LiveThread:
        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def join(self, timeout=None) -> None:
            self.join_calls.append(timeout)

        def is_alive(self) -> bool:
            return True

    recorder = AudioRecorder(lambda _audio: None)
    thread = LiveThread()
    recorder._worker_thread = thread
    recorder._running = True

    recorder.stop()

    assert recorder._worker_thread is thread
    assert thread.join_calls == [2]
    with pytest.raises(RuntimeError, match="still stopping"):
        recorder.start()


def test_audio_stop_aborts_native_input_stream_before_close():
    calls: list[str] = []

    class Stream:
        def abort(self) -> None:
            calls.append("abort")

        def stop(self) -> None:
            calls.append("stop")

        def close(self) -> None:
            calls.append("close")

    recorder = AudioRecorder(lambda _audio: None)
    recorder._stream = Stream()
    recorder._running = True

    recorder.stop()

    assert calls == ["abort", "close"]


def test_repeated_desktop_device_restarts_leave_no_capture_threads_or_recorders(
    monkeypatch,
):
    def capture_loop(recorder: DesktopAudioRecorder) -> None:
        recorder._active_device_name = "Fake Output"
        recorder._loopback_device_info = {
            "name": "Fake Output",
            "backend": "test",
        }
        recorder._stream_config = {
            "rate": 16_000,
            "channels": 1,
            "blocksize": 480,
        }
        if recorder._capture_ready_event is not None:
            recorder._capture_ready_event.set()
        while recorder._running:
            time.sleep(0.001)
        recorder._enqueue_frame(None)

    monkeypatch.setattr(DesktopAudioRecorder, "_capture_loop", capture_loop)
    thread_names = {"desktop-audio-worker", "desktop-audio-capture"}
    baseline = {
        name: sum(thread.name == name and thread.is_alive() for thread in threading.enumerate())
        for name in thread_names
    }
    recorder_refs: list[weakref.ReferenceType[DesktopAudioRecorder]] = []

    for _ in range(20):
        recorder = DesktopAudioRecorder(lambda _audio: None, vad_type="webrtc")
        recorder.start()
        recorder.stop()
        recorder_refs.append(weakref.ref(recorder))
        del recorder

    gc.collect()
    remaining = {
        name: sum(thread.name == name and thread.is_alive() for thread in threading.enumerate())
        for name in thread_names
    }
    assert remaining == baseline
    assert all(reference() is None for reference in recorder_refs)


def test_desktop_stop_never_terminates_pyaudio_while_capture_thread_is_alive():
    class LiveThread:
        def __init__(self) -> None:
            self.join_calls: list[float] = []

        def join(self, timeout=None) -> None:
            self.join_calls.append(timeout)

        def is_alive(self) -> bool:
            return True

    class Stream:
        def __init__(self) -> None:
            self.closed = False

        def stop_stream(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class PyAudio:
        def __init__(self) -> None:
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

    recorder = DesktopAudioRecorder(lambda _audio: None, vad_type="webrtc")
    capture_thread = LiveThread()
    stream = Stream()
    pyaudio = PyAudio()
    recorder._capture_thread = capture_thread
    recorder._capture_stream = stream
    recorder._pyaudio_instance = pyaudio
    recorder._running = True

    recorder.stop()

    assert capture_thread.join_calls == [2, 1]
    assert recorder._capture_thread is capture_thread
    assert stream.closed is True
    assert pyaudio.terminate_calls == 0
    assert recorder._pyaudio_instance is pyaudio


def test_silero_close_releases_per_recorder_model():
    closed: list[bool] = []

    class Model:
        def reset_states(self) -> None:
            return None

        def close(self) -> None:
            closed.append(True)

    detector = SileroVADDetector()
    detector._model = Model()

    detector.close()

    assert detector._model is None
    assert detector._model_error is None
    assert detector._fallback_vad is None
    assert detector._sample_buffer.size == 0
    assert closed == [True]
