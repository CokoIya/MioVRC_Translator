import collections
import unittest

import numpy as np

import src.audio.recorder as recorder_module
from src.audio.recorder import AudioRecorder


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
