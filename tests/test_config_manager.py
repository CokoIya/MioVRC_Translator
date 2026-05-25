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
from src.utils.ui_config import TRANSLATION_BACKENDS, TRANSLATION_MODEL_PRESETS


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
        config = {
            "translation": {
                "openai": {"api_key": "sk-1234567890"}
            }
        }
        assert config_manager._contains_plaintext_api_key(config) is True

    def test_contains_plaintext_api_key_false_protected(self):
        """Should not flag protected API keys."""
        config = {
            "translation": {
                "openai": {"api_key": "dpapi:v1:base64data"}
            }
        }
        assert config_manager._contains_plaintext_api_key(config) is False

    def test_contains_plaintext_api_key_false_empty(self):
        """Should not flag empty API keys."""
        config = {
            "translation": {
                "openai": {"api_key": ""}
            }
        }
        assert config_manager._contains_plaintext_api_key(config) is False

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

    def test_ensure_tts_config_migrates_existing_output_device(self):
        """Existing non-default output devices should keep VRChat output enabled."""
        config = {"tts": {"output_device": 14}}

        changed = config_manager._ensure_tts_config(config)

        assert changed is True
        assert config["tts"]["output_to_vrchat"] is True
        assert config["tts"]["output_device"] == 14
        assert config["tts"]["output_device_name"] == ""

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
        assert config["asr"]["fallback_engine"] == "sensevoice-small"
        assert config["asr"]["webspeech"]["language"] == "ja-JP"
        assert config["asr"]["qwen3_asr"]["model"] == "qwen3-asr-flash"
        assert config["asr"]["qwen3_asr"]["base_url"] == (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        )
        assert config["asr"]["gemini_live"]["transcribe_only"] is True

    def test_ensure_asr_config_migrates_legacy_qwen3_model(self):
        config = {"asr": {"engine": "qwen3-asr", "qwen3_asr": {"model": "qwen3-asr-0.6b"}}}

        changed = config_manager._ensure_asr_config(config)

        assert changed is True
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
        assert config["translation"]["language_pair_source"] == "manual"

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
            "qianwen": ("qwen3.6-plus", "qwen-mt-plus"),
            "deepseek": ("deepseek-v4-flash", "deepseek-v4-pro"),
            "zhipu": ("glm-5.1", "glm-5-turbo"),
            "gemini": ("gemini-3.1-flash-lite", "gemini-3.1-pro-preview"),
            "kimi": ("kimi-k2.6", "kimi-k2.5"),
            "xai": ("grok-4.3",),
            "mistral": ("mistral-small-latest", "mistral-medium-3-5"),
            "anthropic": ("claude-opus-4-1-20250805", "claude-sonnet-4-20250514"),
        }

        for backend, models in expected_models.items():
            presets = TRANSLATION_MODEL_PRESETS[backend]
            for model in models:
                assert model in presets

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

    def test_legacy_anthropic_model_migrates_to_current_default(self):
        config = {
            "ui": {"language": "zh-CN"},
            "translation": {
                "backend": "anthropic",
                "backend_source": "manual",
                "source_language": "zh",
                "target_language": "ja",
                "language_pair_source": "manual",
                "anthropic": {"model": "claude-haiku-4-5-20251001"},
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
        tmp_root = Path.cwd() / ".tmp" / "tests" / "config_manager"
        tmp_root.mkdir(parents=True, exist_ok=True)
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
