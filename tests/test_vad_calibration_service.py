from src.audio.vad_calibration_service import (
    VadCalibrationService,
    recommend_vad_settings,
    rms_from_snapshot,
)


def test_recommend_vad_settings_uses_noise_and_speech_percentiles():
    result = recommend_vad_settings(
        [0.004, 0.005, 0.006, 0.008, 0.010],
        [0.020, 0.030, 0.040, 0.050, 0.060],
        current_silence_s=0.65,
    )

    assert result.noise_floor > 0
    assert result.speech_floor > result.noise_floor
    assert 0.003 <= result.recommended_min_rms <= 0.04
    assert result.noise_sample_count == 5
    assert result.speech_sample_count == 5


def test_recommend_vad_settings_clamps_extreme_values():
    quiet = recommend_vad_settings([0.0, 0.0], [0.0, 0.001])
    loud = recommend_vad_settings([1.0, 1.2], [1.4, 1.6])

    assert quiet.recommended_min_rms == 0.003
    assert loud.recommended_min_rms == 0.04


def test_rms_from_snapshot_prefers_common_diagnostics_keys():
    assert rms_from_snapshot({"last_frame_rms": 0.012}) == 0.012
    assert rms_from_snapshot({"last_prepared_rms": 0.023}) == 0.023
    assert rms_from_snapshot({"last_rms": 0.034}) == 0.034
    assert rms_from_snapshot({"last_frame_rms": "bad"}) is None


def test_vad_calibration_service_collects_snapshots_and_returns_patch():
    service = VadCalibrationService(current_silence_s=0.65)
    for value in (0.004, 0.005, 0.006):
        assert service.add_noise_snapshot({"last_frame_rms": value}) is True
    for value in (0.025, 0.030, 0.035):
        assert service.add_speech_snapshot({"last_prepared_rms": value}) is True

    result = service.result()
    patch = result.as_config_patch(target="vrc_listen")

    assert result.confidence == "low"
    assert "vrc_listen" in patch
    assert "vad_min_rms" in patch["vrc_listen"]
    assert "tail_silence_s" in patch["vrc_listen"]
