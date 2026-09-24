"""Tests for configuration management."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
import os

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import config_manager
from src.asr.model_registry import SENSEVOICE_DEFAULT_REVISION
from src.tts.api_tts_config import (
    QWEN_TTS_DEFAULT_MODEL,
    XIAOMI_TTS_DEFAULT_MODEL,
)
from src.utils.ui_config import (
    DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL,
    OUTPUT_FORMAT_2_DISABLED,
    QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
    QWEN_TRANSLATION_BASE_URL_MAINLAND,
    TRANSLATION_BACKENDS,
    TRANSLATION_MODEL_PRESETS,
    XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_CN,
    XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG,
    NVIDIA_TRANSLATION_BASE_URL,
    get_backend_model_profile,
    normalize_output_format_2,
)


class TestConfigEncryption(unittest.TestCase):
    """Test API key encryption and decryption."""

    def test_protect_secret_empty_string(self):
        """Empty strings should remain empty."""
        result = config_manager._protect_secret("")
        assert result == ""

    def test_protect_secret_already_protected(self):
        """Already protected secrets should not be re-encrypted."""
        protected = "dpapi:v1:somebase64data"
        result = config_manager._protect_secret(protected)
        assert result == protected

    def test_protect_secret_raises_when_dpapi_is_unavailable(self):
        with patch.object(config_manager, "_can_protect_secrets", return_value=False):
            with self.assertRaises(config_manager.SecretProtectionError):
                config_manager._protect_secret("must-not-be-plaintext")

    def test_protect_secret_raises_when_dpapi_sealing_fails(self):
        with patch.object(config_manager, "_can_protect_secrets", return_value=True), \
             patch.object(config_manager, "_dpapi_protect", side_effect=OSError("failure")):
            with self.assertRaises(config_manager.SecretProtectionError):
                config_manager._protect_secret("must-not-be-plaintext")

    def test_unprotect_secret_plaintext(self):
        """Plaintext secrets should pass through unchanged."""
        plaintext = "my-api-key-12345"
        result = config_manager._unprotect_secret(plaintext)
        assert result == plaintext

    def test_unprotect_secret_empty(self):
        """Empty strings should remain empty."""
        result = config_manager._unprotect_secret("")
        assert result == ""

    def test_unprotect_secret_keeps_ciphertext_when_dpapi_unavailable(self):
        """DPAPI failures should not erase stored API keys."""
        protected = "dpapi:v1:not-valid-base64"
        original_can_protect = config_manager._can_protect_secrets
        config_manager._can_protect_secrets = lambda: False
        try:
            result = config_manager._unprotect_secret(protected)
        finally:
            config_manager._can_protect_secrets = original_can_protect

        assert result == protected

    @unittest.skipUnless(sys.platform == "win32", "DPAPI only on Windows")
    def test_protect_unprotect_roundtrip(self):
        """Protect and unprotect should be reversible on Windows."""
        original = "test-api-key-secret"
        protected = config_manager._protect_secret(original)
        assert protected.startswith("dpapi:v1:")
        unprotected = config_manager._unprotect_secret(protected)
        assert unprotected == original


class TestConfigMerge(unittest.TestCase):
    """Test configuration merging logic."""

    def test_merge_defaults_empty_current(self):
        """Merging with empty current should return defaults."""
        defaults = {"key": "value", "nested": {"a": 1}}
        current = {}
        result = config_manager._merge_defaults(defaults, current)
        assert result == defaults

    def test_merge_defaults_override(self):
        """Current values should override defaults."""
        defaults = {"key": "default", "other": "value"}
        current = {"key": "custom"}
        result = config_manager._merge_defaults(defaults, current)
        assert result["key"] == "custom"
        assert result["other"] == "value"

    def test_merge_defaults_nested(self):
        """Nested dictionaries should merge recursively."""
        defaults = {"nested": {"a": 1, "b": 2}}
        current = {"nested": {"b": 99}}
        result = config_manager._merge_defaults(defaults, current)
        assert result["nested"]["a"] == 1
        assert result["nested"]["b"] == 99

    def test_merge_defaults_preserve_extra_keys(self):
        """Extra keys in current should be preserved."""
        defaults = {"key": "value"}
        current = {"key": "custom", "extra": "data"}
        result = config_manager._merge_defaults(defaults, current)
        assert result["key"] == "custom"
        assert result["extra"] == "data"


class TestConfigValidation(unittest.TestCase):
    """Test configuration validation."""

    def test_contains_plaintext_api_key_true(self):
        """Should detect plaintext API keys."""
        config = {"translation": {"openai": {"api_key": "sk-1234567890"}}}
        assert config_manager._contains_plaintext_api_key(config) is True

    def test_contains_plaintext_api_key_false_protected(self):
        """Should not flag protected API keys."""
        config = {"translation": {"openai": {"api_key": "dpapi:v1:base64data"}}}
        assert config_manager._contains_plaintext_api_key(config) is False

    def test_contains_plaintext_api_key_false_empty(self):
        """Should not flag empty API keys."""
        config = {"translation": {"openai": {"api_key": ""}}}
        assert config_manager._contains_plaintext_api_key(config) is False

    def test_ensure_ui_config_defaults_to_851_and_normalizes_system_font(self):
        config = {"ui": {}}

        assert config_manager._ensure_ui_config(config) is True
        assert config["ui"]["font_family"] == "851"

        config["ui"]["font_family"] = "system-default"
        assert config_manager._ensure_ui_config(config) is True
        assert config["ui"]["font_family"] == "system"

    def test_contains_plaintext_api_key_detects_api_tts_keys(self):
        config = {
            "tts": {
                "mimo_tts": {"api_key": "mimo-key"},
                "qwen_tts": {"api_key": ""},
            }
        }

        assert config_manager._contains_plaintext_api_key(config) is True

    def test_qwen_tts_secret_round_trip_stays_engine_specific(self):
        config = {
            "translation": {
                "qianwen": {"api_key": "translation-key"},
            },
            "tts": {
                "qwen_tts": {
                    "api_key": "tts-key",
                    "region": "china_mainland",
                    "base_url": "https://dashscope.aliyuncs.com/api/v1",
                },
            },
        }

        with patch.object(
            config_manager,
            "_protect_secret",
            side_effect=lambda value: f"dpapi:v1:sealed:{value}" if value else "",
        ):
            stored = config_manager._protect_config_for_storage(config)

        assert config["translation"]["qianwen"]["api_key"] == "translation-key"
        assert config["tts"]["qwen_tts"]["api_key"] == "tts-key"
        assert stored["translation"]["qianwen"]["api_key"] == (
            "dpapi:v1:sealed:translation-key"
        )
        assert stored["tts"]["qwen_tts"]["api_key"] == "dpapi:v1:sealed:tts-key"

        with patch.object(
            config_manager,
            "_unprotect_secret",
            side_effect=lambda value: str(value).removeprefix("dpapi:v1:sealed:"),
        ):
            runtime = config_manager._unprotect_config_for_runtime(stored)

        assert runtime["translation"]["qianwen"]["api_key"] == "translation-key"
        assert runtime["tts"]["qwen_tts"]["api_key"] == "tts-key"
        assert runtime["tts"]["qwen_tts"]["region"] == "china_mainland"

    def test_ensure_tts_config_defaults_auto_read_enabled(self):
        """New TTS configs should default to auto-read after manual translation."""
        config = {}

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["enabled"] is False
        assert config["tts"]["auto_read"] is True
        assert config["tts"]["monitor_enabled"] is False
        assert config["tts"]["output_to_vrchat"] is False
        assert config["tts"]["output_device"] is None
        assert config["tts"]["output_device_name"] == ""
        assert config["tts"]["voicevox"]["voice"] is None
        assert config["tts"]["voicevox"]["volume"] == 0.8
        assert config["tts"]["style_bert_vits2"]["voice"] is None
        assert config["tts"]["style_bert_vits2"]["device"] == "cpu"
        assert config["tts"]["style_bert_vits2"]["bert_language"] == "jp"
        assert config["tts"]["qwen_vc"]["voice"] == ""
        assert config["tts"]["qwen_vc"]["upload_consent"] is False
        assert config["tts"]["engine"] == "qwen_tts"
        assert config["tts"]["qwen_vc"]["prewarm"] is True
        assert config["tts"]["qwen_vc"]["model"] == "qwen3-tts-vc-2026-01-22"
        assert config["tts"]["mimo_tts"]["model"] == "mimo-v2.5-tts"
        assert config["tts"]["mimo_tts"]["voice"] == "mimo_default"
        assert config["tts"]["qwen_tts"]["region"] == "singapore"
        assert config["tts"]["qwen_tts"]["base_url"].startswith(
            "https://dashscope-intl."
        )
        assert config["tts"]["qwen_tts"]["model"] == "qwen3-tts-flash"
        assert config["tts"]["qwen_tts"]["optimize_instructions"] is True

    def test_ensure_tts_config_preserves_qwen_flash_model(self):
        config = {
            "tts": {
                "qwen_tts": {
                    "model": "qwen3-tts-flash",
                    "region": "singapore",
                    "base_url": "https://dashscope-intl.aliyuncs.com/api/v1",
                }
            }
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["qwen_tts"]["model"] == "qwen3-tts-flash"

    def test_ensure_tts_config_migrates_existing_output_device(self):
        """Existing non-default output devices should keep VRChat output enabled."""
        config = {"tts": {"output_device": 14}}

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["output_to_vrchat"] is True
        assert config["tts"]["output_device"] == 14
        assert config["tts"]["output_device_name"] == ""

    def test_ensure_tts_config_normalizes_style_bert_language_aliases(self):
        """Old saved BERT language labels should be normalized to runtime codes."""
        config = {
            "tts": {
                "style_bert_vits2": {
                    "bert_language": "English",
                }
            }
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["style_bert_vits2"]["bert_language"] == "en"

        config["tts"]["style_bert_vits2"]["bert_language"] = "zh_CN"
        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["style_bert_vits2"]["bert_language"] == "zh"

    def test_ensure_tts_config_preserves_style_bert_gpu_device(self):
        """Style-Bert-VITS2 should preserve the optional CUDA device setting."""
        config = {
            "tts": {
                "style_bert_vits2": {
                    "device": "cuda",
                }
            }
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["style_bert_vits2"]["device"] == "cuda"

    def test_ensure_tts_config_normalizes_invalid_style_bert_device_to_cpu(self):
        """Invalid Style-Bert-VITS2 devices should fall back to the CPU default."""
        config = {
            "tts": {
                "style_bert_vits2": {
                    "device": "gpu",
                }
            }
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["style_bert_vits2"]["device"] == "cpu"

    def test_ensure_tts_config_migrates_local_cloning_to_cloud_cloning(self):
        """Configs naming the removed local engine keep cloning, not silence."""
        config = {
            "xtts_device": "cuda",
            "tts": {
                "engine": "xtts",
                "xtts": {"device": "CUDA", "language": "zh", "voice": "custom"},
            },
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["engine"] == "qwen_vc"
        assert "xtts" not in config["tts"]
        assert "xtts_device" not in config
        assert config["tts"]["qwen_vc"]["model"] == "qwen3-tts-vc-2026-01-22"
        assert config["tts"]["qwen_vc"]["custom_voices"] == []
        assert config["tts"]["qwen_vc"]["upload_consent"] is False

    def test_ensure_tts_config_drops_cloned_voices_that_no_longer_exist(self):
        config = {
            "tts": {
                "engine": "qwen_vc",
                "qwen_vc": {
                    "voice": "gone",
                    "custom_voices": [
                        {"voice_id": "kept", "display_name": "Kept"},
                        {"display_name": "no id"},
                    ],
                },
            }
        }

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        stored = config["tts"]["qwen_vc"]["custom_voices"]
        assert [voice["voice_id"] for voice in stored] == ["kept"]
        assert config["tts"]["qwen_vc"]["voice"] == "kept"

    def test_ensure_tts_config_reuses_the_qwen_api_key_for_cloning(self):
        config = {
            "tts": {
                "qwen_tts": {"api_key": "sk-shared"},
                "qwen_vc": {"api_key": ""},
            }
        }

        config_manager._ensure_tts_config(config)

        assert config["tts"]["qwen_vc"]["api_key"] == "sk-shared"

    def test_ensure_mode_config_adds_simul_mode_defaults(self):
        """Mode config should default to translation with simultaneous presets."""
        config = {}

        changed = config_manager._ensure_mode_config(config)

        assert changed is True
        assert config["app_mode"] == "translation"
        assert config["simul_mode"]["tts_strategy"] == "queue"
        assert config["simul_mode"]["vad_silence_ms"] == 300

    def test_ensure_mode_config_normalizes_invalid_mode(self):
        config = {"app_mode": "unknown", "simul_mode": {"show_subtitle": False}}

        changed = config_manager._ensure_mode_config(config)

        assert changed is True
        assert config["app_mode"] == "translation"
        assert config["simul_mode"]["show_subtitle"] is False
        assert config["simul_mode"]["merge_window_ms"] == 800

    def test_audio_processing_defaults_are_quiet_and_responsive(self):
        """New audio configs should use the release VAD defaults."""
        config = {"audio": {}}

        changed = config_manager._ensure_audio_device_config(config, loaded={})

        assert changed is True
        assert config["audio"]["denoise_strength"] == 0.0
        assert config["audio"]["vad_silence_threshold"] == 0.65
        assert config["audio"]["vad_speech_ratio"] == 0.6
        assert config["audio"]["vad_activation_threshold_s"] == 0.2
        assert config["audio"]["vad_sensitivity"] == 2
        assert config["audio"]["vad_min_rms"] == 0.012
        assert config["audio"]["min_segment_s"] == 0.45
        assert config["audio"]["partial_min_speech_s"] == 0.45
        assert config["audio"]["max_segment_s"] == 6.0
        assert config["audio"]["sample_rate"] == 16000
        assert config["audio"]["frame_duration_ms"] == 30

    def test_audio_processing_normalizes_invalid_extended_vad_fields(self):
        config = {
            "audio": {
                "vad_sensitivity": 99,
                "vad_min_rms": -1,
                "min_segment_s": 0,
                "partial_min_speech_s": -0.1,
                "max_segment_s": -1,
                "sample_rate": 12345,
                "frame_duration_ms": 25,
            }
        }

        changed = config_manager._ensure_audio_device_config(config, loaded=config)

        assert changed is True
        assert config["audio"]["vad_sensitivity"] == 2
        assert config["audio"]["vad_min_rms"] == 0.012
        assert config["audio"]["min_segment_s"] == 0.45
        assert config["audio"]["partial_min_speech_s"] == 0.45
        assert config["audio"]["max_segment_s"] == 6.0
        assert config["audio"]["sample_rate"] == 16000
        assert config["audio"]["frame_duration_ms"] == 30

    def test_audio_processing_forces_asr_native_16khz_rate(self):
        config = {"audio": {"sample_rate": 48000}}

        changed = config_manager._ensure_audio_device_config(config, loaded=config)

        assert changed is True
        assert config["audio"]["sample_rate"] == 16000

    def test_hotkey_config_defaults_mic_mute_hotkey(self):
        """New hotkey configs should default the microphone mute shortcut."""
        config = {}

        changed = config_manager._ensure_hotkey_config(config)

        assert changed is True
        assert config["hotkeys"]["mic_mute"] == config_manager.DEFAULT_MIC_MUTE_HOTKEY

    def test_hotkey_config_normalizes_mic_mute_hotkey(self):
        config = {"hotkeys": {"mic_mute": "alt-c"}}

        changed = config_manager._ensure_hotkey_config(config)

        assert changed is True
        assert config["hotkeys"]["mic_mute"] == "Alt+C"

    def test_text_input_old_default_migrates_to_alt_x(self):
        config = {"text_input_window": {"hotkey": "Ctrl+Alt+X"}}

        changed = config_manager._ensure_text_input_window_config(config)

        assert changed is True
        assert (
            config["text_input_window"]["hotkey"]
            == config_manager.DEFAULT_TEXT_INPUT_HOTKEY
        )

    def test_ensure_ui_config_adds_background_path_default(self):
        config = {}

        changed = config_manager._ensure_ui_config(config)

        assert changed is True
        assert config["ui"]["background_image_path"] == ""

    def test_ensure_ui_config_preserves_background_path(self):
        config = {"ui": {"background_image_path": "backgrounds/custom.png"}}

        changed = config_manager._ensure_ui_config(config)

        assert changed is True
        assert config["ui"]["background_image_path"] == "backgrounds/custom.png"
        assert config["ui"]["font_family"] == "851"

    def test_ensure_ui_config_normalizes_background_path(self):
        config = {"ui": {"background_image_path": 123}}

        changed = config_manager._ensure_ui_config(config)

        assert changed is True
        assert config["ui"]["background_image_path"] == "123"

    def test_ensure_ui_config_replaces_invalid_ui_block(self):
        config = {"ui": "invalid"}

        changed = config_manager._ensure_ui_config(config)

        assert changed is True
        assert config["ui"]["background_image_path"] == ""

    def test_vrc_listen_segmentation_defaults_keep_sentences_whole(self):
        """A pause inside a sentence must not end the segment.

        0.40 s cut speakers off mid-thought, and the 2.0 s cap force-closed
        long sentences even when nobody had paused.
        """
        config = {"audio": {"vad_silence_threshold": 0.65}, "vrc_listen": {}}

        changed = config_manager._ensure_vrc_listen_config(config, loaded={})

        assert changed is True
        assert config["vrc_listen"]["tail_silence_s"] == 0.8
        assert config["vrc_listen"]["segment_duration_s"] == 5.0
        assert config["vrc_listen"]["latency_profile_version"] == 2

    def test_vrc_listen_latency_profile_migrates_only_shipped_defaults(self):
        """Upgrade inherited defaults; never overwrite a deliberate choice."""
        legacy_v0 = {"vrc_listen": {"tail_silence_s": 0.65}}
        legacy_v1 = {
            "vrc_listen": {
                "tail_silence_s": 0.4,
                "segment_duration_s": 2.0,
                "latency_profile_version": 1,
            }
        }
        custom = {
            "vrc_listen": {
                "tail_silence_s": 1.5,
                "segment_duration_s": 3.0,
                "latency_profile_version": 1,
            }
        }

        for config in (legacy_v0, legacy_v1, custom):
            assert config_manager._ensure_vrc_listen_config(
                config, loaded={"vrc_listen": dict(config["vrc_listen"])}
            )

        assert legacy_v0["vrc_listen"]["tail_silence_s"] == 0.8
        assert legacy_v1["vrc_listen"]["tail_silence_s"] == 0.8
        assert legacy_v1["vrc_listen"]["segment_duration_s"] == 5.0

        assert custom["vrc_listen"]["tail_silence_s"] == 1.5
        assert custom["vrc_listen"]["segment_duration_s"] == 3.0

        assert legacy_v0["vrc_listen"]["asr_timeout_s"] == 5.0
        assert legacy_v0["vrc_listen"]["translation_timeout_s"] == 4.0

    def test_vrc_listen_defaults_follow_main_asr(self):
        config = {}

        changed = config_manager._ensure_vrc_listen_config(config, loaded={})

        assert changed is True
        assert config["vrc_listen"]["asr_engine"] == "same_as_main"

    def test_vrc_listen_adds_vad_diagnostic_defaults(self):
        config = {"vrc_listen": {}}

        changed = config_manager._ensure_vrc_listen_config(config, loaded={})

        assert changed is True
        assert config["vrc_listen"]["vad_speech_ratio"] == 0.4
        assert config["vrc_listen"]["vad_activation_threshold_s"] == 0.06
        assert config["vrc_listen"]["vad_min_rms"] == 0.02
        assert config["vrc_listen"]["denoise_strength"] == 0.35
        assert config["vrc_listen"]["vad_type"] == "webrtc"
        assert config["vrc_listen"]["capture_mode"] == "output_device_loopback"
        assert config["vrc_listen"]["target_process_names"] == ["VRChat.exe"]
        assert config["vrc_listen"]["process_preset"] == "custom"
        assert config["vrc_listen"]["recent_process_names"] == []
        assert config["vrc_listen"]["fallback_to_device_loopback"] is True

    def test_vrc_listen_preserves_custom_target_process_names(self):
        config = {
            "vrc_listen": {
                "target_process_names": ["Game.exe", "Game.exe", "UnityPlayer.exe", ""],
                "recent_process_names": "OtherGame.exe",
                "capture_mode": "process_loopback_target",
            }
        }

        changed = config_manager._ensure_vrc_listen_config(
            config, loaded={"vrc_listen": {}}
        )

        assert changed is True
        assert config["vrc_listen"]["target_process_names"] == [
            "Game.exe",
            "UnityPlayer.exe",
        ]
        assert config["vrc_listen"]["recent_process_names"] == ["OtherGame.exe"]
        assert config["vrc_listen"]["capture_mode"] == "process_loopback_target"

    def test_osc_config_adds_listener_and_control_defaults(self):
        config = {"osc": {"receive_port": "bad", "control_prefix": ""}}

        changed = config_manager._ensure_osc_config(config)

        assert changed is True
        assert config["osc"]["receive_host"] == "127.0.0.1"
        assert config["osc"]["receive_port"] == 9001
        assert config["osc"]["listener_enabled"] is True
        assert config["osc"]["sync_mute_self"] is True
        assert config["osc"]["allow_avatar_control"] is False
        assert config["osc"]["control_prefix"] == "Mio"
        assert config["osc"]["control_params"]["mic"] == "MioToggleMic"
        assert config["osc"]["avatar_sync"]["params"]["muted"] == "MioMuted"
        assert config["osc"]["avatar_sync"]["params"]["overlay"] == "MioOverlayActive"

    def test_osc_listener_can_stay_disabled_when_inbound_features_are_off(self):
        config = {
            "osc": {
                "listener_enabled": False,
                "sync_mute_self": False,
                "allow_avatar_control": False,
            }
        }

        config_manager._ensure_osc_config(config)

        assert config["osc"]["listener_enabled"] is False

    def test_removed_legacy_asr_engine_migrates_away(self):
        """A removed engine migrates to one that works without a download."""
        config = {"asr": {"engine": "legacy-local-asr-large", "legacy_local_asr": {}}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["engine"] == "edge-stt"
        assert "legacy_local_asr" not in config["asr"]

    def test_initial_asr_default_uses_locale_recommendation(self):
        """New configs should use the locale-based ASR default."""
        original_selector = config_manager.select_default_asr_engine
        config_manager.select_default_asr_engine = lambda: "edge-stt"
        try:
            config = {"asr": {"engine": "sensevoice-small"}}
            changed = config_manager._apply_initial_asr_default(config)
        finally:
            config_manager.select_default_asr_engine = original_selector

        assert changed is True
        assert config["asr"]["engine"] == "edge-stt"
        assert config["asr"]["engine_source"] == "auto"

    def test_user_selected_asr_engine_is_not_overridden(self):
        original_selector = config_manager.select_default_asr_engine
        config_manager.select_default_asr_engine = lambda: "edge-stt"
        try:
            config = {
                "asr": {
                    "engine": "qwen3-asr",
                    "engine_source": "manual",
                    "user_selected_engine": True,
                }
            }
            changed = config_manager._apply_initial_asr_default(config)
        finally:
            config_manager.select_default_asr_engine = original_selector

        assert changed is False
        assert config["asr"]["engine"] == "qwen3-asr"


    def test_vrc_listen_whisper_migrates_to_sensevoice_not_follow_main(self):
        config = {"vrc_listen": {"asr_engine": "whisper-large-v3-turbo"}}

        changed = config_manager._ensure_vrc_listen_config(config, config)

        assert changed is True
        assert config["vrc_listen"]["asr_engine"] == "sensevoice-small"

    def test_ensure_asr_config_adds_online_provider_defaults(self):
        config = {"asr": {"engine": "qwen3-asr"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["auto_fallback"] is True
        assert config["asr"]["device"] == "cpu"
        assert config["asr"]["fallback_engine"] == "sensevoice-small"
        assert config["asr"]["qwen3_asr"]["model"] == "qwen3-asr-flash-2026-02-10"
        assert config["asr"]["qwen3_asr"]["base_url"] == (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        assert config["asr"]["qwen3_asr"]["hard_timeout_seconds"] == 12
        assert config["asr"]["qwen3_asr"]["max_concurrent_transcriptions"] == 1
        assert "whisper" not in config["asr"]
        assert config["asr"]["edge_stt"]["language"] == "ja-JP"
        assert config["asr"]["edge_stt"]["reuse_connection"] is True
        assert config["asr"]["edge_stt"]["max_turns"] == 18
        assert config["asr"]["streaming"] == {
            "chunk_interval_ms": 250,
            "chunk_window_s": 1.6,
            "ring_buffer_s": 4.0,
            "recent_speech_hold_s": 0.8,
            "partial_stability_hits": 2,
        }

    def test_ensure_asr_config_bounds_streaming_cadence_and_buffer_relationships(self):
        config = {
            "asr": {
                "engine": "sensevoice-small",
                "streaming": {
                    "chunk_interval_ms": 2000,
                    "chunk_window_s": 0.5,
                    "ring_buffer_s": 0.25,
                    "recent_speech_hold_s": -1,
                    "partial_stability_hits": 99,
                },
            }
        }

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        streaming = config["asr"]["streaming"]
        assert streaming["chunk_interval_ms"] == 2000
        assert streaming["chunk_window_s"] == 2.0
        assert streaming["ring_buffer_s"] == 2.0
        assert streaming["recent_speech_hold_s"] == 0.8
        assert streaming["partial_stability_hits"] == 2

    def test_ensure_asr_config_migrates_whisper_engine_to_sensevoice(self):
        config = {"asr": {"engine": "whisper-large-v3-turbo"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["engine"] == "sensevoice-small"
        assert "whisper" not in config["asr"]

    def test_ensure_asr_config_migrates_legacy_whisper_default(self):
        config = {
            "asr": {
                "engine": "whisper-large-v3-turbo",
                "whisper": {
                    "model_id": "iic/Whisper-large-v3-turbo",
                    "model_revision": "master",
                },
            }
        }

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["engine"] == "sensevoice-small"
        # The retired backend's saved section is dropped with the engine.
        assert "whisper" not in config["asr"]

    def test_ensure_asr_config_preserves_gpu_device(self):
        config = {"asr": {"engine": "whisper-large-v3-turbo", "device": "cuda"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["device"] == "cuda"

    def test_ensure_asr_config_normalizes_invalid_device_to_cpu(self):
        config = {"asr": {"engine": "sensevoice-small", "device": "gpu"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["device"] == "cpu"

    def test_ensure_asr_config_migrates_legacy_qwen3_model(self):
        config = {
            "asr": {"engine": "qwen3-asr", "qwen3_asr": {"model": "qwen3-asr-0.6b"}}
        }

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["qwen3_asr"]["model"] == "qwen3-asr-flash-2026-02-10"

    def test_ensure_asr_config_preserves_qwen3_latest_flash_alias(self):
        config = {
            "asr": {
                "engine": "qwen3-asr",
                "qwen3_asr": {"model": "qwen3-asr-flash"},
            }
        }

        config_manager._ensure_asr_config(config)

        assert config["asr"]["qwen3_asr"]["model"] == "qwen3-asr-flash"

    def test_ensure_asr_config_updates_known_qwen3_base_url_for_region(self):
        config = {
            "asr": {
                "engine": "qwen3-asr",
                "qwen3_asr": {
                    "region": "china",
                    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                },
            }
        }

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["qwen3_asr"]["region"] == "china_mainland"
        assert config["asr"]["qwen3_asr"]["base_url"] == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def test_ensure_asr_config_preserves_custom_qwen3_base_url(self):
        config = {
            "asr": {
                "engine": "qwen3-asr",
                "qwen3_asr": {
                    "region": "singapore",
                    "base_url": "https://proxy.example.com/v1",
                },
            }
        }

        config_manager._ensure_asr_config(config)

        assert config["asr"]["qwen3_asr"]["base_url"] == "https://proxy.example.com/v1"

    def test_ensure_asr_config_leaves_custom_qwen3_base_url_blank(self):
        config = {
            "asr": {
                "engine": "qwen3-asr",
                "qwen3_asr": {"region": "custom"},
            }
        }

        config_manager._ensure_asr_config(config)

        assert config["asr"]["qwen3_asr"]["region"] == "custom"
        assert config["asr"]["qwen3_asr"]["base_url"] == ""

    def test_startup_asr_default_preserves_manual_choice(self):
        """Manual ASR choices should survive startup locale recommendations."""
        original_selector = config_manager.select_default_asr_engine
        config_manager.select_default_asr_engine = lambda: "sensevoice-small"
        try:
            config = {"asr": {"engine": "qwen3-asr", "engine_source": "manual"}}
            changed = config_manager._apply_startup_asr_default(config)
        finally:
            config_manager.select_default_asr_engine = original_selector

        assert changed is False
        assert config["asr"]["engine"] == "qwen3-asr"
        assert config["asr"]["engine_source"] == "manual"
        assert config["asr"]["user_selected_engine"] is True

    def test_cleanup_obsolete_runtime_models_removes_deleted_local_asr_models(self):
        """Deferred cleanup should remove deleted local ASR runtime models."""
        original_writable_app_dir = config_manager.writable_app_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            obsolete = root / "runtime_models" / "Legacy--local-asr-medium"
            obsolete_small = root / "runtime_models" / "Legacy--local-asr-small"
            obsolete.mkdir(parents=True)
            obsolete_small.mkdir(parents=True)
            (obsolete / "model.bin").write_text("old", encoding="utf-8")
            (obsolete_small / "model.bin").write_text("small", encoding="utf-8")

            config_manager.writable_app_dir = lambda: root
            try:
                config_manager.cleanup_obsolete_runtime_models()
            finally:
                config_manager.writable_app_dir = original_writable_app_dir

            assert not obsolete.exists()
            assert not obsolete_small.exists()

    def test_auto_language_pair_defaults_chinese_to_japanese(self):
        """Chinese computer language should preset Chinese input and Japanese output."""
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {"backend": "qianwen", "backend_source": "auto"},
        }

        config_manager._ensure_translation_config(
            config,
            loaded={},
            prefer_auto_backend=True,
        )

        assert config["translation"]["source_language"] == "zh"
        assert config["translation"]["target_language"] == "ja"
        assert config["translation"]["target_language_2"] == "en"
        assert config["translation"]["target_language_3"] == ""
        assert config["translation"]["chatbox_template"] == ""
        assert config["translation"]["fallback_backends"] == []
        assert config["translation"]["rewrite_typed_text"] is False
        assert (
            config["translation"][
                "original_only_read_translation_wait_for_tts"
            ]
            is True
        )
        assert "output_format_2" not in config["translation"]
        assert config["translation"]["language_pair_source"] == "auto"

    def test_original_read_translation_tts_wait_toggle_preserves_explicit_false(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "original_only_read_translation_wait_for_tts": False,
            },
        }
        loaded = json.loads(json.dumps(config))

        config_manager._ensure_translation_config(config, loaded=loaded)

        assert (
            config["translation"][
                "original_only_read_translation_wait_for_tts"
            ]
            is False
        )

    def test_auto_language_pair_defaults_non_chinese_to_chinese(self):
        """Japanese or other supported computer languages should translate into Chinese."""
        config = {
            "ui": {"language": "ja"},
            "translation": {
                "backend": "openai",
                "backend_source": "auto",
                "target_language": "ja",
            },
        }

        config_manager._ensure_translation_config(config, loaded={})

        assert config["translation"]["source_language"] == "ja"
        assert config["translation"]["target_language"] == "zh"
        assert config["translation"]["language_pair_source"] == "auto"

    def test_translation_rewrite_migration_preserves_existing_non_off_style(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "asr_rewrite_style": "catgirl",
                "social": {
                    "mode": "roleplay",
                    "persona_preset": "frieren",
                    "persona_prompt": "My saved legacy prompt",
                },
            },
        }
        loaded = json.loads(json.dumps(config))

        config_manager._ensure_translation_config(config, loaded=loaded)

        translation = config["translation"]
        assert translation["asr_rewrite_style"] == "catgirl"
        assert translation["rewrite_typed_text"] is True
        assert translation["social"]["mode"] == "standard"
        assert (
            translation["social"]["persona_prompt"]
            == "My saved legacy prompt"
        )

    def test_translation_rewrite_migration_promotes_known_legacy_style(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "asr_rewrite_style": "off",
                "social": {
                    "mode": "language_exchange",
                    "persona_preset": "custom",
                },
            },
        }
        loaded = json.loads(json.dumps(config))

        config_manager._ensure_translation_config(config, loaded=loaded)

        translation = config["translation"]
        assert translation["asr_rewrite_style"] == "language_exchange"
        assert translation["rewrite_typed_text"] is True
        assert translation["social"]["mode"] == "standard"

    def test_translation_rewrite_migration_neutralizes_unknown_custom_persona(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "social": {
                    "mode": "roleplay",
                    "persona_preset": "custom",
                    "persona_prompt": "Keep this user-authored prompt",
                },
            },
        }
        loaded = json.loads(json.dumps(config))

        config_manager._ensure_translation_config(config, loaded=loaded)

        translation = config["translation"]
        assert translation["asr_rewrite_style"] == "off"
        assert translation["rewrite_typed_text"] is False
        assert translation["social"]["mode"] == "standard"
        assert (
            translation["social"]["persona_prompt"]
            == "Keep this user-authored prompt"
        )

    def test_translation_rewrite_migration_preserves_explicit_typed_toggle(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "rewrite_typed_text": False,
                "social": {
                    "mode": "roleplay",
                    "persona_preset": "frieren",
                },
            },
        }
        loaded = json.loads(json.dumps(config))

        config_manager._ensure_translation_config(config, loaded=loaded)

        translation = config["translation"]
        assert translation["asr_rewrite_style"] == "frieren"
        assert translation["rewrite_typed_text"] is False
        assert translation["social"]["mode"] == "standard"

    def test_manual_language_pair_is_preserved(self):
        """User-selected source/target languages should not be overwritten by locale defaults."""
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "source_language": "auto",
                "target_language": "fr",
                "language_pair_source": "manual",
            },
        }
        loaded = {"translation": dict(config["translation"])}

        config_manager._ensure_translation_config(config, loaded=loaded)

        assert config["translation"]["source_language"] == "auto"
        assert config["translation"]["target_language"] == "fr"
        assert config["translation"]["target_language_2"] == "en"
        assert config["translation"]["language_pair_source"] == "manual"

    def test_output_format_2_normalization(self):
        assert (
            normalize_output_format_2("translated1_with_translated2")
            == "translated1_with_translated2"
        )
        assert normalize_output_format_2("unknown") == OUTPUT_FORMAT_2_DISABLED

    def test_qwen_translation_default_uses_international_region_for_japanese_ui(
        self,
    ):
        config = {
            "ui": {"language": "ja"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "qianwen": {"api_key": "test-key"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        qwen_cfg = config["translation"]["qianwen"]
        assert qwen_cfg["region"] == "singapore"
        assert qwen_cfg["base_url"] == QWEN_TRANSLATION_BASE_URL_INTERNATIONAL
        assert qwen_cfg["timeout_s"] == TRANSLATION_BACKENDS["qianwen"]["timeout_s"]
        assert qwen_cfg["max_retries"] == 0

    def test_qwen_translation_default_uses_mainland_endpoint_for_chinese_ui(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "qianwen": {
                    "api_key": "test-key",
                    "base_url": QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
                },
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={},
            prefer_auto_backend=True,
        )

        assert changed is True
        assert config["translation"]["qianwen"]["region"] == "china_mainland"
        assert (
            config["translation"]["qianwen"]["base_url"]
            == QWEN_TRANSLATION_BASE_URL_MAINLAND
        )

    def test_qwen_translation_preserves_selected_region_across_ui_language(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "qianwen": {
                    "api_key": "test-key",
                    "region": "singapore",
                    "base_url": QWEN_TRANSLATION_BASE_URL_INTERNATIONAL,
                },
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
            prefer_auto_backend=True,
        )

        assert config["translation"]["qianwen"]["region"] == "singapore"
        assert (
            config["translation"]["qianwen"]["base_url"]
            == QWEN_TRANSLATION_BASE_URL_INTERNATIONAL
        )

    def test_qwen_translation_preserves_custom_base_url(self):
        config = {
            "ui": {"language": "ja"},
            "translation": {
                "backend": "qianwen",
                "qianwen": {
                    "api_key": "test-key",
                    "region": "custom",
                    "base_url": "https://proxy.example.com/v1",
                },
            },
        }

        config_manager._ensure_translation_config(config, loaded={})

        assert config["translation"]["qianwen"]["region"] == "custom"
        assert (
            config["translation"]["qianwen"]["base_url"]
            == "https://proxy.example.com/v1"
        )

    def test_deepseek_translation_default_uses_official_endpoint(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "deepseek",
                "backend_source": "manual",
                "deepseek": {"api_key": "test-key"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        deepseek_cfg = config["translation"]["deepseek"]
        assert deepseek_cfg["region"] == "official"
        assert deepseek_cfg["base_url"] == DEEPSEEK_TRANSLATION_BASE_URL_OFFICIAL
        assert deepseek_cfg["model"] == TRANSLATION_BACKENDS["deepseek"]["model"]
        assert (
            deepseek_cfg["timeout_s"] == TRANSLATION_BACKENDS["deepseek"]["timeout_s"]
        )

    def test_deepseek_translation_preserves_custom_proxy_base_url(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "deepseek",
                "backend_source": "manual",
                "deepseek": {
                    "api_key": "test-key",
                    "region": "custom",
                    "base_url": "https://proxy.example.com/v1",
                    "model": "deepseek-v4-flash",
                },
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert config["translation"]["deepseek"]["region"] == "custom"
        assert (
            config["translation"]["deepseek"]["base_url"]
            == "https://proxy.example.com/v1"
        )

    def test_xiaomi_translation_default_uses_china_cluster_for_chinese_ui(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "xiaomi",
                "backend_source": "manual",
                "xiaomi": {"api_key": "test-key"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        xiaomi_cfg = config["translation"]["xiaomi"]
        assert xiaomi_cfg["region"] == "china_cluster"
        assert xiaomi_cfg["base_url"] == XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_CN
        assert xiaomi_cfg["model"] == TRANSLATION_BACKENDS["xiaomi"]["model"]
        assert xiaomi_cfg["timeout_s"] == TRANSLATION_BACKENDS["xiaomi"]["timeout_s"]

    def test_xiaomi_translation_preserves_selected_token_plan_region(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "xiaomi",
                "backend_source": "manual",
                "xiaomi": {
                    "api_key": "test-key",
                    "region": "singapore_cluster",
                    "base_url": XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG,
                },
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
            prefer_auto_backend=True,
        )

        assert config["translation"]["xiaomi"]["region"] == "singapore_cluster"
        assert (
            config["translation"]["xiaomi"]["base_url"]
            == XIAOMI_TRANSLATION_BASE_URL_TOKEN_PLAN_SG
        )

    def test_nvidia_translation_default_uses_hosted_endpoint(self):
        config = {
            "ui": {"language": "ja"},
            "translation": {
                "backend": "nvidia",
                "backend_source": "manual",
                "nvidia": {"api_key": "test-key"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        nvidia_cfg = config["translation"]["nvidia"]
        assert nvidia_cfg["region"] == "global"
        assert nvidia_cfg["base_url"] == NVIDIA_TRANSLATION_BASE_URL
        assert nvidia_cfg["model"] == TRANSLATION_BACKENDS["nvidia"]["model"]

    def test_existing_custom_target_marks_language_pair_manual(self):
        """Old configs with a non-default target should keep that target."""
        config = {
            "ui": {"language": "ja"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "target_language": "fr",
            },
        }
        loaded = {"translation": dict(config["translation"])}

        config_manager._ensure_translation_config(config, loaded=loaded)

        assert config["translation"]["source_language"] == "ja"
        assert config["translation"]["target_language"] == "fr"
        assert config["translation"]["language_pair_source"] == "manual"

    def test_openai_model_presets_use_current_official_family(self):
        """OpenAI presets should expose real current model ids only."""
        presets = TRANSLATION_MODEL_PRESETS["openai"]

        assert TRANSLATION_BACKENDS["openai"]["model"] == "gpt-5.6-sol"
        assert "gpt-5.6-sol" in presets
        assert "gpt-5.6-terra" in presets
        assert "gpt-5.6-luna" in presets
        # Dropped from OpenAI's own catalog once the 5.6 family shipped.
        assert "gpt-5.5" not in presets
        assert "gpt-5.4" not in presets
        assert "gpt-5.4-mini" not in presets
        assert "gpt-5.4-nano" not in presets
        assert "gpt-5.4-pro" not in presets
        assert "gpt-5.5-mini" not in presets

    def test_api_provider_presets_include_latest_model_families(self):
        """All hosted translation backends should expose their current model families."""
        expected_models = {
            "qianwen": ("qwen-mt-plus", "qwen-mt-flash", "qwen-mt-lite"),
            "xiaomi": ("mimo-v2.5-pro", "mimo-v2-flash"),
            "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
            "zhipu": ("glm-5.3", "glm-5.3-flash"),
            "gemini": ("gemini-3.7-flash", "gemini-3.6-flash"),
            "kimi": ("kimi-k3", "kimi-k2.6"),
            "hunyuan": ("hunyuan-turbos-latest", "hunyuan-turbo-latest"),
            "xai": ("grok-4.6", "grok-4.3"),
            "mistral": ("mistral-medium-3-5", "mistral-small-latest"),
            "nvidia": ("nvidia/nemotron-3-nano-30b-a3b",),
            "anthropic": ("claude-sonnet-5", "claude-haiku-4-5"),
        }

        for backend, models in expected_models.items():
            presets = TRANSLATION_MODEL_PRESETS[backend]
            for model in models:
                assert model in presets

        assert TRANSLATION_BACKENDS["qianwen"]["model"] == "qwen-mt-plus"
        assert TRANSLATION_BACKENDS["mistral"]["model"] == "mistral-medium-3-5"
        assert (
            TRANSLATION_BACKENDS["nvidia"]["model"]
            == "nvidia/nemotron-3-nano-30b-a3b"
        )
        assert TRANSLATION_BACKENDS["anthropic"]["model"] == "claude-opus-5"
        assert TRANSLATION_BACKENDS["openai_compatible"]["model"] == "gpt-5.6-sol"
        assert (
            TRANSLATION_BACKENDS["anthropic_compatible"]["model"] == "claude-opus-5"
        )
        assert TRANSLATION_BACKENDS["hunyuan"]["model"] == "hunyuan-turbos-latest"
        assert "gpt-5.6-sol" in TRANSLATION_MODEL_PRESETS["openai_compatible"]
        assert "gpt-5.6-terra" in TRANSLATION_MODEL_PRESETS["openai_compatible"]
        assert "gpt-5.6-luna" in TRANSLATION_MODEL_PRESETS["openai_compatible"]
        assert "gpt-5.5" not in TRANSLATION_MODEL_PRESETS["openai_compatible"]
        assert "gpt-5.4-mini" not in TRANSLATION_MODEL_PRESETS["openai_compatible"]
        assert "claude-opus-5" in TRANSLATION_MODEL_PRESETS["anthropic_compatible"]
        assert "claude-sonnet-5" in TRANSLATION_MODEL_PRESETS["anthropic_compatible"]
        assert "claude-sonnet-4-6" in TRANSLATION_MODEL_PRESETS["anthropic_compatible"]
        assert (
            "claude-haiku-4-5"
            in TRANSLATION_MODEL_PRESETS["anthropic_compatible"]
        )
        qwen_flash = TRANSLATION_MODEL_PRESETS["qianwen"].index("qwen-mt-flash")
        qwen_plus = TRANSLATION_MODEL_PRESETS["qianwen"].index("qwen-mt-plus")
        assert qwen_plus < qwen_flash
        assert "qwen3.6-max-preview" not in TRANSLATION_MODEL_PRESETS["qianwen"]
        assert "qwen3.6-plus" not in TRANSLATION_MODEL_PRESETS["qianwen"]
        assert "qwen3.6-flash" not in TRANSLATION_MODEL_PRESETS["qianwen"]
        assert "kimi-k2-thinking" not in TRANSLATION_MODEL_PRESETS["kimi"]
        assert "grok-4.20-0309-reasoning" not in TRANSLATION_MODEL_PRESETS["xai"]
        assert "magistral-small-latest" not in TRANSLATION_MODEL_PRESETS["mistral"]

    def test_config_example_uses_current_translation_model_defaults(self):
        example_path = Path(__file__).resolve().parents[1] / "config.example.json"
        example = json.loads(example_path.read_text(encoding="utf-8"))
        translation = example["translation"]

        from src.utils.ui_config import DISABLED_TRANSLATION_BACKENDS

        for backend, spec in TRANSLATION_BACKENDS.items():
            if backend in DISABLED_TRANSLATION_BACKENDS:
                continue
            assert translation[backend]["model"] == spec["model"]

        assert example["asr"]["sensevoice"]["model_revision"] == SENSEVOICE_DEFAULT_REVISION
        assert example["tts"]["qwen_tts"]["model"] == QWEN_TTS_DEFAULT_MODEL
        assert example["tts"]["mimo_tts"]["model"] == XIAOMI_TTS_DEFAULT_MODEL

    def test_retired_hosted_models_are_preserved_for_custom_endpoints(self):
        config = {
            "ui": {"language": "en"},
            "translation": {
                "backend": "nvidia",
                "backend_source": "manual",
                "nvidia": {
                    "region": "custom",
                    "base_url": "https://nim.internal.example/v1",
                    "model": "nvidia/nemotron-3-super-120b-a12b",
                },
                "qianwen": {
                    "region": "custom",
                    "base_url": "https://qwen-relay.internal.example/v1",
                    "model": "qwen3.7-max",
                },
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert (
            config["translation"]["nvidia"]["model"]
            == "nvidia/nemotron-3-super-120b-a12b"
        )
        assert config["translation"]["qianwen"]["model"] == "qwen3.7-max"

    def test_every_selectable_translation_model_survives_config_normalization(self):
        for backend, models in TRANSLATION_MODEL_PRESETS.items():
            for model in models:
                with self.subTest(backend=backend, model=model):
                    config = {
                        "ui": {"language": "en"},
                        "translation": {
                            "backend": backend,
                            "backend_source": "manual",
                            backend: {"model": model},
                        },
                    }

                    config_manager._ensure_translation_config(
                        config,
                        loaded={"translation": dict(config["translation"])},
                    )

                    assert config["translation"][backend]["model"] == model

    def test_model_profiles_expose_ten_point_live_scores(self):
        qwen_mt = get_backend_model_profile("qianwen", "qwen-mt-plus")
        gpt = get_backend_model_profile("openai", "gpt-5.6-terra")
        compat_gpt = get_backend_model_profile("openai_compatible", "gpt-5.6-terra")
        custom = get_backend_model_profile("openai", "custom-router-model")

        assert qwen_mt["score"] == "9.7"
        assert gpt["score"] == "9.4"
        assert compat_gpt["score"] == "9.4"
        assert compat_gpt["note"] == "balanced_quality"
        assert custom["score"] == "6.5"

    def test_legacy_provider_defaults_migrate_without_overwriting_selectable_fast_models(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "qianwen": {
                    "model": "qwen-mt-flash",
                    "base_url": QWEN_TRANSLATION_BASE_URL_MAINLAND,
                },
                "doubao": {
                    "base_url": "https://ark.cn-beijing.volces.com/api/compatible/v1",
                    "model": "doubao-seed-2.0-pro",
                },
                "deepseek": {"base_url": "https://api.deepseek.com/v1"},
                "xiaomi": {"model": "mimo-v2-flash"},
                "gemini": {"model": "gemini-3.1-flash-lite"},
                "nvidia": {"model": "nvidia/llama-3.1-nemotron-nano-8b-v1"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["qianwen"]["model"] == "qwen-mt-flash"
        assert (
            config["translation"]["doubao"]["base_url"]
            == "https://ark.cn-beijing.volces.com/api/v3"
        )
        assert config["translation"]["doubao"]["model"] == "doubao-seed-2-0-pro-260215"
        assert (
            config["translation"]["deepseek"]["base_url"] == "https://api.deepseek.com"
        )
        assert config["translation"]["xiaomi"]["model"] == "mimo-v2-flash"
        assert config["translation"]["gemini"]["model"] == "gemini-3.5-flash"
        assert (
            config["translation"]["nvidia"]["model"]
            == "nvidia/nemotron-3-nano-30b-a3b"
        )

    def test_unroutable_qwen36_models_migrate_to_qwen_mt(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "qianwen",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "qianwen": {
                    "model": "qwen3.6-plus",
                    "base_url": QWEN_TRANSLATION_BASE_URL_MAINLAND,
                },
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["qianwen"]["model"] == "qwen-mt-plus"

    def test_thinking_models_migrate_to_non_thinking_variants(self):
        cases = (
            ("deepseek", "deepseek-reasoner", "deepseek-v4-flash"),
            ("kimi", "kimi-k2-thinking", "kimi-k2.6"),
            (
                "xai",
                "grok-4.20-0309-reasoning",
                "grok-4.20-0309-non-reasoning",
            ),
            ("mistral", "magistral-medium-latest", "mistral-medium-3-5"),
        )
        for backend, thinking_model, expected in cases:
            with self.subTest(backend=backend, model=thinking_model):
                config = {
                    "translation": {
                        "backend": backend,
                        backend: {"model": thinking_model},
                    }
                }

                changed = config_manager._ensure_translation_config(
                    config,
                    loaded={"translation": dict(config["translation"])},
                )

                assert changed is True
                assert config["translation"][backend]["model"] == expected

    def test_legacy_openai_model_migrates_to_live_default(self):
        """Old GPT-4 defaults should migrate without touching newer official ids."""
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "openai": {"model": "gpt-4"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["openai"]["model"] == "gpt-5.6-sol"

    def test_removed_models_migrate_only_for_provider_owned_catalogs(self):
        config = {
            "translation": {
                "backend": "openai_compatible",
                "openai": {"model": "gpt-5.4"},
                "openai_compatible": {"model": "gpt-5.4-mini"},
                "anthropic": {"model": "claude-opus-4-1-20250805"},
                "anthropic_compatible": {"model": "claude-opus-4-1-20250805"},
            }
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["openai"]["model"] == "gpt-5.6-sol"
        assert config["translation"]["openai_compatible"]["model"] == "gpt-5.4-mini"
        assert config["translation"]["anthropic"]["model"] == "claude-opus-5"
        assert (
            config["translation"]["anthropic_compatible"]["model"]
            == "claude-opus-4-1-20250805"
        )

    def test_corrupted_openai_claude_model_migrates_to_live_default(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "openai": {"model": "claude"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["openai"]["model"] == "gpt-5.6-sol"

    def test_compatible_proxy_models_are_preserved(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "openai_compatible",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "openai_compatible": {
                    "api_key": "relay-key",
                    "base_url": "https://relay.example.com/v1",
                    "model": "claude-through-openai-relay",
                },
                "anthropic_compatible": {
                    "api_key": "relay-key",
                    "base_url": "https://claude-relay.example.com",
                    "model": "custom-claude-router",
                },
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert (
            config["translation"]["openai_compatible"]["model"]
            == "claude-through-openai-relay"
        )
        assert (
            config["translation"]["openai_compatible"]["base_url"]
            == "https://relay.example.com/v1"
        )
        assert (
            config["translation"]["anthropic_compatible"]["model"]
            == "custom-claude-router"
        )
        assert (
            config["translation"]["anthropic_compatible"]["base_url"]
            == "https://claude-relay.example.com"
        )

    def test_legacy_anthropic_sonnet_migrates_to_supported_floor(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "anthropic",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "anthropic": {"model": "claude-sonnet-4-20250514"},
            },
        }

        changed = config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert changed is True
        assert config["translation"]["anthropic"]["model"] == "claude-opus-5"

    def test_current_openai_gpt41_model_is_preserved(self):
        """GPT-4.1 is still an official option and should not be auto-migrated."""
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "openai",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "openai": {"model": "gpt-4.1-mini"},
            },
        }

        config_manager._ensure_translation_config(
            config,
            loaded={"translation": dict(config["translation"])},
        )

        assert config["translation"]["openai"]["model"] == "gpt-4.1-mini"


class TestPerformanceConfig(unittest.TestCase):
    """Test performance defaults and low-power normalization."""

    def test_settings_preload_defaults_off_and_low_power_forces_off(self):
        config = {"performance": {"profile": "low_power", "preload_settings_window": True}}

        changed = config_manager._ensure_performance_config(config)

        assert changed is True
        assert config["performance"]["preload_settings_window"] is False
        assert config["performance"]["tts_cache_max_mb"] <= 12
        assert config["performance"]["tts_cache_max_items"] <= 32

    def test_settings_preload_can_be_opted_in_for_balanced_profile(self):
        config = {"performance": {"profile": "balanced", "preload_settings_window": True}}

        config_manager._ensure_performance_config(config)

        assert config["performance"]["preload_settings_window"] is True


class TestConfigSave(unittest.TestCase):
    """Test configuration saving."""

    @staticmethod
    def _complete_config(marker: str) -> dict:
        return {
            "asr": {},
            "audio": {},
            "osc": {},
            "translation": {"marker": marker},
            "tts": {},
            "ui": {},
        }

    def test_save_config_atomic(self):
        """Config save should be atomic (temp file + replace)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            config_path = tmp_root / f"config-{os.getpid()}.json"

            # Mock the config path
            original_config_path = config_manager._config_path
            config_manager._config_path = lambda: config_path

            try:
                test_config = {"test": "data", "number": 42}
                config_manager.save_config(test_config)

                assert config_path.exists()
                with config_path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                assert loaded["test"] == "data"
                assert loaded["number"] == 42
            finally:
                config_manager._config_path = original_config_path

    def test_failed_secret_protection_leaves_existing_file_untouched(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            original_bytes = b'{"existing": true}\n'
            config_path.write_bytes(original_bytes)
            plaintext = "secret-that-must-never-reach-disk"
            config = {"translation": {"openai": {"api_key": plaintext}}}

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_can_protect_secrets", return_value=False):
                with self.assertRaises(config_manager.SecretProtectionError):
                    config_manager.save_config(config)

            assert config_path.read_bytes() == original_bytes
            assert plaintext.encode("utf-8") not in config_path.read_bytes()
            assert list(root.glob("config.json.*.tmp")) == []

    def test_failed_secret_protection_never_creates_plaintext_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            plaintext = "secret-that-must-never-reach-disk"
            config = {"translation": {"openai": {"api_key": plaintext}}}

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_can_protect_secrets", return_value=False):
                with self.assertRaises(config_manager.SecretProtectionError):
                    config_manager.save_config(config)

            assert not config_path.exists()
            assert list(root.glob("config.json.*.tmp")) == []

    def test_load_returns_normalized_runtime_config_when_persistence_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            example_path = root / "config.example.json"
            config_path.write_text(
                json.dumps({"translation": {"openai": {"api_key": "legacy-plaintext"}}}),
                encoding="utf-8",
            )
            example_path.write_text("{}", encoding="utf-8")

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_example_path", return_value=example_path), \
                 patch.object(
                     config_manager,
                     "_cleanup_obsolete_runtime_models",
                     side_effect=AssertionError("model cleanup blocked config load"),
                 ), \
                 patch.object(config_manager, "_ensure_ui_config", return_value=True), \
                 patch.object(
                     config_manager,
                     "save_config",
                     side_effect=config_manager.SecretProtectionError("blocked"),
                 ):
                loaded = config_manager.load_config()

            assert loaded["translation"]["openai"]["api_key"] == "legacy-plaintext"

    def test_save_refuses_to_replace_complete_config_with_sparse_payload(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            original = self._complete_config("original")
            config_path.write_text(json.dumps(original), encoding="utf-8")

            with patch.object(config_manager, "_config_path", return_value=config_path):
                with self.assertRaisesRegex(ValueError, "severely incomplete"):
                    config_manager.save_config({})

            assert json.loads(config_path.read_text(encoding="utf-8")) == original

    def test_save_preserves_previous_complete_config_as_last_good(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            original = self._complete_config("original")
            updated = self._complete_config("updated")
            config_path.write_text(json.dumps(original), encoding="utf-8")

            with patch.object(config_manager, "_config_path", return_value=config_path):
                config_manager.save_config(updated)

            backup_path = config_manager._last_good_config_path(config_path)
            assert json.loads(backup_path.read_text(encoding="utf-8")) == original
            assert json.loads(config_path.read_text(encoding="utf-8")) == updated

    def test_load_recovers_severely_incomplete_config_from_last_good(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            example_path = root / "config.example.json"
            backup_path = config_manager._last_good_config_path(config_path)
            config_path.write_text("{}", encoding="utf-8")
            example_path.write_text("{}", encoding="utf-8")
            recovered = self._complete_config("recovered")
            recovered["ui"] = {
                "language": "ja",
                "language_source": "auto",
            }
            backup_path.write_text(json.dumps(recovered), encoding="utf-8")

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_example_path", return_value=example_path), \
                 patch.object(config_manager, "_cleanup_obsolete_runtime_models"), \
                 patch(
                     "src.utils.ui_language_detection.detect_initial_ui_language",
                     return_value="ko",
                 ):
                loaded = config_manager.load_config()

            assert loaded["translation"]["marker"] == "recovered"
            assert loaded["ui"]["language"] == "ja"

    def test_installer_bootstrap_config_uses_system_ui_language(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            example_path = root / "config.example.json"
            config_path.write_text(
                json.dumps(
                    {
                        "ui": {
                            "language": "en",
                            "language_source": "auto",
                            "theme": "dark",
                        }
                    }
                ),
                encoding="utf-8",
            )
            example_path.write_text(
                json.dumps(
                    {
                        "ui": {
                            "language": "zh-CN",
                            "language_source": "auto",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_example_path", return_value=example_path), \
                 patch.object(config_manager, "_cleanup_obsolete_runtime_models"), \
                 patch(
                     "src.utils.ui_language_detection.detect_initial_ui_language",
                     return_value="ko",
                 ):
                loaded = config_manager.load_config()

            assert loaded["ui"]["language"] == "ko"
            assert loaded["ui"]["language_source"] == "auto"

    def test_first_config_file_uses_system_ui_language(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.json"
            example_path = root / "config.example.json"
            example_path.write_text(
                json.dumps(
                    {
                        "ui": {
                            "language": "zh-CN",
                            "language_source": "auto",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(config_manager, "_config_path", return_value=config_path), \
                 patch.object(config_manager, "_example_path", return_value=example_path), \
                 patch.object(config_manager, "_cleanup_obsolete_runtime_models"), \
                 patch(
                     "src.utils.ui_language_detection.detect_initial_ui_language",
                     return_value="ru",
                 ):
                loaded = config_manager.load_config()

            assert loaded["ui"]["language"] == "ru"
            assert loaded["ui"]["language_source"] == "auto"


class TestConfigGet(unittest.TestCase):
    """Test configuration getter utility."""

    def test_get_nested_value(self):
        """Should retrieve nested values."""
        config = {"a": {"b": {"c": "value"}}}
        result = config_manager.get(config, "a", "b", "c")
        assert result == "value"

    def test_get_missing_key_default(self):
        """Should return default for missing keys."""
        config = {"a": {"b": 1}}
        result = config_manager.get(config, "a", "x", default="fallback")
        assert result == "fallback"

    def test_get_non_dict_default(self):
        """Should return default when traversing non-dict."""
        config = {"a": "string"}
        result = config_manager.get(config, "a", "b", default=None)
        assert result is None


if __name__ == "__main__":
    unittest.main()


class TestFirstRunDefaults:
    def test_fresh_install_starts_on_an_asr_that_needs_no_download(self):
        """The first launch must transcribe without a model download.

        Chinese systems used to start on SenseVoice, so a new player could not
        say a single word until hundreds of megabytes had arrived.
        """

        from src.utils.locale_detect import select_default_asr_engine

        assert select_default_asr_engine() == "edge-stt"

    def test_fresh_install_starts_on_the_free_keyless_translator(self):
        from src.utils.ui_config import DEFAULT_BACKEND

        assert DEFAULT_BACKEND == "microsoft_edge_web"

    def test_renamed_edge_web_model_id_migrates(self):
        """The catalog id changed with the rename; the endpoint did not."""

        config = {
            "translation": {
                "backend": "microsoft_edge_web",
                "microsoft_edge_web": {"model": "microsoft-edge-web"},
            }
        }

        config_manager._ensure_translation_config(config)

        assert config["translation"]["microsoft_edge_web"]["model"] == "bing"

    def test_a_deliberate_edge_web_model_choice_is_left_alone(self):
        config = {
            "translation": {
                "backend": "microsoft_edge_web",
                "microsoft_edge_web": {"model": "custom-relay-model"},
            }
        }

        config_manager._ensure_translation_config(config)

        assert (
            config["translation"]["microsoft_edge_web"]["model"] == "custom-relay-model"
        )


class TestModelCatalogCurrency:
    """A retired model id is not cosmetic: the request fails outright."""

    def test_every_selectable_model_has_a_profile_and_score(self):
        from src.utils.ui_config import (
            TRANSLATION_MODEL_PRESETS,
            get_backend_model_profile,
        )

        unrated = []
        from src.utils.ui_config import DISABLED_TRANSLATION_BACKENDS

        for backend, models in TRANSLATION_MODEL_PRESETS.items():
            if backend in DISABLED_TRANSLATION_BACKENDS:
                continue
            for model in models:
                profile = get_backend_model_profile(backend, model)
                if profile["score"] == "6.5" and profile.get("note") == "custom":
                    unrated.append((backend, model))

        assert unrated == []

    def test_each_backend_marks_at_most_one_live_default(self):
        from src.utils.ui_config import (
            TRANSLATION_MODEL_PRESETS,
            get_backend_model_profile,
        )

        for backend, models in TRANSLATION_MODEL_PRESETS.items():
            defaults = [
                model
                for model in models
                if get_backend_model_profile(backend, model).get("note")
                == "live_default"
            ]
            assert len(defaults) <= 1, (backend, defaults)

    def test_retired_models_are_gone_from_the_picker(self):
        from src.utils.ui_config import TRANSLATION_MODEL_PRESETS as presets

        # Verified retired against each provider's own catalog.
        assert "gpt-5.5" not in presets["openai"]
        assert "gemini-2.5-flash" not in presets["gemini"]
        assert "gemini-2.5-flash-lite" not in presets["gemini"]
        assert "kimi-k2.5" not in presets["kimi"]
        for dead in ("glm-5.1", "glm-5", "glm-5-turbo", "glm-4.7"):
            assert dead not in presets["zhipu"], dead

    def test_claude_ids_carry_no_date_suffix(self):
        """A date-suffixed Claude id is not a valid model string."""

        import re

        from src.utils.ui_config import TRANSLATION_MODEL_PRESETS as presets

        for backend in ("anthropic", "anthropic_compatible"):
            for model in presets[backend]:
                assert not re.search(r"-\d{8}$", model), model

    @pytest.mark.parametrize(
        ("backend", "dead", "replacement"),
        [
            ("zhipu", "glm-4.7-flash", "glm-5.3-flash"),
            ("kimi", "kimi-k2.5", "kimi-k2.6"),
            ("anthropic", "claude-haiku-4-5-20251001", "claude-haiku-4-5"),
            ("xai", "grok-4.20", "grok-4.20-0309-non-reasoning"),
        ],
    )
    def test_a_saved_retired_model_migrates(self, backend, dead, replacement):
        config = {"translation": {"backend": backend, backend: {"model": dead}}}

        config_manager._ensure_translation_config(config)

        assert config["translation"][backend]["model"] == replacement

    def test_a_deliberate_model_choice_survives_migration(self):
        config = {
            "translation": {
                "backend": "zhipu",
                "zhipu": {"model": "glm-private-relay-build"},
            }
        }

        config_manager._ensure_translation_config(config)

        assert config["translation"]["zhipu"]["model"] == "glm-private-relay-build"


class TestVROverlayConfig:
    def test_a_fresh_install_leaves_the_headset_panel_off(self):
        """It needs SteamVR running; nobody should pay for an attempt."""

        config: dict = {}
        config_manager._ensure_vrc_listen_config(config, loaded={})
        vr_cfg = config["vrc_listen"]["vr_overlay"]

        assert vr_cfg["enabled"] is False
        assert vr_cfg["width_meters"] == pytest.approx(1.6)
        assert vr_cfg["plate_opacity"] == pytest.approx(0.50)
        assert vr_cfg["position"] == [0.0, -0.32, -1.5]

    def test_a_panel_saved_inside_the_players_face_is_pushed_back(self):
        config = {"vrc_listen": {"vr_overlay": {"position": [0.0, 0.0, 0.0]}}}
        config_manager._ensure_vrc_listen_config(config, loaded=config)

        assert config["vrc_listen"]["vr_overlay"]["position"][2] <= -0.4

    def test_a_corrupt_position_falls_back_rather_than_crashing(self):
        config = {"vrc_listen": {"vr_overlay": {"position": "somewhere"}}}
        config_manager._ensure_vrc_listen_config(config, loaded=config)

        assert config["vrc_listen"]["vr_overlay"]["position"] == [0.0, -0.32, -1.5]

    @pytest.mark.parametrize("key", ["width_meters", "plate_opacity"])
    def test_out_of_range_values_are_repaired(self, key):
        config = {"vrc_listen": {"vr_overlay": {key: 999.0}}}
        config_manager._ensure_vrc_listen_config(config, loaded=config)

        value = config["vrc_listen"]["vr_overlay"][key]
        assert 0.0 <= value <= 4.0

    def test_a_players_own_position_is_preserved(self):
        """A drag in the headset must survive the next launch."""

        config = {"vrc_listen": {"vr_overlay": {"position": [0.5, -0.1, -2.0]}}}
        config_manager._ensure_vrc_listen_config(config, loaded=config)

        assert config["vrc_listen"]["vr_overlay"]["position"] == [0.5, -0.1, -2.0]
