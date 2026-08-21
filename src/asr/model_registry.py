from __future__ import annotations

from dataclasses import dataclass, replace

from src.utils.ui_config import DEFAULT_ASR_ENGINE

WHISPER_ASR_LEGACY_DEFAULT_MODELS = frozenset({"iic/Whisper-large-v3-turbo"})
WHISPER_ASR_DEFAULT_MODEL = "iic/speech_whisper-small_asr_english"
WHISPER_ASR_DEFAULT_REVISION = "master"
SENSEVOICE_DEFAULT_MODEL = "iic/SenseVoiceSmall"
SENSEVOICE_DEFAULT_REVISION = "70514a3da51f1160f51d18449dab6128bbd4928b"
QWEN3_ASR_DEFAULT_MODEL = "qwen3-asr-flash-2026-02-10"
QWEN3_ASR_MODEL_CHOICES = (
    QWEN3_ASR_DEFAULT_MODEL,
    "qwen3-asr-flash",
)
QWEN3_ASR_DEFAULT_REGION = "singapore"
QWEN3_ASR_REGION_BASE_URLS = {
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    # Alibaba Cloud Model Studio in Tokyo uses a workspace-specific hostname.
    "japan": "",
    "china_mainland": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
QWEN3_ASR_REGION_ALIASES = {
    "intl": "singapore",
    "international": "singapore",
    "sg": "singapore",
    "jp": "japan",
    "japan": "japan",
    "china": "china_mainland",
    "cn": "china_mainland",
    "mainland": "china_mainland",
    "china-mainland": "china_mainland",
}
QWEN3_ASR_LEGACY_MODEL_IDS = frozenset(
    {
        "qwen3-asr-0.6b",
        "qwen3-asr-1.7b",
    }
)
ASR_ENGINE_FOLLOW_MAIN = "same_as_main"


@dataclass(frozen=True)
class ASRRuntimeSpec:
    engine: str
    label: str
    config_key: str
    model_id: str
    model_revision: str
    requires_local_model: bool = True
    bundled_dir_names: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()
    required_file_sha256: tuple[tuple[str, str], ...] = ()
    required_file_sizes: tuple[tuple[str, int], ...] = ()


ASR_ENGINE_SPECS: dict[str, ASRRuntimeSpec] = {
    "sensevoice-small": ASRRuntimeSpec(
        engine="sensevoice-small",
        label="SenseVoice Small",
        config_key="sensevoice",
        model_id=SENSEVOICE_DEFAULT_MODEL,
        model_revision=SENSEVOICE_DEFAULT_REVISION,
        requires_local_model=True,
        bundled_dir_names=("sensevoice-small",),
        required_files=(
            "am.mvn",
            "chn_jpn_yue_eng_ko_spectok.bpe.model",
            "config.yaml",
            "configuration.json",
            "model.pt",
        ),
        required_file_sha256=(
            (
                "am.mvn",
                "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5",
            ),
            (
                "chn_jpn_yue_eng_ko_spectok.bpe.model",
                "aa87f86064c3730d799ddf7af3c04659151102cba548bce325cf06ba4da4e6a8",
            ),
            (
                "config.yaml",
                "f71e239ba36705564b5bf2d2ffd07eece07b8e3f2bbf6d2c99d8df856339ac19",
            ),
            (
                "configuration.json",
                "02810a7f8e9e8aee10370a265f7e799728ce25b4c00cdbf4602b303ee395a38e",
            ),
            (
                "model.pt",
                "833ca2dcfdf8ec91bd4f31cfac36d6124e0c459074d5e909aec9cabe6204a3ea",
            ),
        ),
        required_file_sizes=(
            ("am.mvn", 11_203),
            ("chn_jpn_yue_eng_ko_spectok.bpe.model", 377_341),
            ("config.yaml", 1_855),
            ("configuration.json", 396),
            ("model.pt", 936_291_369),
        ),
    ),
    "whisper-large-v3-turbo": ASRRuntimeSpec(
        engine="whisper-large-v3-turbo",
        label="Whisper Small",
        config_key="whisper",
        model_id=WHISPER_ASR_DEFAULT_MODEL,
        model_revision=WHISPER_ASR_DEFAULT_REVISION,
        requires_local_model=True,
        bundled_dir_names=("whisper-small",),
        required_files=("small.en.pb",),
    ),
    "webspeech": ASRRuntimeSpec(
        engine="webspeech",
        label="Web Speech",
        config_key="webspeech",
        model_id="webspeech",
        model_revision="",
        requires_local_model=False,
    ),
    "qwen3-asr": ASRRuntimeSpec(
        engine="qwen3-asr",
        label="Qwen3-ASR",
        config_key="qwen3_asr",
        model_id=QWEN3_ASR_DEFAULT_MODEL,
        model_revision="",
        requires_local_model=False,
    ),
    "gemini-live": ASRRuntimeSpec(
        engine="gemini-live",
        label="Gemini Live API",
        config_key="gemini_live",
        model_id="gemini-live",
        model_revision="",
        requires_local_model=False,
    ),
}

USER_SELECTABLE_ASR_ENGINES = (
    "webspeech",
    "qwen3-asr",
    "gemini-live",
    "sensevoice-small",
)
LISTEN_SELECTABLE_ASR_ENGINES = (
    ASR_ENGINE_FOLLOW_MAIN,
    *USER_SELECTABLE_ASR_ENGINES,
)
AVAILABLE_ASR_ENGINES = tuple(ASR_ENGINE_SPECS.keys())
ONLINE_ASR_ENGINES = frozenset(
    k for k, spec in ASR_ENGINE_SPECS.items() if not spec.requires_local_model
)
_BUILTIN_MODEL_OWNER = {
    spec.model_id: engine
    for engine, spec in ASR_ENGINE_SPECS.items()
    if spec.requires_local_model
}


def normalize_asr_engine(engine: str | None) -> str:
    if engine in ASR_ENGINE_SPECS:
        return str(engine)
    return DEFAULT_ASR_ENGINE


def get_asr_engine_spec(engine: str | None) -> ASRRuntimeSpec:
    return ASR_ENGINE_SPECS[normalize_asr_engine(engine)]


def normalize_qwen3_asr_region(region: object) -> str:
    text = str(region or "").strip().lower().replace(" ", "_")
    text = QWEN3_ASR_REGION_ALIASES.get(text, text)
    if text in QWEN3_ASR_REGION_BASE_URLS or text == "custom":
        return text
    return QWEN3_ASR_DEFAULT_REGION


def get_qwen3_asr_base_url(region: object) -> str:
    region_code = normalize_qwen3_asr_region(region)
    return QWEN3_ASR_REGION_BASE_URLS.get(region_code, "")


def get_asr_runtime_spec(
    config: dict | None = None,
    engine: str | None = None,
) -> ASRRuntimeSpec:
    resolved_engine = normalize_asr_engine(
        engine or (config or {}).get("asr", {}).get("engine", DEFAULT_ASR_ENGINE)
    )
    base_spec = get_asr_engine_spec(resolved_engine)
    asr_cfg = (config or {}).get("asr", {})
    engine_cfg = asr_cfg.get(base_spec.config_key, {})
    if not isinstance(engine_cfg, dict):
        engine_cfg = {}
    if not base_spec.requires_local_model:
        model_id_key = "model" if "model" in engine_cfg else "model_id"
        model_id = (
            str(engine_cfg.get(model_id_key, base_spec.model_id)).strip()
            or base_spec.model_id
        )
        if base_spec.engine == "qwen3-asr" and model_id in QWEN3_ASR_LEGACY_MODEL_IDS:
            model_id = QWEN3_ASR_DEFAULT_MODEL
        return replace(base_spec, model_id=model_id, required_file_sha256=())
    model_id = (
        str(engine_cfg.get("model_id", base_spec.model_id)).strip()
        or base_spec.model_id
    )
    model_revision = (
        str(engine_cfg.get("model_revision", base_spec.model_revision)).strip()
        or base_spec.model_revision
    )
    if model_id == base_spec.model_id:
        model_revision = base_spec.model_revision
    if (
        base_spec.engine == "whisper-large-v3-turbo"
        and model_id in WHISPER_ASR_LEGACY_DEFAULT_MODELS
    ):
        model_id = base_spec.model_id
        model_revision = base_spec.model_revision
    model_owner = _BUILTIN_MODEL_OWNER.get(model_id)
    if model_owner is not None and model_owner != resolved_engine:
        model_id = base_spec.model_id
        model_revision = base_spec.model_revision
    required_file_sha256 = (
        base_spec.required_file_sha256 if model_id == base_spec.model_id else ()
    )
    return replace(
        base_spec,
        model_id=model_id,
        model_revision=model_revision,
        required_file_sha256=required_file_sha256,
    )
