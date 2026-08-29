from __future__ import annotations

from pathlib import Path

import pytest

from tools import verify_release_bundle as verifier


def _bundle(tmp_path: Path, *, complete: bool = True) -> Path:
    bundle = tmp_path / "MioTranslator"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "MioTranslator.exe").write_bytes(b"exe")
    (bundle / "LICENSE").write_text("license", encoding="utf-8")
    (bundle / "NOTICE").write_text("notice", encoding="utf-8")
    if complete:
        (bundle / "_internal" / "config.example.json").write_text("{}", encoding="utf-8")
        (bundle / "_internal" / "assets").mkdir()
        for source_relative, bundle_relative in verifier.CONDITIONAL_PAYLOAD.items():
            if not (verifier.ROOT / source_relative).is_file():
                continue
            target = bundle / bundle_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"model")
    return bundle


def _spec(tmp_path: Path, excludes: list[str]) -> Path:
    spec = tmp_path / "Fake.spec"
    body = ",\n    ".join(repr(name) for name in excludes)
    spec.write_text(
        "a = 1\nexcludes = [\n    %s,\n]\n" % body,
        encoding="utf-8",
    )
    return spec


def test_reads_the_excluded_packages_out_of_the_real_spec():
    """The forbidden list must stay tied to the spec, not a second copy."""

    excludes = verifier._spec_excludes(verifier.ROOT / "MioTranslator.spec")

    assert "edge_tts" in excludes
    assert "gtts" in excludes
    assert "pyttsx3" in excludes
    assert "torchvision" in excludes


def test_complete_bundle_reports_no_missing_payload(tmp_path):
    assert verifier._missing_payload(_bundle(tmp_path)) == []


def test_missing_data_file_is_reported(tmp_path):
    """A data file that quietly stops being collected must fail the build."""

    problems = verifier._missing_payload(_bundle(tmp_path, complete=False))

    assert any("config.example.json" in problem for problem in problems)
    assert any("assets" in problem for problem in problems)


def test_a_deleted_engine_still_in_the_bundle_is_caught(tmp_path):
    """The exact failure this script exists for.

    An engine is removed from the source tree, but a stale pin keeps the
    package installed and PyInstaller keeps collecting it.
    """

    bundle = _bundle(tmp_path)
    stale = bundle / "_internal" / "edge_tts"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text("", encoding="utf-8")

    hits = verifier._forbidden_hits(bundle, ["edge_tts", "gtts"])

    assert len(hits) == 1
    assert hits[0].startswith("edge_tts: 1 file")


def test_excluded_name_does_not_match_a_substring_of_a_library(tmp_path):
    """"av" must not flag avcodec.dll, or the check gets muted as noise."""

    bundle = _bundle(tmp_path)
    (bundle / "_internal" / "avcodec-61.dll").write_bytes(b"dll")
    (bundle / "_internal" / "gtts_helper.py").write_text("", encoding="utf-8")

    assert verifier._forbidden_hits(bundle, ["av", "gtts"]) == []


def test_our_own_tts_package_does_not_look_like_coqui(tmp_path):
    """src/tts must not trip the "TTS" rule on a case-insensitive filesystem.

    Muting the rule instead would have retired the single most valuable check
    in this script.
    """

    bundle = _bundle(tmp_path)
    own_package = bundle / "_internal" / "src" / "tts"
    own_package.mkdir(parents=True)
    (own_package / "manager.py").write_text("", encoding="utf-8")

    assert verifier._forbidden_hits(bundle, ["TTS"]) == []


def test_coqui_itself_is_still_caught_next_to_our_package(tmp_path):
    bundle = _bundle(tmp_path)
    own_package = bundle / "_internal" / "src" / "tts"
    own_package.mkdir(parents=True)
    (own_package / "manager.py").write_text("", encoding="utf-8")
    coqui = bundle / "_internal" / "TTS"
    coqui.mkdir()
    (coqui / "api.py").write_text("", encoding="utf-8")

    hits = verifier._forbidden_hits(bundle, ["TTS"])

    assert len(hits) == 1
    assert hits[0].startswith("TTS: 1 file")


def test_source_hidden_imports_come_from_the_spec():
    modules = verifier._spec_source_hiddenimports(verifier.ROOT / "MioTranslator.spec")

    assert "src.asr.edge_stt_asr" in modules
    assert "src.ui_qt.floating_window" in modules
    assert all(name.startswith("src.") for name in modules)


def test_every_pinned_source_module_exists_in_the_tree():
    """A spec entry for a module that was deleted would freeze silently."""

    missing = []
    for name in verifier._spec_source_hiddenimports(verifier.ROOT / "MioTranslator.spec"):
        relative = Path(*name.split(".")).with_suffix(".py")
        package = Path(*name.split(".")) / "__init__.py"
        if not (verifier.ROOT / relative).is_file() and not (verifier.ROOT / package).is_file():
            missing.append(name)

    assert missing == []


def test_missing_bundle_directory_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["verify_release_bundle.py", "--bundle", str(tmp_path / "absent")],
    )

    assert verifier.main() == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize("relative", ["MioTranslator.exe", "LICENSE", "NOTICE"])
def test_each_required_payload_entry_is_enforced(tmp_path, relative):
    bundle = _bundle(tmp_path)
    (bundle / relative).unlink()

    problems = verifier._missing_payload(bundle)

    assert any(relative in problem for problem in problems)


def test_a_nested_directory_sharing_a_name_is_not_coqui(tmp_path):
    """The real build tripped on these two before the matcher was narrowed.

    modelscope ships models/audio/tts and the app ships assets/tts; neither is
    the Coqui package the spec excludes.
    """

    bundle = _bundle(tmp_path)
    nested = bundle / "_internal" / "modelscope" / "models" / "audio" / "tts"
    nested.mkdir(parents=True)
    (nested / "voice.py").write_text("", encoding="utf-8")
    asset = bundle / "_internal" / "assets" / "tts"
    asset.mkdir(parents=True)
    (asset / "catalog.json").write_text("{}", encoding="utf-8")

    assert verifier._forbidden_hits(bundle, ["TTS"]) == []


def test_a_dotted_exclusion_matches_its_submodule(tmp_path):
    """PySide6.QtWebEngineCore must match inside PySide6, not at the root."""

    bundle = _bundle(tmp_path)
    qt = bundle / "_internal" / "PySide6"
    qt.mkdir(parents=True)
    (qt / "QtWebEngineCore.cp311-win_amd64.pyd").write_bytes(b"x")
    (qt / "QtCore.cp311-win_amd64.pyd").write_bytes(b"x")

    hits = verifier._forbidden_hits(bundle, ["PySide6.QtWebEngineCore"])

    assert len(hits) == 1
    assert hits[0].startswith("PySide6.QtWebEngineCore: 1 file")


def test_a_dotted_exclusion_ignores_a_same_named_top_level_directory(tmp_path):
    bundle = _bundle(tmp_path)
    stray = bundle / "_internal" / "QtWebEngineCore"
    stray.mkdir(parents=True)
    (stray / "data.bin").write_bytes(b"x")

    assert verifier._forbidden_hits(bundle, ["PySide6.QtWebEngineCore"]) == []
