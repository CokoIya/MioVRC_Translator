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
    assert get_qwen3_asr_base_url("custom") == ""


def test_unknown_legacy_engine_normalizes_to_default_local_asr():
    config = {"asr": {"engine": "legacy-local-asr-large"}}

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "sensevoice-small"
    assert spec.model_id == "iic/SenseVoiceSmall"
    assert spec.requires_local_model is True


def test_sensevoice_spec_includes_tokenizer_and_cmvn_files():
    spec = get_asr_runtime_spec({"asr": {"engine": "sensevoice-small"}})

    assert "model.pt" in spec.required_files
    assert "chn_jpn_yue_eng_ko_spectok.bpe.model" in spec.required_files
    assert "am.mvn" in spec.required_files


def test_whisper_asr_spec_is_local_user_selectable_model():
    config = {"asr": {"engine": "whisper-large-v3-turbo"}}

    spec = get_asr_runtime_spec(config)

    assert spec.engine == "whisper-large-v3-turbo"
    assert spec.model_id == WHISPER_ASR_DEFAULT_MODEL
    assert spec.model_revision == "master"
    assert spec.requires_local_model is True
    assert spec.required_files == ("large-v3-turbo.pt",)
    assert "whisper-large-v3-turbo" in USER_SELECTABLE_ASR_ENGINES
    assert "whisper-large-v3-turbo" in LISTEN_SELECTABLE_ASR_ENGINES
