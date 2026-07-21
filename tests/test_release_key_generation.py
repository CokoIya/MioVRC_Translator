from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path

import pytest

from src.updater.manifest_signature import public_key_from_seed


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_release_signing_script():
    path = REPO_ROOT / "scripts" / "update_manifest_signature.py"
    spec = importlib.util.spec_from_file_location("release_signing_script_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_directory(module, path: Path) -> None:
    path.mkdir()
    if os.name == "nt":
        module._protect_windows_path(path)
    else:
        path.chmod(0o700)


def test_generate_key_requires_external_secure_nonexisting_seed_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_release_signing_script()
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    secure = tmp_path / "secure"
    _secure_directory(module, secure)
    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)

    seed_path = secure / "release.seed.hex"
    public_path = secure / "release.public.hex"
    arguments = argparse.Namespace(
        seed_out=str(seed_path),
        public_out=str(public_path),
    )

    assert module._generate_key(arguments) == 0
    seed = seed_path.read_text(encoding="ascii").strip()
    public_key = public_path.read_text(encoding="ascii").strip()
    captured = capsys.readouterr()
    assert len(seed) == 64
    assert public_key_from_seed(seed) == public_key
    assert seed not in captured.out + captured.err

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        module._generate_key(arguments)


def test_generate_key_rejects_seed_path_inside_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_release_signing_script()
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)

    with pytest.raises(RuntimeError, match="outside the repository"):
        module._generate_key(
            argparse.Namespace(
                seed_out=str(fake_repo / "release.seed.hex"),
                public_out="",
            )
        )


def test_generate_key_rejects_broad_private_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_release_signing_script()
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    broad = tmp_path / "broad"
    broad.mkdir()
    if os.name != "nt":
        broad.chmod(0o755)
    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)

    with pytest.raises(RuntimeError, match="Private seed directory"):
        module._generate_key(
            argparse.Namespace(
                seed_out=str(broad / "release.seed.hex"),
                public_out="",
            )
        )
