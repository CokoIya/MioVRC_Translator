"""Tests for configuration management."""

import json
from pathlib import Path
import tempfile
import unittest
import sys
import os

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import config_manager
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

    def test_contains_plaintext_api_key_detects_api_tts_keys(self):
        config = {
            "tts": {
                "mimo_tts": {"api_key": "mimo-key"},
                "qwen_tts": {"api_key": ""},
            }
        }

        assert config_manager._contains_plaintext_api_key(config) is True

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
        assert config["tts"]["pyttsx3"]["rate"] == 1.0
        assert config["tts"]["voicevox"]["voice"] is None
        assert config["tts"]["aivis_speech"]["volume"] == 0.8
        assert config["tts"]["style_bert_vits2"]["voice"] is None
        assert config["tts"]["style_bert_vits2"]["device"] == "cpu"
        assert config["tts"]["style_bert_vits2"]["bert_language"] == "jp"
        assert config["tts"]["mimo_tts"]["model"] == "mimo-v2.5-tts"
        assert config["tts"]["mimo_tts"]["voice"] == "mimo_default"
        assert config["tts"]["qwen_tts"]["region"] == "singapore"
        assert config["tts"]["qwen_tts"]["base_url"].startswith(
            "https://dashscope-intl."
        )

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

        assert changed is False
        assert config["ui"]["background_image_path"] == "backgrounds/custom.png"

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

    def test_vrc_listen_tail_silence_defaults_to_release_value(self):
        """Reverse listen tail silence should use the same fast default."""
        config = {"audio": {"vad_silence_threshold": 0.65}, "vrc_listen": {}}

        changed = config_manager._ensure_vrc_listen_config(config, loaded={})

        assert changed is True
        assert config["vrc_listen"]["tail_silence_s"] == 0.65

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
        assert config["osc"]["listener_enabled"] is False
        assert config["osc"]["sync_mute_self"] is True
        assert config["osc"]["allow_avatar_control"] is False
        assert config["osc"]["control_prefix"] == "Mio"
        assert config["osc"]["control_params"]["mic"] == "MioToggleMic"
        assert config["osc"]["avatar_sync"]["params"]["muted"] == "MioMuted"
        assert config["osc"]["avatar_sync"]["params"]["overlay"] == "MioOverlayActive"

    def test_removed_legacy_asr_engine_migrates_away(self):
        """Removed local ASR engines should migrate to the default local ASR."""
        config = {"asr": {"engine": "legacy-local-asr-large", "legacy_local_asr": {}}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["engine"] == "sensevoice-small"
        assert "legacy_local_asr" not in config["asr"]

    def test_initial_asr_default_uses_locale_recommendation(self):
        """New configs should use the locale-based ASR default."""
        original_selector = config_manager.select_default_asr_engine
        config_manager.select_default_asr_engine = lambda: "webspeech"
        try:
            config = {"asr": {"engine": "sensevoice-small"}}
            changed = config_manager._apply_initial_asr_default(config)
        finally:
            config_manager.select_default_asr_engine = original_selector

        assert changed is True
        assert config["asr"]["engine"] == "webspeech"
        assert config["asr"]["engine_source"] == "auto"

    def test_user_selected_asr_engine_is_not_overridden(self):
        original_selector = config_manager.select_default_asr_engine
        config_manager.select_default_asr_engine = lambda: "webspeech"
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

    def test_ensure_asr_config_adds_online_provider_defaults(self):
        config = {"asr": {"engine": "qwen3-asr"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["auto_fallback"] is True
        assert config["asr"]["device"] == "cpu"
        assert config["asr"]["fallback_engine"] == "sensevoice-small"
        assert config["asr"]["webspeech"]["language"] == "ja-JP"
        assert config["asr"]["qwen3_asr"]["model"] == "qwen3-asr-flash-2026-02-10"
        assert config["asr"]["qwen3_asr"]["base_url"] == (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        assert (
            config["asr"]["whisper"]["model_id"]
            == "iic/speech_whisper-small_asr_english"
        )
        assert config["asr"]["whisper"]["model_revision"] == "master"
        assert config["asr"]["whisper"]["language"] == "auto"
        assert config["asr"]["whisper"]["ncpu"] is None
        assert config["asr"]["gemini_live"]["transcribe_only"] is True

    def test_ensure_asr_config_adds_whisper_defaults(self):
        config = {"asr": {"engine": "whisper-large-v3-turbo"}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
        assert config["asr"]["engine"] == "whisper-large-v3-turbo"
        assert (
            config["asr"]["whisper"]["model_id"]
            == "iic/speech_whisper-small_asr_english"
        )
        assert config["asr"]["whisper"]["model_revision"] == "master"
        assert config["asr"]["whisper"]["language"] == "auto"

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
        assert (
            config["asr"]["whisper"]["model_id"]
            == "iic/speech_whisper-small_asr_english"
        )

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
        """Startup cleanup should remove deleted local ASR runtime models."""
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
                config_manager._cleanup_obsolete_runtime_models()
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
        assert "output_format_2" not in config["translation"]
        assert config["translation"]["language_pair_source"] == "auto"

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

    def test_qwen_translation_default_uses_international_endpoint_for_non_chinese_ui(
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

        assert TRANSLATION_BACKENDS["openai"]["model"] == "gpt-5.5"
        assert "gpt-5.5" in presets
        assert "gpt-5.4" in presets
        assert "gpt-5.4-mini" in presets
        assert "gpt-5.4-nano" in presets
        assert "gpt-5.4-pro" not in presets
        assert "gpt-5.5-mini" not in presets

    def test_api_provider_presets_include_latest_model_families(self):
        """All hosted translation backends should expose their current model families."""
        expected_models = {
            "qianwen": ("qwen3.7-max", "qwen-mt-plus", "qwen-mt-flash"),
            "xiaomi": ("mimo-v2.5-pro", "mimo-v2-flash"),
            "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
            "zhipu": ("glm-5.1", "glm-5-turbo"),
            "gemini": ("gemini-3.5-flash", "gemini-2.5-flash"),
            "kimi": ("kimi-k2.6", "kimi-k2.5"),
            "xai": ("grok-4.3",),
            "mistral": ("mistral-medium-3-5", "mistral-small-latest"),
            "nvidia": (
                "nvidia/nemotron-3-super-120b-a12b",
                "nvidia/nemotron-3-nano-30b-a3b",
            ),
            "anthropic": ("claude-opus-4-8", "claude-sonnet-4-6"),
        }

        for backend, models in expected_models.items():
            presets = TRANSLATION_MODEL_PRESETS[backend]
            for model in models:
                assert model in presets

        assert TRANSLATION_BACKENDS["qianwen"]["model"] == "qwen-mt-plus"
        assert TRANSLATION_BACKENDS["mistral"]["model"] == "mistral-medium-3-5"
        assert (
            TRANSLATION_BACKENDS["nvidia"]["model"]
            == "nvidia/nemotron-3-super-120b-a12b"
        )
        assert TRANSLATION_BACKENDS["anthropic"]["model"] == "claude-opus-4-8"
        qwen_flash = TRANSLATION_MODEL_PRESETS["qianwen"].index("qwen-mt-flash")
        qwen_plus = TRANSLATION_MODEL_PRESETS["qianwen"].index("qwen-mt-plus")
        assert qwen_plus < qwen_flash
        assert "qwen3.6-max-preview" not in TRANSLATION_MODEL_PRESETS["qianwen"]
        assert "qwen3.6-plus" not in TRANSLATION_MODEL_PRESETS["qianwen"]
        assert "qwen3.6-flash" not in TRANSLATION_MODEL_PRESETS["qianwen"]

    def test_model_profiles_expose_ten_point_live_scores(self):
        qwen_mt = get_backend_model_profile("qianwen", "qwen-mt-plus")
        gpt = get_backend_model_profile("openai", "gpt-5.5")
        opus = get_backend_model_profile("anthropic", "claude-opus-4-8")
        custom = get_backend_model_profile("openai", "custom-router-model")

        assert qwen_mt["score"] == "9.7"
        assert gpt["score"] == "9.5"
        assert opus["score"] == "7.8"
        assert custom["score"] == "6.5"
        assert float(qwen_mt["score"]) > float(opus["score"])

    def test_legacy_provider_defaults_migrate_to_runnable_model_ids(self):
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
        assert config["translation"]["qianwen"]["model"] == "qwen-mt-plus"
        assert (
            config["translation"]["doubao"]["base_url"]
            == "https://ark.cn-beijing.volces.com/api/v3"
        )
        assert config["translation"]["doubao"]["model"] == "doubao-seed-2-0-pro-260215"
        assert (
            config["translation"]["deepseek"]["base_url"] == "https://api.deepseek.com"
        )
        assert config["translation"]["xiaomi"]["model"] == "mimo-v2.5-pro"
        assert config["translation"]["gemini"]["model"] == "gemini-3.5-flash"
        assert (
            config["translation"]["nvidia"]["model"]
            == TRANSLATION_BACKENDS["nvidia"]["model"]
        )

    def test_unroutable_qwen36_models_migrate_to_qwen37(self):
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
        assert config["translation"]["qianwen"]["model"] == "qwen3.7-max"

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
        assert config["translation"]["openai"]["model"] == "gpt-5.5"

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
        assert config["translation"]["openai"]["model"] == "gpt-5.5"

    def test_legacy_anthropic_model_migrates_to_current_default(self):
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
        assert (
            config["translation"]["anthropic"]["model"]
            == TRANSLATION_BACKENDS["anthropic"]["model"]
        )

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


class TestConfigSave(unittest.TestCase):
    """Test configuration saving."""

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
