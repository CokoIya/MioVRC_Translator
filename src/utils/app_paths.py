"""バンドル済みリソースと書き込み可能なアプリデータのパスを管理する  """

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Mio RealTime Translator"


def project_root() -> Path:
    """ソース実行時のリポジトリルートを返す  """
    return Path(__file__).resolve().parents[2]


def resource_base_dirs() -> list[Path]:
    """読み取り専用の同梱リソースを探す候補ディレクトリを返す  """
    if not getattr(sys, "frozen", False):
        return [project_root()]

    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass)
        if meipass_path not in dirs:
            dirs.append(meipass_path)
    return dirs


def writable_app_dir() -> Path:
    """実行時データを書き込むユーザー単位のディレクトリを返す  """
    if not getattr(sys, "frozen", False):
        return project_root()

    override = os.environ.get("MIO_TRANSLATOR_HOME")
    if override:
        return Path(override).expanduser()

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / APP_DIR_NAME

    return Path.home() / ".local" / "share" / APP_DIR_NAME
