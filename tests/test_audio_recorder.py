import collections
import threading
import unittest

import numpy as np

import src.audio.recorder as recorder_module
from src.audio.recorder import FRAME_QUEUE_MAXSIZE, AudioRecorder


class AudioRecorderTests(unittest.TestCase):
    def test_vad_activation_frames_count_toward_minimum_segment_duration(self):
        segments = []
        recorder = AudioRecorder(
            segments.append,
            min_segment_s=0.45,
        )

        class IdentityDenoiser:
            @staticmethod
            def process(frame, *, update_profile):
                del update_profile
                return frame

        class ActivationVAD:
            def __init__(self):
                self.calls = 0
                self.in_speech = False
                self._min_rms = 0.0
                self._activation_window = collections.deque(maxlen=6)

            def process_frame(self, _pcm):
                self.calls += 1
                if self.calls <= 6:
                    self._activation_window.append(True)
                    self.in_speech = self.calls == 6
                elif self.calls <= 15:
                    self.in_speech = True
                else:
                    self.in_speech = False
                return self.in_speech

            def reset(self):
                self.in_speech = False
                self._activation_window.clear()

        recorder._denoiser = IdentityDenoiser()
        recorder.vad = ActivationVAD()
        frame = np.full(480, 0.1, dtype=np.float32)
        for _index in range(16):
            recorder._frame_queue.put_nowait(frame.copy())
        recorder._frame_queue.put_nowait(None)

        recorder._process_loop()

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].size, 15 * frame.size)

    def _speaking_recorder(self, segments):
        recorder = AudioRecorder(segments.append, min_segment_s=0.1)

        class IdentityDenoiser:
            @staticmethod
            def process(frame, *, update_profile):
                del update_profile
                return frame

        class AlwaysSpeakingVAD:
            in_speech = False
            _min_rms = 0.0

            def process_frame(self, _pcm):
                self.in_speech = True
                return True

            def activation_speech_samples(self, frame_size):
                return frame_size

            def reset(self):
                self.in_speech = False

        recorder._denoiser = IdentityDenoiser()
        recorder.vad = AlwaysSpeakingVAD()
        frame = np.full(480, 0.1, dtype=np.float32)
        for _index in range(10):
            recorder._frame_queue.put_nowait(frame.copy())
        recorder._frame_queue.put_nowait(None)
        return recorder, frame

    def test_stop_drops_the_sentence_in_progress_by_default(self):
        segments = []
        recorder, _frame = self._speaking_recorder(segments)

        recorder._process_loop()

        self.assertEqual(segments, [])

    def test_stop_with_flush_emits_the_sentence_in_progress(self):
        segments = []
        recorder, frame = self._speaking_recorder(segments)
        recorder._flush_pending_on_stop = True

        recorder._process_loop()

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].size, 10 * frame.size)
        self.assertFalse(recorder._was_in_speech)

    def test_signal_presence_ignores_digital_zero_but_not_a_quiet_floor(self):
        recorder = AudioRecorder(lambda _segment: None)

        class Silent:
            in_speech = False
            _min_rms = 0.012

            def process_frame(self, _pcm):
                return False

            def reset(self):
                pass

        recorder.vad = Silent()
        recorder._frame_queue.put_nowait(np.zeros(480, dtype=np.int16))
        recorder._frame_queue.put_nowait(None)
        recorder._process_loop()
        self.assertEqual(recorder._last_signal_at, 0.0)

        # A quiet room (a few 16-bit steps, about 2e-4): far below the voice
        # threshold, still not digital zero.
        recorder._frame_queue.put_nowait(np.full(480, 6, dtype=np.int16))
        recorder._frame_queue.put_nowait(None)
        recorder._process_loop()
        self.assertGreater(recorder._last_signal_at, 0.0)
        self.assertEqual(recorder._last_non_silent_at, 0.0)
        self.assertIn("last_signal_at", recorder.diagnostics_snapshot())

    def test_vad_state_callback_failure_is_logged_and_processing_continues(self):
        segments = []
        vad_calls = []

        def failing_vad_state(in_speech):
            vad_calls.append(in_speech)
            raise RuntimeError("synthetic VAD state failure")

        recorder = AudioRecorder(
            segments.append,
            min_segment_s=0.1,
            on_vad_state=failing_vad_state,
        )

        class IdentityDenoiser:
            @staticmethod
            def process(frame, *, update_profile):
                del update_profile
                return frame

        class ToggleVAD:
            def __init__(self):
                self.calls = 0
                self.in_speech = False
                self._min_rms = 0.0
                self._activation_window = collections.deque(maxlen=6)

            def process_frame(self, _pcm):
                self.calls += 1
                self.in_speech = self.calls <= 15
                return self.in_speech

            def reset(self):
                self.in_speech = False
                self._activation_window.clear()

        recorder._denoiser = IdentityDenoiser()
        recorder.vad = ToggleVAD()
        frame = np.full(480, 0.1, dtype=np.float32)
        for _index in range(16):
            recorder._frame_queue.put_nowait(frame.copy())
        recorder._frame_queue.put_nowait(None)

        with self.assertLogs(recorder_module.logger, level="ERROR") as logs:
            recorder._process_loop()

        self.assertEqual(vad_calls[:2], [True, False])
        self.assertEqual(len(segments), 1)
        self.assertTrue(
            any("on_vad_state callback failed" in line for line in logs.output)
        )

    def test_stateful_soxr_resampler_reblocks_exact_vad_frames(self):
        if not recorder_module._HAS_SOXR:
            self.skipTest("soxr is unavailable")
        recorder = AudioRecorder(lambda _audio: None)
        recorder._capture_dtype = "float32"
        recorder._capture_rate = 48000
        phase = np.arange(1440, dtype=np.float32)
        frame = np.sin(phase * (2.0 * np.pi * 440.0 / 48000.0)).astype(np.float32)

        outputs = [recorder._prepare_frame(frame) for _ in range(20)]
        emitted = [output for output in outputs if output.size]

        self.assertGreaterEqual(len(emitted), 18)
        self.assertTrue(all(output.shape == (480,) for output in emitted))
        self.assertIsNotNone(recorder._resample_stream)
        stream = recorder._resample_stream
        recorder._prepare_frame(frame)
        self.assertIs(recorder._resample_stream, stream)

    def test_streaming_resampler_resets_when_capture_rate_changes(self):
        if not recorder_module._HAS_SOXR:
            self.skipTest("soxr is unavailable")
        recorder = AudioRecorder(lambda _audio: None)
        recorder._capture_dtype = "float32"
        recorder._capture_rate = 48000
        recorder._prepare_frame(np.zeros(1440, dtype=np.float32))
        first_stream = recorder._resample_stream

        recorder._capture_rate = 44100
        recorder._prepare_frame(np.zeros(1323, dtype=np.float32))

        self.assertIsNotNone(first_stream)
        self.assertIsNot(recorder._resample_stream, first_stream)

    def test_diagnostics_snapshot_includes_vad_meter_fields(self):
        recorder = AudioRecorder(lambda _audio: None)
        snapshot = recorder.diagnostics_snapshot()

        self.assertIn("vad_in_speech", snapshot)
        self.assertIn("vad_speech_ratio", snapshot)
        self.assertIn("vad_activation_ratio", snapshot)
        self.assertEqual(snapshot["vad_min_rms"], 0.012)

    def test_start_failure_resets_running_state(self):
        recorder = AudioRecorder(lambda _audio: None)

        def fail_open(*_args, **_kwargs):
            raise RuntimeError("boom")

        recorder._open_stream = fail_open

        with self.assertRaisesRegex(RuntimeError, "boom"):
            recorder.start()

        self.assertFalse(recorder.is_running)
        self.assertIsNone(recorder._stream)

    def test_start_reset_failure_does_not_strand_running_state_and_can_retry(self):
        recorder = AudioRecorder(lambda _audio: None)

        class FakeStream:
            def __init__(self):
                self.aborted = False
                self.closed = False

            def abort(self):
                self.aborted = True

            def close(self):
                self.closed = True

        original_reset = recorder.vad.reset
        reset_calls = 0

        def fail_first_reset():
            nonlocal reset_calls
            reset_calls += 1
            if reset_calls == 1:
                raise RuntimeError("synthetic reset failure")
            original_reset()

        stream = FakeStream()
        recorder.vad.reset = fail_first_reset
        recorder._open_stream = lambda *_args, **_kwargs: stream

        with self.assertRaisesRegex(RuntimeError, "synthetic reset failure"):
            recorder.start()

        self.assertFalse(recorder.is_running)
        self.assertFalse(recorder.worker_alive)
        self.assertIsNone(recorder._stream)
        self.assertIsNone(recorder._worker_thread)

        recorder.start()
        self.assertTrue(recorder.is_running)
        self.assertTrue(recorder.worker_alive)
        recorder.stop()

        self.assertFalse(recorder.is_running)
        self.assertFalse(recorder.worker_alive)
        self.assertTrue(stream.aborted)
        self.assertTrue(stream.closed)

    def test_worker_failure_stops_capture_and_discards_partial_audio(self):
        vad_states = []
        recorder = AudioRecorder(
            lambda _audio: None,
            on_vad_state=vad_states.append,
        )

        class FakeStream:
            def __init__(self):
                self.aborted = False
                self.closed = False

            def abort(self):
                self.aborted = True

            def close(self):
                self.closed = True

        stream = FakeStream()
        recorder._stream = stream
        recorder._active_device_name = "Failure Mic"
        recorder._running = True
        recorder._buffer.append(np.ones(480, dtype=np.float32))
        recorder._pre_speech_buffer.append(np.ones(480, dtype=np.float32))
        recorder._speech_samples = 480
        recorder._was_in_speech = True
        recorder._enqueue_frame(np.ones(480, dtype=np.float32))

        def fail_processing():
            raise RuntimeError("synthetic VAD failure")

        recorder._process_loop = fail_processing
        worker = threading.Thread(target=recorder._worker_main, daemon=True)
        recorder._worker_thread = worker
        worker.start()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertFalse(recorder.is_running)
        self.assertTrue(stream.aborted)
        self.assertTrue(stream.closed)
        self.assertIsNone(recorder._stream)
        self.assertIsNone(recorder.active_input_device_name)
        self.assertEqual(recorder._buffer, [])
        self.assertEqual(list(recorder._pre_speech_buffer), [])
        self.assertEqual(recorder._speech_samples, 0)
        self.assertFalse(recorder._was_in_speech)
        self.assertEqual(vad_states, [False])

        snapshot = recorder.diagnostics_snapshot()
        self.assertFalse(snapshot["worker_alive"])
        self.assertFalse(snapshot["stream_open"])
        self.assertEqual(snapshot["frame_queue_size"], 0)
        self.assertEqual(snapshot["stale_frames_discarded"], 1)
        self.assertEqual(snapshot["worker_failure_count"], 1)
        self.assertGreater(snapshot["last_worker_error_at"], 0.0)
        self.assertIn("RuntimeError: synthetic VAD failure", snapshot["last_worker_error"])

    def test_recorder_can_restart_after_worker_failure_and_repeated_stops(self):
        recorder = AudioRecorder(lambda _audio: None)
        streams = []

        class FakeStream:
            def __init__(self):
                self.aborted = False
                self.closed = False

            def abort(self):
                self.aborted = True

            def close(self):
                self.closed = True

        def open_stream(*_args, **_kwargs):
            stream = FakeStream()
            streams.append(stream)
            return stream

        original_process_loop = recorder._process_loop
        fail_first_worker = True

        def process_loop():
            nonlocal fail_first_worker
            if fail_first_worker:
                fail_first_worker = False
                raise RuntimeError("fail once")
            original_process_loop()

        recorder._open_stream = open_stream
        recorder._process_loop = process_loop

        recorder.start()
        failed_worker = recorder._worker_thread
        self.assertIsNotNone(failed_worker)
        failed_worker.join(timeout=2)
        self.assertFalse(recorder.is_running)
        self.assertIn("fail once", recorder.last_worker_error)

        for _index in range(3):
            recorder.start()
            self.assertTrue(recorder.is_running)
            self.assertTrue(recorder.worker_alive)
            self.assertIsNone(recorder.last_worker_error)
            recorder.stop()
            self.assertFalse(recorder.is_running)
            self.assertFalse(recorder.worker_alive)

        self.assertEqual(len(streams), 4)
        self.assertTrue(all(stream.aborted for stream in streams))
        self.assertTrue(all(stream.closed for stream in streams))

    def test_frame_queue_saturation_drops_oldest_without_blocking(self):
        recorder = AudioRecorder(lambda _audio: None)
        overflow = 7

        for index in range(FRAME_QUEUE_MAXSIZE + overflow):
            recorder._enqueue_frame(np.asarray([index], dtype=np.float32))

        snapshot = recorder.diagnostics_snapshot()
        self.assertEqual(snapshot["frame_queue_size"], FRAME_QUEUE_MAXSIZE)
        self.assertEqual(snapshot["frame_queue_capacity"], FRAME_QUEUE_MAXSIZE)
        self.assertEqual(snapshot["frame_queue_high_watermark"], FRAME_QUEUE_MAXSIZE)
        self.assertEqual(snapshot["frame_queue_dropped"], overflow)

        retained = []
        while not recorder._frame_queue.empty():
            retained.append(int(recorder._frame_queue.get_nowait()[0]))
        self.assertEqual(retained[0], overflow)
        self.assertEqual(retained[-1], FRAME_QUEUE_MAXSIZE + overflow - 1)

    def test_fixed_device_does_not_fall_back_to_default(self):
        original_sd = recorder_module.sd

        class FakePortAudioError(Exception):
            pass

        class FakeDefault:
            device = [2, -1]

        class FakeInputStream:
            def __init__(self, *, device=None, **_kwargs):
                self.device = device

            def start(self):
                if self.device == 1:
                    raise FakePortAudioError("requested device unavailable")

            def close(self):
                pass

        class FakeSoundDevice:
            PortAudioError = FakePortAudioError
            InputStream = FakeInputStream
            default = FakeDefault()

            @staticmethod
            def query_devices(index):
                names = {
                    1: "Requested Mic",
                    2: "Default Mic",
                }
                return {
                    "name": names.get(index, "Unknown"),
                    "default_samplerate": 16000,
                    "max_input_channels": 1,
                    "max_output_channels": 0,
                }

        recorder_module.sd = FakeSoundDevice
        try:
            recorder = AudioRecorder(
                lambda _audio: None,
                input_device=1,
                allow_default_fallback=False,
            )
            with self.assertRaises(FakePortAudioError):
                recorder._open_stream(recorder.input_device, None)
            self.assertEqual(recorder.input_device, 1)

            fallback_recorder = AudioRecorder(
                lambda _audio: None,
                input_device=1,
                allow_default_fallback=True,
            )
            stream = fallback_recorder._open_stream(fallback_recorder.input_device, None)
            self.assertIsNone(fallback_recorder.input_device)
            self.assertEqual(fallback_recorder.active_input_device_name, "Default Mic")
            stream.close()
        finally:
            recorder_module.sd = original_sd

    def test_invalid_sample_rate_falls_back_to_supported_rate(self):
        original_sd = recorder_module.sd

        class FakePortAudioError(Exception):
            pass

        class FakeDefault:
            device = [1, -1]

        class FakeInputStream:
            opened_rates = []

            def __init__(self, *, samplerate=None, device=None, **_kwargs):
                self.samplerate = samplerate
                self.device = device
                self.opened_rates.append(samplerate)
                if samplerate != 48000:
                    raise ValueError("Error opening InputStream: Invalid sample rate [PaErrorCode -9997]")

            def start(self):
                pass

            def close(self):
                pass

        class FakeSoundDevice:
            PortAudioError = FakePortAudioError
            InputStream = FakeInputStream
            default = FakeDefault()

            @staticmethod
            def query_devices(index):
                return {
                    "name": "Strict 48k Mic",
                    "default_samplerate": 16000,
                    "max_input_channels": 1,
                    "max_output_channels": 0,
                }

        recorder_module.sd = FakeSoundDevice
        try:
            recorder = AudioRecorder(
                lambda _audio: None,
                input_device=1,
                sample_rate=16000,
                allow_default_fallback=False,
            )
            stream = recorder._open_stream(recorder.input_device, None)

            self.assertEqual(recorder._capture_rate, 48000)
            self.assertEqual(recorder.sample_rate, 16000)
            self.assertIn(16000, FakeInputStream.opened_rates)
            self.assertEqual(FakeInputStream.opened_rates[-1], 48000)
            stream.close()
        finally:
            recorder_module.sd = original_sd

    def test_fixed_device_tries_same_named_host_api_fallback(self):
        original_sd = recorder_module.sd

        class FakeDefault:
            device = [4, -1]

        class FakeInputStream:
            opened_configs = []

            def __init__(self, *, samplerate=None, device=None, **_kwargs):
                self.samplerate = samplerate
                self.device = device
                self.opened_configs.append((device, samplerate))
                if device != 3 or samplerate != 44100:
                    raise ValueError("Error opening InputStream: Invalid sample rate [PaErrorCode -9997]")

            def start(self):
                pass

            def close(self):
                pass

        class FakeSoundDevice:
            InputStream = FakeInputStream
            default = FakeDefault()
            _devices = [
                {"name": "Other Mic", "default_samplerate": 16000, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
                {"name": "Strict Mic", "default_samplerate": 48000, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
                {"name": "Strict Mic Output", "default_samplerate": 48000, "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
                {"name": "Strict Mic", "default_samplerate": 44100, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 2},
                {"name": "Default Mic", "default_samplerate": 16000, "max_input_channels": 1, "max_output_channels": 0, "hostapi": 0},
            ]

            @staticmethod
            def query_devices(index=None):
                if index is None:
                    return FakeSoundDevice._devices
                return FakeSoundDevice._devices[int(index)]

            @staticmethod
            def query_hostapis():
                return [
                    {"name": "Windows WASAPI"},
                    {"name": "Windows WDM-KS"},
                    {"name": "MME"},
                ]

        recorder_module.sd = FakeSoundDevice
        try:
            recorder = AudioRecorder(
                lambda _audio: None,
                input_device=1,
                sample_rate=16000,
                allow_default_fallback=False,
            )
            stream = recorder._open_stream(recorder.input_device, None)

            self.assertEqual(recorder.input_device, 3)
            self.assertEqual(recorder.active_input_device_name, "Strict Mic")
            self.assertIn((1, 48000), FakeInputStream.opened_configs)
            self.assertEqual(FakeInputStream.opened_configs[-1], (3, 44100))
            stream.close()
        finally:
            recorder_module.sd = original_sd


if __name__ == "__main__":
    unittest.main()
