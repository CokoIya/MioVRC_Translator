import os
import subprocess

import pytest

import main
from src.utils import gpu_support


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


def test_cuda_cleanup_rejects_symlink_alias_without_deleting_target(
    monkeypatch,
    tmp_path,
):
    app_root = tmp_path / "Mio"
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))
    expected = gpu_support.cuda_runtime_site_packages()
    marker = expected / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    alias = tmp_path / "runtime-alias"
    _make_directory_link(alias, expected)

    try:
        with pytest.raises(RuntimeError, match="unexpected CUDA runtime path"):
            main._clean_cuda_runtime_target(alias)
        assert expected.is_dir()
        assert marker.read_text(encoding="utf-8") == "keep"
    finally:
        _remove_directory_link(alias)


def test_cuda_cleanup_rejects_link_at_exact_target_without_following_it(
    monkeypatch,
    tmp_path,
):
    app_root = tmp_path / "Mio"
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))
    expected = gpu_support.cuda_runtime_site_packages()
    expected.rmdir()
    outside = tmp_path / "outside-runtime"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    _make_directory_link(expected, outside)

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            main._clean_cuda_runtime_target(expected)
        assert marker.read_text(encoding="utf-8") == "keep"
    finally:
        _remove_directory_link(expected)


def test_cuda_runtime_rejects_redirected_writable_path_chain(monkeypatch, tmp_path):
    outside = tmp_path / "outside-home"
    outside.mkdir()
    redirected = tmp_path / "redirected-home"
    _make_directory_link(redirected, outside)
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(redirected / "Mio"))

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            gpu_support.cuda_runtime_site_packages()
        assert not (outside / "Mio" / "runtime_cuda").exists()
    finally:
        _remove_directory_link(redirected)


def test_cuda_install_cache_rejects_redirected_subdirectory(monkeypatch, tmp_path):
    app_root = tmp_path / "Mio"
    app_root.mkdir()
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    redirected_cache = app_root / "runtime_cache"
    _make_directory_link(redirected_cache, outside)
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))

    try:
        with pytest.raises(RuntimeError, match="unsafe writable directory"):
            gpu_support.cuda_install_environment_vars()
        assert list(outside.iterdir()) == []
    finally:
        _remove_directory_link(redirected_cache)


def test_cuda_cleanup_quarantines_old_tree_and_recreates_exact_target(
    monkeypatch,
    tmp_path,
):
    app_root = tmp_path / "Mio"
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(app_root))
    expected = gpu_support.cuda_runtime_site_packages()
    marker = expected / "old" / "marker.txt"
    marker.parent.mkdir()
    marker.write_text("old", encoding="utf-8")

    main._clean_cuda_runtime_target(expected)

    assert expected.is_dir()
    assert not marker.exists()
    assert list(expected.iterdir()) == []
    assert list(expected.parent.glob(".site-packages.cleanup-*")) == []
