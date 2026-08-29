from src.asr.model_registry import (
    LISTEN_SELECTABLE_ASR_ENGINES,
    USER_SELECTABLE_ASR_ENGINES,
    WHISPER_ASR_DEFAULT_MODEL,
    get_asr_runtime_spec,
    get_qwen3_asr_base_url,
    normalize_qwen3_asr_region,
)


def test_online_asr_specs_do_not_require_local_models():
    config = {
        "asr": {
            "engine": "qwen3-asr",
            "qwen3_asr": {"model": "qwen3-asr-flash-2026-02-10"},
        }
    }

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "qwen3-asr"
    assert spec.model_id == "qwen3-asr-flash-2026-02-10"
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


def test_whisper_asr_spec_remains_internal_but_is_not_user_selectable():
    config = {"asr": {"engine": "whisper-large-v3-turbo"}}

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "whisper-large-v3-turbo"
    assert spec.model_id == WHISPER_ASR_DEFAULT_MODEL
    assert spec.model_revision == "master"
    assert spec.requires_local_model is True
    assert spec.required_files == ("small.en.pb",)
    assert "whisper-large-v3-turbo" not in USER_SELECTABLE_ASR_ENGINES
    assert "whisper-large-v3-turbo" not in LISTEN_SELECTABLE_ASR_ENGINES
