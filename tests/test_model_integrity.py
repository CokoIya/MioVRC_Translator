from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.asr.model_manager import validate_model_configuration, verify_model_integrity
from src.asr.model_registry import ASRRuntimeSpec, get_asr_engine_spec
from src.asr.sensevoice_asr import SenseVoiceASR
from src.asr.sensevoice_model_manager import _resolve_spec


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_spec_files(root: Path, files: dict[str, bytes]) -> ASRRuntimeSpec:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return ASRRuntimeSpec(
        engine="test",
        label="Test",
        config_key="test",
        model_id="example/model",
        model_revision="rev",
        required_files=tuple(files),
        required_file_sha256=tuple((name, _digest(content)) for name, content in files.items()),
    )


def test_trusted_hash_is_required_when_present(tmp_path):
    spec = _write_spec_files(tmp_path, {"model.pt": b"trusted model"})

    assert verify_model_integrity(tmp_path, spec)
    (tmp_path / "model.pt").write_bytes(b"tampered model")
    assert not verify_model_integrity(tmp_path, spec)


def test_trusted_hashes_must_cover_every_required_file(tmp_path):
    (tmp_path / "model.pt").write_bytes(b"trusted model")
    (tmp_path / "support.txt").write_bytes(b"support")
    spec = ASRRuntimeSpec(
        engine="test",
        label="Test",
        config_key="test",
        model_id="example/model",
        model_revision="rev",
        required_files=("model.pt", "support.txt"),
        required_file_sha256=(("model.pt", _digest(b"trusted model")),),
    )

    assert not verify_model_integrity(tmp_path, spec)


def test_metadata_cannot_override_a_trusted_hash(tmp_path):
    spec = _write_spec_files(tmp_path, {"model.pt": b"trusted model"})
    (tmp_path / "model.pt").write_bytes(b"attacker model")
    (tmp_path / ".mio-model.json").write_text(
        json.dumps({"required_file_sha256": {"model.pt": _digest(b"attacker model")}}),
        encoding="utf-8",
    )

    assert not verify_model_integrity(tmp_path, spec)


def test_sensevoice_registry_hashes_exactly_cover_required_files():
    spec = get_asr_engine_spec("sensevoice-small")

    assert set(dict(spec.required_file_sha256)) == set(spec.required_files)
    assert set(dict(spec.required_file_sizes)) == set(spec.required_files)
    assert len(spec.required_files) == 5
    assert spec.model_revision == "70514a3da51f1160f51d18449dab6128bbd4928b"
    assert SenseVoiceASR()._runtime_spec().required_file_sha256
    assert _resolve_spec("iic/SenseVoiceSmall").required_file_sha256


@pytest.mark.parametrize(
    "configuration",
    [
        {"remote_code": True},
        {"custom-code": "plugin"},
        {"module": "attacker.module"},
        {"nested": {"script": "payload.py"}},
        {"path": "../outside/model.pt"},
        {"path": "C:\\Windows\\payload.py"},
        {"url": "https://attacker.invalid/model.py"},
        {"factory": "attacker.module:Factory"},
    ],
)
def test_json_configuration_rejects_remote_code_and_unsafe_references(
    tmp_path, configuration
):
    (tmp_path / "configuration.json").write_text(
        json.dumps(configuration), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        validate_model_configuration(tmp_path)


def test_yaml_safe_load_rejects_python_tags(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "payload: !!python/object/apply:os.system ['echo unsafe']\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        validate_model_configuration(tmp_path)


def test_benign_sensevoice_style_configuration_is_allowed(tmp_path):
    (tmp_path / "configuration.json").write_text(
        json.dumps(
            {
                "framework": "pytorch",
                "model": {"type": "funasr"},
                "file_path_metas": {"init_param": "model.pt", "config": "config.yaml"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "encoder: SenseVoiceEncoderSmall\nmodel: SenseVoiceSmall\n",
        encoding="utf-8",
    )

    validate_model_configuration(tmp_path)


def test_required_file_symlink_is_rejected(tmp_path):
    target = tmp_path / "real-model.pt"
    target.write_bytes(b"trusted")
    link = tmp_path / "model.pt"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")
    spec = ASRRuntimeSpec(
        engine="test",
        label="Test",
        config_key="test",
        model_id="example/model",
        model_revision="rev",
        required_files=("model.pt",),
        required_file_sha256=(("model.pt", _digest(b"trusted")),),
    )

    assert not verify_model_integrity(tmp_path, spec)



def test_sensevoice_disables_remote_code_before_automodel(monkeypatch, tmp_path):
    from src.asr import sensevoice_asr

    captured: dict[str, object] = {}

    def fake_auto_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        sensevoice_asr,
        "_load_runtime_symbols",
        lambda: (fake_auto_model, lambda text: text),
    )
    monkeypatch.setattr(
        sensevoice_asr, "existing_model_path", lambda _spec: tmp_path
    )

    sensevoice_asr.SenseVoiceASR().load()

    assert captured["trust_remote_code"] is False
    assert captured["check_latest"] is False
    assert captured["disable_update"] is True
