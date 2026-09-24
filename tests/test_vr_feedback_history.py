"""Buzz-and-sound cues, and the reads kept for the wrist panel."""

from __future__ import annotations

import io
import wave

from src.core.vr_feedback import CUES, REPEAT_GUARD_SECONDS, VRFeedback, tone_wav
from src.core.vr_history import ReadHistory


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _feedback(tmp_path, **kwargs):
    pulses: list = []
    played: list = []
    clock = _Clock()
    feedback = VRFeedback(
        lambda hand, **kw: pulses.append((hand, kw)) or True,
        sound_dir=tmp_path / "cues",
        player=lambda path: played.append(path) or True,
        clock=clock,
        **kwargs,
    )
    return feedback, pulses, played, clock


class TestFeedback:
    def test_every_cue_has_a_valid_sound(self):
        for name, (_hands, _seconds, _amplitude, sound) in CUES.items():
            if sound is None:
                continue
            data = tone_wav(sound, 0.5)
            with wave.open(io.BytesIO(data)) as wav:
                assert wav.getnframes() > 0, name

    def test_a_frame_cue_buzzes_both_hands_and_plays_its_sound(self, tmp_path):
        feedback, pulses, played, _clock = _feedback(tmp_path)

        feedback.cue("frame_ready")

        assert sorted(hand for hand, _kw in pulses) == ["left", "right"]
        assert len(played) == 1 and played[0].exists()

    def test_a_click_buzzes_only_the_hand_that_clicked(self, tmp_path):
        feedback, pulses, _played, _clock = _feedback(tmp_path)

        feedback.cue("click", "right")

        assert [hand for hand, _kw in pulses] == ["right"]

    def test_a_click_without_a_hand_only_sounds(self, tmp_path):
        feedback, pulses, played, _clock = _feedback(tmp_path)

        feedback.cue("click", None)

        assert pulses == [] and len(played) == 1

    def test_the_same_cue_twice_at_once_is_one_cue(self, tmp_path):
        feedback, pulses, _played, clock = _feedback(tmp_path)

        feedback.cue("result")
        feedback.cue("result")
        clock.now += REPEAT_GUARD_SECONDS + 0.01
        feedback.cue("result")

        assert len(pulses) == 4

    def test_switches_and_volume(self, tmp_path):
        feedback, pulses, played, _clock = _feedback(tmp_path, haptics=False, sounds=True, volume=0.0)

        feedback.cue("capture")
        assert pulses == [] and played == []

        feedback.configure(haptics=True, sounds=False, volume=1.0)
        feedback.cue("error")
        assert pulses and played == []

    def test_an_unknown_cue_is_ignored(self, tmp_path):
        feedback, pulses, played, _clock = _feedback(tmp_path)

        feedback.cue("nonsense")

        assert pulses == [] and played == []

    def test_a_failing_pulse_never_escapes(self, tmp_path):
        def broken(hand, **kwargs):
            raise RuntimeError("no controller")

        feedback = VRFeedback(broken, sound_dir=None, player=lambda path: True)

        feedback.cue("frame_ready")


class TestHistory:
    def test_newest_first_and_bounded(self):
        history = ReadHistory(max_entries=3)
        for index in range(5):
            history.add(card=None, pairs=[(f"o{index}", f"t{index}")], title=str(index))

        assert [entry.title for entry in history.recent()] == ["4", "3", "2"]
        assert len(history) == 3

    def test_pinned_reads_come_first_and_are_never_pushed_out(self):
        history = ReadHistory(max_entries=3)
        first = history.add(card=None, pairs=[("a", "甲")], title="first")
        history.set_pinned(first.entry_id, True)
        for index in range(5):
            history.add(card=None, pairs=[("b", "乙")], title=str(index))

        titles = [entry.title for entry in history.recent()]
        assert titles[0] == "first"
        assert len(titles) == 3

    def test_summary_is_the_first_translated_line(self):
        history = ReadHistory()
        entry = history.add(card=None, pairs=[("", ""), ("hello", ""), ("x", "你好")])

        assert entry.summary == "hello"

    def test_clear_keeps_the_pins(self):
        history = ReadHistory()
        kept = history.add(card=None, pairs=[("a", "b")])
        history.add(card=None, pairs=[("c", "d")])
        history.set_pinned(kept.entry_id, True)

        history.clear()

        assert [entry.entry_id for entry in history.recent()] == [kept.entry_id]
        assert history.get(kept.entry_id) is kept
        assert not history.set_pinned(999, True)


class TestConfig:
    def test_cues_default_on_and_bad_values_are_repaired(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config = {"vrc_listen": {"vr_feedback": {"volume": 7, "haptics": "no"}}}
        _ensure_vrc_listen_config(config)

        feedback = config["vrc_listen"]["vr_feedback"]
        assert feedback["sounds"] is True
        assert feedback["volume"] == 0.5
        assert feedback["haptics"] is False

    def test_push_to_talk_is_off_unless_chosen(self):
        from src.utils.config_manager import _ensure_audio_device_config

        config = {"audio": {}}
        _ensure_audio_device_config(config)
        assert config["audio"]["push_to_talk"] is False

        config = {"audio": {"push_to_talk": True}}
        _ensure_audio_device_config(config)
        assert config["audio"]["push_to_talk"] is True

    def test_controller_settings_default_and_repair(self):
        from src.utils.config_manager import _ensure_vrc_listen_config

        config = {"vrc_listen": {"vr_controls": {"grip_threshold": 5, "swap_trigger_grip": "no"}}}
        _ensure_vrc_listen_config(config)

        controls = config["vrc_listen"]["vr_controls"]
        assert controls == {
            "grip_threshold": 0.4,
            "swap_trigger_grip": False,
            "block_game_input": True,
            "trigger_threshold": 0.5,
            "thumbrest_taps": 4,
            "thumbrest_action": "frame_gesture",
        }
        shot = config["vrc_listen"]["screenshot_translation"]
        assert (shot["view_eye"], shot["quick_frame"], shot["gesture_sensitivity"]) == ("right", False, 0.5)
