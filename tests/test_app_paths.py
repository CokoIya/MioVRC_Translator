from src.utils import app_paths


def test_writable_app_dir_defaults_to_resource_base(monkeypatch, tmp_path):
    install_root = tmp_path / "install"
    outside_root = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(outside_root))
    monkeypatch.delenv("MIO_TRANSLATOR_HOME", raising=False)
    monkeypatch.setattr(app_paths, "resource_base_dirs", lambda: [install_root])

    assert app_paths.writable_app_dir() == install_root


def test_writable_app_dir_honors_explicit_override(monkeypatch, tmp_path):
    install_root = tmp_path / "install"
    override_root = tmp_path / "override"
    monkeypatch.setenv("MIO_TRANSLATOR_HOME", str(override_root))
    monkeypatch.setattr(app_paths, "resource_base_dirs", lambda: [install_root])

    assert app_paths.writable_app_dir() == override_root


def test_generated_dirs_stay_under_writable_app_dir(monkeypatch, tmp_path):
    install_root = tmp_path / "install"
    monkeypatch.delenv("MIO_TRANSLATOR_HOME", raising=False)
    monkeypatch.setattr(app_paths, "resource_base_dirs", lambda: [install_root])

    temp_dir = app_paths.app_temp_dir()
    backgrounds_dir = app_paths.backgrounds_dir()

    assert temp_dir == install_root / "temp"
    assert backgrounds_dir == install_root / "backgrounds"
    assert temp_dir.is_dir()
    assert backgrounds_dir.is_dir()
