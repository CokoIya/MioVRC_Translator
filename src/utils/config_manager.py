"""設定ファイルの読み書きを管理するユーティリティ。"""

import json
import shutil
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"
EXAMPLE_PATH = Path(__file__).parent.parent.parent / "config.example.json"


def load_config() -> dict:
    """設定ファイルを読み込む。存在しない場合はサンプルからコピーする。"""
    if not CONFIG_PATH.exists():
        if EXAMPLE_PATH.exists():
            shutil.copy(EXAMPLE_PATH, CONFIG_PATH)
        else:
            return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """設定をJSONファイルに書き込む。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get(config: dict, *keys, default=None):
    """ネストされた辞書から安全に値を取得する。"""
    node = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node
