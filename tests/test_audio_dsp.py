from __future__ import annotations

import numpy as np
import pytest

from src.audio.adaptive_denoiser import AdaptiveDenoiser
from src.audio.chunk_streamer import ChunkStreamer
from src.audio.envelope_follower import EnvelopeFollower

FRAME = 480
SAMPLE_RATE = 16000


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio))))


# ---- ChunkStreamer ---------------------------------------------------------
# sample_rate=1000 keeps the arithmetic readable: interval 100 samples,
# window 500, ring buffer 1000, speech hold 200, warm-up 0.6 * 500 = 300.


def _streamer() -> ChunkStreamer:
    return ChunkStreamer(
        sample_rate=1000,
        chunk_interval_ms=100,
        chunk_window_s=0.5,
        ring_buffer_s=1.0,
        recent_speech_hold_s=0.2,
    )


def _frame(value: float, size: int = 50) -> np.ndarray:
    return np.full(size, value, dtype=np.float32)


def test_chunk_streamer_emits_first_chunk_after_short_warmup():
    streamer = _streamer()

    for index in range(5):
        assert streamer.push_frame(_frame(index), True) == []
    chunks = streamer.push_frame(_frame(5), True)

    assert len(chunks) == 1
    expected = np.concatenate([_frame(index) for index in range(6)])
    np.testing.assert_array_equal(chunks[0], expected)


def test_chunk_streamer_follows_interval_cadence_with_latest_audio():
    streamer = _streamer()
    for index in range(6):
        streamer.push_frame(_frame(index), True)

    assert streamer.push_frame(_frame(6), True) == []
    chunks = streamer.push_frame(_frame(7), True)

    assert len(chunks) == 1
    expected = np.concatenate([_frame(index) for index in range(8)])
    np.testing.assert_array_equal(chunks[0], expected)


def test_chunk_streamer_catches_up_one_chunk_per_elapsed_interval():
    streamer = _streamer()
    for index in range(6):
        streamer.push_frame(_frame(index), True)

    chunks = streamer.push_frame(_frame(9, size=250), True)

    assert len(chunks) == 2
    assert all(chunk[-1] == 9 for chunk in chunks)


def test_chunk_streamer_never_exceeds_window_while_ring_buffer_trims():
    streamer = _streamer()
    emitted = []
    for index in range(40):
        emitted.extend(streamer.push_frame(_frame(index), True))

    assert emitted
    assert max(chunk.size for chunk in emitted) == 500
    np.testing.assert_array_equal(
        emitted[-1],
        np.concatenate([_frame(index) for index in range(30, 40)]),
    )


def test_chunk_streamer_stops_after_speech_hold_and_rewarms_immediately():
    streamer = _streamer()
    for index in range(8):
        streamer.push_frame(_frame(index), True)

    # Silence inside the 200-sample hold keeps the cadence going.
    held = []
    for _ in range(4):
        held.extend(streamer.push_frame(_frame(0.0), False))
    assert held
    # Past the hold the streamer goes quiet.
    assert streamer.push_frame(_frame(0.0), False) == []
    assert streamer.push_frame(_frame(0.0), False) == []

    # The buffer already holds more than the warm-up, so speech resuming
    # emits a fresh chunk on its very first frame.
    chunks = streamer.push_frame(_frame(42), True)
    assert len(chunks) == 1
    assert chunks[0][-1] == 42


def test_chunk_streamer_reset_requires_a_new_warmup():
    streamer = _streamer()
    for index in range(10):
        streamer.push_frame(_frame(index), True)

    streamer.reset()

    for index in range(5):
        assert streamer.push_frame(_frame(index), True) == []
    assert len(streamer.push_frame(_frame(5), True)) == 1


def test_chunk_streamer_ignores_empty_frames():
    streamer = _streamer()
    assert streamer.push_frame(np.array([], dtype=np.float32), True) == []


# ---- AdaptiveDenoiser ------------------------------------------------------


def _noise(rng: np.random.Generator, level: float = 0.01) -> np.ndarray:
    return (rng.standard_normal(FRAME) * level).astype(np.float32)


def _tone(amplitude: float = 0.3, frequency: float = 440.0) -> np.ndarray:
    phase = np.arange(FRAME, dtype=np.float32) * (2.0 * np.pi * frequency / SAMPLE_RATE)
    return (np.sin(phase) * amplitude).astype(np.float32)


def _learn_noise(denoiser: AdaptiveDenoiser, rng: np.random.Generator) -> None:
    for _ in range(12):
        denoiser.process(_noise(rng), update_profile=True)


def test_denoiser_at_zero_strength_passes_audio_through():
    rng = np.random.default_rng(0)
    denoiser = AdaptiveDenoiser(strength=0.0)
    frame = _noise(rng) + _tone()

    np.testing.assert_array_equal(denoiser.process(frame, update_profile=True), frame)


def test_denoiser_returns_input_until_a_profile_is_learned():
    rng = np.random.default_rng(1)
    denoiser = AdaptiveDenoiser(strength=0.8)
    frame = _noise(rng)

    np.testing.assert_array_equal(denoiser.process(frame, update_profile=False), frame)


def test_denoiser_suppresses_learned_stationary_noise():
    rng = np.random.default_rng(2)
    denoiser = AdaptiveDenoiser(strength=0.8)
    _learn_noise(denoiser, rng)

    frame = _noise(rng)
    output = denoiser.process(frame, update_profile=False)

    assert output.dtype == np.float32
    assert _rms(output) < 0.2 * _rms(frame)


def test_denoiser_keeps_speech_well_above_the_noise_floor():
    rng = np.random.default_rng(3)
    denoiser = AdaptiveDenoiser(strength=0.8)
    _learn_noise(denoiser, rng)

    frame = _tone() + _noise(rng)
    output = denoiser.process(frame, update_profile=False)

    assert _rms(output) > 0.8 * _rms(frame)


def test_denoiser_does_not_learn_loud_frames_as_noise():
    rng = np.random.default_rng(4)
    denoiser = AdaptiveDenoiser(strength=0.8)
    _learn_noise(denoiser, rng)

    # Loud frames offered for profiling must not be absorbed; if they were,
    # the next noise frame would come back louder instead of suppressed.
    for _ in range(20):
        denoiser.process(_tone() + _noise(rng), update_profile=True)

    frame = _noise(rng)
    assert _rms(denoiser.process(frame, update_profile=False)) < 0.2 * _rms(frame)


def test_denoiser_set_strength_discards_the_learned_profile():
    rng = np.random.default_rng(5)
    denoiser = AdaptiveDenoiser(strength=0.8)
    _learn_noise(denoiser, rng)

    denoiser.set_strength(0.8)
    frame = _noise(rng)

    np.testing.assert_array_equal(denoiser.process(frame, update_profile=False), frame)


def test_denoiser_output_is_clipped_to_unit_range():
    denoiser = AdaptiveDenoiser(strength=1.0)
    rng = np.random.default_rng(6)
    _learn_noise(denoiser, rng)

    output = denoiser.process(_tone(amplitude=3.0), update_profile=False)

    assert output.max() <= 1.0
    assert output.min() >= -1.0


def test_denoiser_handles_empty_frames():
    denoiser = AdaptiveDenoiser(strength=0.8)
    assert denoiser.process(np.array([], dtype=np.float32), update_profile=True).size == 0


# ---- EnvelopeFollower ------------------------------------------------------


def test_envelope_attacks_faster_than_it_releases():
    follower = EnvelopeFollower(attack_rate=0.6, release_rate=0.12)

    assert follower.process(1.0) == pytest.approx(0.6)
    assert follower.process(0.0) == pytest.approx(0.6 - 0.6 * 0.12)


def test_envelope_converges_to_a_steady_level():
    follower = EnvelopeFollower()
    for _ in range(80):
        follower.process(0.5)

    assert follower.envelope == pytest.approx(0.5, abs=1e-4)


def test_envelope_applies_input_gain():
    follower = EnvelopeFollower(attack_rate=0.6, gain=2.0)

    assert follower.process(0.25) == pytest.approx(0.3)


def test_envelope_rate_setters_clamp_to_unit_range():
    follower = EnvelopeFollower()
    follower.set_attack_rate(5.0)
    follower.set_release_rate(-1.0)

    assert follower.process(0.4) == pytest.approx(0.4)
    # A zero release rate holds the envelope when the level drops.
    assert follower.process(0.0) == pytest.approx(0.4)


def test_envelope_reset_returns_to_silence():
    follower = EnvelopeFollower()
    follower.process(0.9)
    follower.reset()

    assert follower.envelope == 0.0
