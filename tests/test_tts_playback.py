"""Unit tests for TTS audio playback improvements."""
import pytest
import numpy as np
from unittest.mock import Mock, patch
from src.tts.manager import TTSManager


class TestTTSAudioPlayback:
    """Test TTS audio playback functionality."""

    def test_probe_supported_sample_rates(self):
        """Test sample rate probing."""
        manager = TTSManager()

        # Mock sounddevice to return specific rates as supported
        with patch('sounddevice.check_output_settings') as mock_check:
            # Simulate that only 44100 and 48000 are supported
            def check_settings(device, samplerate, channels):
                if samplerate in [44100, 48000]:
                    return
                raise Exception("Not supported")

            mock_check.side_effect = check_settings

            rates = manager._probe_supported_sample_rates(None)
            assert 44100 in rates
            assert 48000 in rates
            assert 24000 not in rates

    def test_prepare_qwen_mono_for_mixline_uses_stereo_48khz(self, monkeypatch):
        """MixLine receives Qwen's 24 kHz mono output as duplicated 48 kHz L/R."""
        manager = TTSManager()
        probes = []

        monkeypatch.setattr(
            "src.tts.manager.sd.query_devices",
            lambda _device, kind=None: {
                "name": "Speakers (MIXLINE)",
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            },
        )

        def check_settings(*, device, samplerate, channels):
            probes.append((device, samplerate, channels))
            if samplerate != 48000 or channels != 2:
                raise RuntimeError("unsupported format")

        monkeypatch.setattr(
            "src.tts.manager.sd.check_output_settings",
            check_settings,
        )

        source = np.array([0.25, -0.5, 0.75, 0.0], dtype=np.float32)
        prepared, sample_rate = manager._prepare_audio_for_device(source, 24000, 40)

        assert sample_rate == 48000
        assert prepared.shape == (8, 2)
        np.testing.assert_allclose(prepared[:, 0], prepared[:, 1])
        assert probes
        assert {channels for _device, _rate, channels in probes} == {2}

    def test_choose_best_sample_rate_exact_match(self):
        """Test choosing sample rate when exact match exists."""
        manager = TTSManager()

        supported = [16000, 24000, 44100, 48000]

        # Exact match should be returned
        assert manager._choose_best_sample_rate(24000, supported) == 24000
        assert manager._choose_best_sample_rate(48000, supported) == 48000

    def test_choose_best_sample_rate_closest(self):
        """Test choosing closest sample rate."""
        manager = TTSManager()

        supported = [16000, 44100, 48000]

        # 24000 not supported, should choose closest higher (44100)
        result = manager._choose_best_sample_rate(24000, supported)
        assert result == 44100

    def test_resample_audio(self):
        """Test audio resampling."""
        manager = TTSManager()

        # Create test audio at 24kHz
        audio_24k = np.random.rand(24000).astype(np.float32)

        # Resample to 48kHz
        resampled = manager._resample_audio(audio_24k, 24000, 48000)

        # Should be approximately 2x length
        assert len(resampled) == pytest.approx(48000, rel=0.01)
        assert resampled.dtype == np.float32

        # Values should be clipped to [-1, 1]
        assert np.all(resampled >= -1.0)
        assert np.all(resampled <= 1.0)

    def test_resample_audio_no_change(self):
        """Test resampling with same rate returns original."""
        manager = TTSManager()

        audio = np.random.rand(24000).astype(np.float32)
        resampled = manager._resample_audio(audio, 24000, 24000)

        # Should return same array
        assert len(resampled) == len(audio)
        np.testing.assert_array_equal(resampled, audio)

    def test_speak_validates_input(self):
        """Test that speak validates input text."""
        manager = TTSManager()
        manager._running = True
        manager._engine = Mock()

        # Empty text should fail
        assert not manager.speak("", "voice")
        assert not manager.speak("   ", "voice")

        # Valid text should succeed (queue)
        assert manager.speak("Hello", "voice")

    def test_device_name_matching(self):
        """Test device name fuzzy matching."""
        from src.tts.manager import _device_names_match

        # Exact match
        assert _device_names_match(
            "MixLine Input (Logitech G MixLine)",
            "MixLine Input (Logitech G MixLine)"
        )

        # Partial match
        assert _device_names_match(
            "MixLine Input (Logitech G MixLine)",
            "MixLine Input"
        )

        # Case insensitive
        assert _device_names_match(
            "MIXLINE INPUT",
            "mixline input"
        )

        # No match
        assert not _device_names_match(
            "Speakers",
            "Microphone"
        )

    def test_virtual_output_score(self):
        """Test virtual output device scoring."""
        from src.tts.manager import _virtual_output_score

        # MixLine Input should score highest
        score1 = _virtual_output_score(
            "MixLine Input (Logitech G MixLine)",
            "Windows WASAPI"
        )

        # Generic MixLine endpoint should score lower
        score2 = _virtual_output_score(
            "MixLine Output",
            "Windows WASAPI"
        )

        # Non-virtual device should score 0
        score3 = _virtual_output_score(
            "Speakers (Realtek Audio)",
            "Windows WASAPI"
        )

        assert score1 > score2
        assert score2 > score3
        assert score3 == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
