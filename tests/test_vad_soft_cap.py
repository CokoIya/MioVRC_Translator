"""A long sentence ends at the next breath, not at a hard length cut.

The target length used to be a hard cut: a player speaking one long
sentence heard it come back as several pieces, split mid-word. Past the
target the segment now ends at the next short pause, and only a sentence
with no pause at all is cut, at 2.5 times the target.
"""

from __future__ import annotations

from src.audio.vad_detector import (
    HARD_MAX_SPEECH_FACTOR,
    SileroVADDetector,
    VADDetector,
    hard_max_speech_seconds,
)


def _silero() -> SileroVADDetector:
    # 30 ms frames: target 0.9 s = 30 frames, breath 0.2 s = 6 frames,
    # hard limit 2.25 s = 75 frames, normal end-of-sentence silence 0.6 s = 20.
    return SileroVADDetector(
        max_speech_s=0.9, silence_threshold_s=0.6, activation_threshold_s=0.06, frame_duration_ms=30
    )


def _start_speech(detector) -> None:
    for _ in range(3):
        detector._update_state(True)
    assert detector.in_speech


class TestSileroSoftCap:
    def test_continuous_speech_runs_past_the_target_until_the_hard_limit(self):
        detector = _silero()
        _start_speech(detector)

        for _ in range(40):  # well past the 30-frame target
            detector._update_state(True)
        assert detector.in_speech

        frames = 0
        while detector.in_speech:
            detector._update_state(True)
            frames += 1
        # Ended by the hard limit (75 frames in all), not by the target.
        assert 30 < frames < 40
        assert detector._speech_frames == 0  # reset on finish

    def test_a_breath_after_the_target_ends_the_sentence_whole(self):
        detector = _silero()
        _start_speech(detector)
        for _ in range(32):
            detector._update_state(True)
        assert detector.in_speech

        for _ in range(5):
            detector._update_state(False)
        assert detector.in_speech  # a 0.15 s gap is not yet a breath
        detector._update_state(False)  # 0.18 s: the sixth frame is the breath
        assert not detector.in_speech

    def test_a_short_sentence_still_needs_the_full_silence(self):
        detector = _silero()
        _start_speech(detector)
        for _ in range(3):
            detector._update_state(True)

        for _ in range(19):
            detector._update_state(False)
        assert detector.in_speech
        detector._update_state(False)
        assert not detector.in_speech


class TestWebrtcSoftCap:
    def test_the_same_rule_applies_to_the_webrtc_detector(self, monkeypatch):
        detector = VADDetector(
            max_speech_s=0.3, silence_threshold_s=0.6, activation_threshold_s=0.06, frame_duration_ms=30,
            use_envelope_follower=False,
        )
        script = iter([True] * 3 + [True] * 12 + [False] * 6)
        monkeypatch.setattr(detector, "_is_voiced", lambda pcm: next(script))
        frame = b"\x00" * detector.frame_bytes

        states = [detector.process_frame(frame) for _ in range(21)]

        assert states[2] is True  # speech started after the activation window
        assert states[14] is True  # past the target, still one sentence
        assert states[19] is True  # five quiet frames are not a breath yet
        assert states[20] is False  # the sixth is, and the sentence ends whole


class TestHardLimit:
    def test_the_hard_limit_is_a_multiple_of_the_target_with_a_ceiling(self):
        assert hard_max_speech_seconds(6.0) == 6.0 * HARD_MAX_SPEECH_FACTOR
        assert hard_max_speech_seconds(20.0) == 30.0
        assert hard_max_speech_seconds(6.0, 9.0) == 9.0
        assert hard_max_speech_seconds(None) is None
        assert hard_max_speech_seconds(0) is None
