"""Whisper モデルの同梱配置と初回ダウンロードを管理する。"""

from __future__ import annotations

import pathlib
import threading
from collections.abc import Callable

from src.utils.app_paths import project_root, resource_base_dirs, writable_app_dir

ALLOWED_SIZES = ("base", "small")
_DOWNLOAD_LOCKS = {size: threading.Lock() for size in ALLOWED_SIZES}
_REQUIRED_FILES = ("config.json", "model.bin")

ProgressCallback = Callable[[str], None]


def bundled_models_dirs() -> list[pathlib.Path]:
    """同梱済みモデルを探す候補ディレクトリを返す。"""
    return [base / "models" for base in resource_base_dirs()]


def downloaded_models_dir() -> pathlib.Path:
    """ユーザー単位でダウンロードしたモデルの保存先を返す。"""
    return writable_app_dir() / "models"


def packaging_models_dir() -> pathlib.Path:
    """パッケージ作成前にモデルを置くリポジトリ内ディレクトリを返す。"""
    return project_root() / "models"


def model_dir(size: str) -> pathlib.Path:
    """指定サイズのユーザー向けモデル保存先を返す。"""
    if size not in ALLOWED_SIZES:
        raise ValueError(f"Unsupported Whisper model size: {size}")
    return downloaded_models_dir() / f"whisper-{size}"


def packaging_model_dir(size: str) -> pathlib.Path:
    """指定サイズのパッケージ同梱用モデル保存先を返す。"""
    if size not in ALLOWED_SIZES:
        raise ValueError(f"Unsupported Whisper model size: {size}")
    return packaging_models_dir() / f"whisper-{size}"


def _is_complete_model_dir(path: pathlib.Path) -> bool:
    return path.is_dir() and all((path / filename).exists() for filename in _REQUIRED_FILES)


def _existing_model_path(size: str) -> pathlib.Path | None:
    for models_root in bundled_models_dirs():
        candidate = models_root / f"whisper-{size}"
        if _is_complete_model_dir(candidate):
            return candidate

    downloaded = model_dir(size)
    if _is_complete_model_dir(downloaded):
        return downloaded

    return None


def model_exists(size: str) -> bool:
    """使用可能な同梱モデルまたはダウンロード済みモデルがあるか判定する。"""
    return _existing_model_path(size) is not None


def resolve_model_path(size: str) -> str:
    """利用可能なモデルディレクトリの絶対パスを返す。"""
    path = _existing_model_path(size)
    if path is not None:
        return str(path)
    raise FileNotFoundError(
        f"未找到可用的 Whisper {size} 模型。\n"
        f"程序会在首次点击“开始监听”时自动下载到：{model_dir(size)}"
    )


def _download_model_to(
    size: str,
    target_dir: pathlib.Path,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    try:
        from faster_whisper import download_model
    except ImportError as exc:
        raise RuntimeError("缺少 faster-whisper 依赖，无法下载 Whisper 模型。") from exc

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        if target_dir.exists():
            progress_callback(f"検出した不完全な Whisper {size} モデルを再ダウンロードしています。")
        else:
            progress_callback(f"Whisper {size} モデルをダウンロードしています。")

    try:
        download_model(size, output_dir=str(target_dir))
    except Exception as exc:
        raise RuntimeError(f"Whisper {size} 模型下载失败：{exc}") from exc

    if not _is_complete_model_dir(target_dir):
        raise RuntimeError(f"Whisper {size} 模型下载完成后仍不完整：{target_dir}")

    if progress_callback:
        progress_callback(f"Whisper {size} モデルのダウンロードが完了しました。")

    return target_dir


def ensure_model(size: str, progress_callback: ProgressCallback | None = None) -> pathlib.Path:
    """利用可能なモデルパスを返す。  見つからない場合は LocalAppData 側へダウンロードする。"""
    existing = _existing_model_path(size)
    if existing is not None:
        return existing

    lock = _DOWNLOAD_LOCKS[size]
    with lock:
        existing = _existing_model_path(size)
        if existing is not None:
            return existing
        return _download_model_to(size, model_dir(size), progress_callback=progress_callback)


def ensure_packaging_model(size: str, progress_callback: ProgressCallback | None = None) -> pathlib.Path:
    """パッケージ同梱用のリポジトリ内モデルが揃っていることを保証する。"""
    target = packaging_model_dir(size)
    if _is_complete_model_dir(target):
        return target

    lock = _DOWNLOAD_LOCKS[size]
    with lock:
        if _is_complete_model_dir(target):
            return target
        return _download_model_to(size, target, progress_callback=progress_callback)
