import os
import subprocess

import pytest

from src.runtime_hooks import pyi_rth_bundle_paths
from src.utils import app_paths


@pytest.fixture(autouse=True)
def _reset_migration_state():
    app_paths._MIGRATED_DESTINATIONS.clear()
    app_paths._MIGRATION_ATTEMPTED_DESTINATIONS.clear()
    yield
    app_paths._MIGRATED_DESTINATIONS.clear()
    app_paths._MIGRATION_ATTEMPTED_DESTINATIONS.clear()


def _configure_default(monkeypatch, tmp_path):
    data_root = tmp_path / "local-app-data"
    legacy_root = tmp_path / "legacy-install"
    legacy_root.mkdir()
    monkeypatch.delenv("MIO_TRANSLATOR_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(data_root))
    monkeypatch.setattr(app_paths, "_legacy_writable_app_dir", lambda: legacy_root)
    return data_root / "Mio RealTime Translator", legacy_root


def _make_directory_link(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("directory symlink and junction creation are unavailable")


def _remove_directory_link(link):
    if not os.path.lexists(link):
        return
    if os.name == "nt" and not link.is_symlink():
        os.rmdir(link)
    else:
        link.unlink()


def test_writable_app_dir_defaults_to_per_user_local_app_data(monkeypatch, tmp_path):
    destination, _legacy = _configure_default(monkeypatch, tmp_path)

    assert app_paths.writable_app_dir() == destination
    assert destination.is_dir()
    assert (destination / app_paths._MIGRATION_MARKER_NAME).is_file()


def test_runtime_hook_uses_identical_per_user_default(monkeypatch, tmp_path):
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.delenv("MIO_TRANSLATOR_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert pyi_rth_bundle_paths._writable_app_dir() == local_app_data / "Mio RealTime Translator"


def test_runtime_hook_activates_only_real_cuda_directory_chains(monkeypatch, tmp_path):
    app_root = tmp_path / "app-data"
    site_packages = app_root / "runtime_cuda" / "site-packages"
    torch_lib = site_packages / "torch" / "lib"
    torchaudio_lib = site_packages / "torchaudio" / "lib"
    torch_lib.mkdir(parents=True)
    torchaudio_lib.mkdir(parents=True)
    runtime_dirs = []
    isolated_sys_path = ["existing-path"]
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))
    monkeypatch.setattr(pyi_rth_bundle_paths.sys, "path", isolated_sys_path)
    monkeypatch.setattr(pyi_rth_bundle_paths, "_add_runtime_dir", runtime_dirs.append)

    pyi_rth_bundle_paths._activate_external_cuda_runtime()

    assert isolated_sys_path == [str(site_packages), "existing-path"]
    assert runtime_dirs == [site_packages, torch_lib, torchaudio_lib]


def test_runtime_hook_rejects_redirected_writable_root(monkeypatch, tmp_path):
    outside = tmp_path / "outside-root"
    site_packages = outside / "app-data" / "runtime_cuda" / "site-packages"
    site_packages.mkdir(parents=True)
    redirected_root = tmp_path / "redirected-root"
    _make_directory_link(redirected_root, outside)
    configured_site_packages = (
        redirected_root / "app-data" / "runtime_cuda" / "site-packages"
    )
    runtime_dirs = []
    isolated_sys_path = ["existing-path"]
    monkeypatch.setenv(
        "MIO_TRANSLATOR_HOME",
        str(redirected_root / "app-data"),
    )
    monkeypatch.setattr(pyi_rth_bundle_paths.sys, "path", isolated_sys_path)
    monkeypatch.setattr(pyi_rth_bundle_paths, "_add_runtime_dir", runtime_dirs.append)

    try:
        pyi_rth_bundle_paths._activate_external_cuda_runtime()

        assert str(configured_site_packages) not in isolated_sys_path
        assert isolated_sys_path == ["existing-path"]
        assert runtime_dirs == []
    finally:
        _remove_directory_link(redirected_root)


def test_runtime_hook_rejects_redirected_cuda_site_packages(monkeypatch, tmp_path):
    app_root = tmp_path / "app-data"
    runtime_root = app_root / "runtime_cuda"
    runtime_root.mkdir(parents=True)
    outside_site_packages = tmp_path / "outside-site-packages"
    (outside_site_packages / "torch" / "lib").mkdir(parents=True)
    site_packages = runtime_root / "site-packages"
    _make_directory_link(site_packages, outside_site_packages)
    runtime_dirs = []
    isolated_sys_path = ["existing-path"]
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))
    monkeypatch.setattr(pyi_rth_bundle_paths.sys, "path", isolated_sys_path)
    monkeypatch.setattr(pyi_rth_bundle_paths, "_add_runtime_dir", runtime_dirs.append)

    try:
        pyi_rth_bundle_paths._activate_external_cuda_runtime()

        assert str(site_packages) not in isolated_sys_path
        assert isolated_sys_path == ["existing-path"]
        assert runtime_dirs == []
    finally:
        _remove_directory_link(site_packages)

def test_writable_app_dir_honors_override_and_skips_migration(monkeypatch, tmp_path):
    override_root = tmp_path / "override"
    migrations = []
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(override_root))
    monkeypatch.setattr(app_paths, "_migrate_legacy_data", migrations.append)

    assert app_paths.writable_app_dir() == override_root
    assert migrations == []
    assert override_root.is_dir()


def test_legacy_migration_merges_allowlisted_data_without_overwrite(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("legacy-config", encoding="utf-8")
    (legacy / "seren.json").write_text("legacy-seren", encoding="utf-8")
    (legacy / "not-allowlisted.txt").write_text("no", encoding="utf-8")
    (legacy / "dictionaries" / "nested").mkdir(parents=True)
    (legacy / "dictionaries" / "existing.txt").write_text("legacy", encoding="utf-8")
    (legacy / "dictionaries" / "nested" / "new.txt").write_text("new", encoding="utf-8")

    (destination / "dictionaries").mkdir(parents=True)
    (destination / "config.json").write_text("current-config", encoding="utf-8")
    (destination / "dictionaries" / "existing.txt").write_text("current", encoding="utf-8")

    assert app_paths.writable_app_dir() == destination

    assert (destination / "config.json").read_text(encoding="utf-8") == "current-config"
    assert (destination / "seren.json").read_text(encoding="utf-8") == "legacy-seren"
    assert (destination / "dictionaries" / "existing.txt").read_text(encoding="utf-8") == "current"
    assert (destination / "dictionaries" / "nested" / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (destination / "not-allowlisted.txt").exists()


def test_migration_done_marker_makes_migration_idempotent(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    (legacy / "seren.json").write_text("first", encoding="utf-8")

    app_paths.writable_app_dir()
    (legacy / "catalog_cache.json").write_text("late", encoding="utf-8")
    app_paths._MIGRATED_DESTINATIONS.clear()
    app_paths.writable_app_dir()

    assert (destination / "seren.json").read_text(encoding="utf-8") == "first"
    assert not (destination / "catalog_cache.json").exists()


def test_incomplete_migration_is_not_retried_on_every_path_lookup(
    monkeypatch,
    tmp_path,
):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    (legacy / "runtime_cache").mkdir()
    attempts = []

    def fail_merge(source, target):
        attempts.append((source, target))
        return False

    monkeypatch.setattr(app_paths, "_merge_missing_directory", fail_merge)

    assert app_paths.writable_app_dir() == destination
    assert app_paths.writable_app_dir() == destination

    assert len(attempts) == len(app_paths._MIGRATION_DIRS)
    assert not (destination / app_paths._MIGRATION_MARKER_NAME).exists()


def test_migration_skips_disposable_bytecode_cache_trees(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    cache_root = legacy / "runtime_cache"
    (cache_root / "pycache" / "very" / "deep").mkdir(parents=True)
    (cache_root / "pycache" / "very" / "deep" / "module.pyc").write_bytes(
        b"bytecode"
    )
    (cache_root / "kept").mkdir()
    (cache_root / "kept" / "runtime.bin").write_bytes(b"runtime")
    (cache_root / "kept" / "ignored.pyo").write_bytes(b"bytecode")

    assert app_paths.writable_app_dir() == destination

    migrated_cache = destination / "runtime_cache"
    assert (migrated_cache / "kept" / "runtime.bin").read_bytes() == b"runtime"
    assert not (migrated_cache / "kept" / "ignored.pyo").exists()
    assert not (migrated_cache / "pycache").exists()
    assert (destination / app_paths._MIGRATION_MARKER_NAME).is_file()


def test_migration_never_follows_source_symlinks(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    outside_file = tmp_path / "outside.json"
    outside_file.write_text("secret", encoding="utf-8")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside_file, legacy / "config.json")
        os.symlink(outside_dir, legacy / "dictionaries", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    app_paths.writable_app_dir()

    assert not (destination / "config.json").exists()
    assert not (destination / "dictionaries" / "secret.txt").exists()


def test_migration_rejects_redirected_destination_ancestor(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    (legacy / "config.json").write_text("secret", encoding="utf-8")
    outside = tmp_path / "outside-destination-root"
    outside.mkdir()
    redirected_ancestor = destination.parent
    _make_directory_link(redirected_ancestor, outside)

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            app_paths.writable_app_dir()
        assert not (outside / destination.name / "config.json").exists()
        assert not (outside / destination.name / app_paths._MIGRATION_MARKER_NAME).exists()
    finally:
        _remove_directory_link(redirected_ancestor)


def test_migration_does_not_traverse_destination_symlink(monkeypatch, tmp_path):
    destination, legacy = _configure_default(monkeypatch, tmp_path)
    (legacy / "dictionaries").mkdir()
    (legacy / "dictionaries" / "new.txt").write_text("new", encoding="utf-8")
    outside = tmp_path / "outside-destination"
    outside.mkdir()
    destination.mkdir(parents=True)
    try:
        os.symlink(outside, destination / "dictionaries", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    app_paths.writable_app_dir()

    assert not (outside / "new.txt").exists()
    assert not (destination / app_paths._MIGRATION_MARKER_NAME).exists()


def test_generated_dirs_reject_redirected_override_ancestor(monkeypatch, tmp_path):
    outside = tmp_path / "outside-override"
    outside.mkdir()
    redirected_ancestor = tmp_path / "redirected-override"
    _make_directory_link(redirected_ancestor, outside)
    monkeypatch.setenv(
        "MIO_TRANSLATOR_HOME",
        str(redirected_ancestor / "app-data"),
    )

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            app_paths.app_temp_dir()
        assert not (outside / "app-data" / "temp").exists()
    finally:
        _remove_directory_link(redirected_ancestor)


def test_generated_dirs_stay_under_per_user_writable_dir(monkeypatch, tmp_path):
    destination, _legacy = _configure_default(monkeypatch, tmp_path)

    temp_dir = app_paths.app_temp_dir()
    backgrounds_dir = app_paths.backgrounds_dir()

    assert temp_dir == destination / "temp"
    assert backgrounds_dir == destination / "backgrounds"
    assert temp_dir.is_dir()
    assert backgrounds_dir.is_dir()


def test_writable_app_dir_rejects_redirected_override_directly(monkeypatch, tmp_path):
    outside = tmp_path / "outside-home"
    outside.mkdir()
    redirected = tmp_path / "redirected-home"
    _make_directory_link(redirected, outside)
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(redirected / "app-data"))

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            app_paths.writable_app_dir()
        assert not (outside / "app-data").exists()
    finally:
        _remove_directory_link(redirected)


def test_secure_file_path_rejects_hardlinked_file(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    candidate = tmp_path / "state" / "config.json"
    candidate.parent.mkdir()
    try:
        os.link(outside, candidate)
    except (OSError, NotImplementedError):
        pytest.skip("hard-link creation is unavailable")

    with pytest.raises(RuntimeError, match="unsafe writable file"):
        app_paths.secure_file_path(candidate)


def test_atomic_write_rejects_redirected_parent(tmp_path):
    outside = tmp_path / "outside-state"
    outside.mkdir()
    redirected = tmp_path / "redirected-state"
    _make_directory_link(redirected, outside)

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            app_paths.atomic_write_text(redirected / "cache.json", "blocked")
        assert not (outside / "cache.json").exists()
    finally:
        _remove_directory_link(redirected)


def test_atomic_write_replaces_regular_file_without_partial_temp(tmp_path):
    target = tmp_path / "state" / "cache.json"
    app_paths.atomic_write_text(target, "first")
    app_paths.atomic_write_text(target, "second")

    assert app_paths.read_secure_text(target) == "second"
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_secure_unlink_expected_stat_refuses_replacement_file(tmp_path):
    target = tmp_path / "temporary.bin"
    target.write_bytes(b"original")
    expected_stat = os.lstat(target)

    target.unlink()
    target.write_bytes(b"replacement")

    with pytest.raises(RuntimeError, match="replaced before removal"):
        app_paths.secure_unlink(target, expected_stat=expected_stat)

    assert target.read_bytes() == b"replacement"
