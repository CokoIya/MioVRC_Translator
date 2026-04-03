from __future__ import annotations

from dataclasses import dataclass, replace

from src.utils.ui_config import DEFAULT_ASR_ENGINE


@dataclass(frozen=True)
class ASRRuntimeSpec:
    engine: str
    label: str
    config_key: str
    model_id: str
    model_revision: str
    bundled_dir_names: tuple[str, ...] = ()
    required_files: tuple[str, ...] = ()


ASR_ENGINE_SPECS: dict[str, ASRRuntimeSpec] = {
    "sensevoice-small": ASRRuntimeSpec(
        engine="sensevoice-small",
        label="SenseVoice Small",
        config_key="sensevoice",
        model_id="iic/SenseVoiceSmall",
        model_revision="master",
        bundled_dir_names=("sensevoice-small",),
        required_files=("model.pt",),
    ),
}

AVAILABLE_ASR_ENGINES = tuple(ASR_ENGINE_SPECS.keys())


def normalize_asr_engine(engine: str | None) -> str:
    if engine in ASR_ENGINE_SPECS:
        return str(engine)
    return DEFAULT_ASR_ENGINE


def get_asr_engine_spec(engine: str | None) -> ASRRuntimeSpec:
    return ASR_ENGINE_SPECS[normalize_asr_engine(engine)]


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
    model_id = str(engine_cfg.get("model_id", base_spec.model_id)).strip() or base_spec.model_id
    model_revision = (
        str(engine_cfg.get("model_revision", base_spec.model_revision)).strip()
        or base_spec.model_revision
    )
    return replace(
        base_spec,
        model_id=model_id,
        model_revision=model_revision,
    )
