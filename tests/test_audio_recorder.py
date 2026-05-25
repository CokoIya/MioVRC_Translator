import unittest

import src.audio.recorder as recorder_module
from src.audio.recorder import AudioRecorder


class AudioRecorderTests(unittest.TestCase):
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
