import pytest

from src.asr.model_registry import (
    LISTEN_SELECTABLE_ASR_ENGINES,
    QWEN3_ASR_DEFAULT_MODEL,
    QWEN3_ASR_MODEL_CHOICES,
    USER_SELECTABLE_ASR_ENGINES,
    get_asr_runtime_spec,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)


def test_online_asr_specs_do_not_require_local_models():
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        }
    }

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "qwen3-asr"
    assert spec.model_id == "qwen3-asr-flash"
    assert spec.requires_local_model is False


def test_qwen3_latest_flash_alias_remains_selectable():
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        }
    }

    spec = get_asr_runtime_spec(config)

    assert spec.model_id == "qwen3-asr-flash"


def test_qwen3_asr_only_offers_the_stable_flash_alias():
    """Alibaba retired every dated qwen3-asr-flash snapshot on 2026-10-10."""

    assert QWEN3_ASR_MODEL_CHOICES == ("qwen3-asr-flash",)
    assert QWEN3_ASR_DEFAULT_MODEL == "qwen3-asr-flash"


@pytest.mark.parametrize(
    "qwen_cfg",
    [{}, {"model": ""}, {"model": None}, {"model": "   "}],
    ids=["model-missing", "model-empty", "model-null", "model-whitespace"],
)
def test_qwen3_asr_defaults_to_the_stable_flash_alias(qwen_cfg):
    config = {"asr": {"engine": "qwen3-asr", "qwen3_asr": qwen_cfg}}

    spec = get_asr_runtime_spec(config)

    assert spec.model_id == "qwen3-asr-flash"


@pytest.mark.parametrize(
    "retired_model",
    ["qwen3-asr-flash-2026-02-10", "qwen3-asr-flash-2025-09-08"],
)
def test_qwen3_retired_dated_snapshot_resolves_to_the_stable_alias(retired_model):
    """A config saved by an older release must not keep a retired snapshot.

    The dated ids were retired on 2026-10-10, so the runtime spec reports the
    alias even for a config that has not been through the migration yet.
    """

    saved = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": retired_model},
        }
    }
    alias = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash"},
        }
    }

    spec = get_asr_runtime_spec(saved)

    assert spec.engine == "qwen3-asr"
    assert spec.model_id == "qwen3-asr-flash"
    assert spec == get_asr_runtime_spec(alias)
    # Reverse listen passes its own engine while the main engine differs.
    listen_only = {
        "asr": {
            "engine": "edge-stt",
            "qwen3_asr": {"model": retired_model},
        }
    }
    assert get_asr_runtime_spec(listen_only, "qwen3-asr").model_id == (
        "qwen3-asr-flash"
    )


def test_qwen3_region_base_url_helpers():
    assert normalize_qwen3_asr_region("intl") == "singapore"
    assert normalize_qwen3_asr_region("china") == "china_mainland"
    assert normalize_qwen3_asr_region("unknown") == "singapore"
    assert get_qwen3_asr_base_url("singapore") == (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    assert get_qwen3_asr_base_url("china_mainland") == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert get_qwen3_asr_base_url("japan") == ""
    assert get_qwen3_asr_base_url("custom") == ""


def test_unknown_legacy_engine_normalizes_to_the_default_engine():
    """A retired engine id must land somewhere the player can use at once.

    The fallback used to be the local model, which left anyone migrating from
    a removed engine unable to transcribe until a large download finished.
    """

    config = {"asr": {"engine": "legacy-local-asr-large"}}

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "edge-stt"
    assert spec.requires_local_model is False


def test_null_local_model_settings_fall_back_to_the_pinned_model():
    """A saved null must not become the model id or revision "None"."""

    spec = get_asr_runtime_spec(
        {
            "asr": {
                "engine": "sensevoice-small",
                "sensevoice": {"model_id": None, "model_revision": None},
            }
        }
    )

    assert spec.model_id == "iic/SenseVoiceSmall"
    assert spec.model_revision == "70514a3da51f1160f51d18449dab6128bbd4928b"
    assert spec.required_file_sha256


def test_sensevoice_spec_is_pinned_and_all_required_files_are_hashed():
    spec = get_asr_runtime_spec({"asr": {"engine": "sensevoice-small"}})

    assert spec.model_revision == "70514a3da51f1160f51d18449dab6128bbd4928b"
    assert set(spec.required_files) == {
        "am.mvn",
        "chn_jpn_yue_eng_ko_spectok.bpe.model",
        "config.yaml",
        "configuration.json",
        "model.pt",
    }
    assert set(dict(spec.required_file_sha256)) == set(spec.required_files)
    assert set(dict(spec.required_file_sizes)) == set(spec.required_files)


def test_retired_whisper_backend_is_gone_from_the_registry():
    from src.asr.model_registry import ASR_ENGINE_SPECS

    assert "whisper-large-v3-turbo" not in ASR_ENGINE_SPECS
    assert "whisper-large-v3-turbo" not in USER_SELECTABLE_ASR_ENGINES
    assert "whisper-large-v3-turbo" not in LISTEN_SELECTABLE_ASR_ENGINES
