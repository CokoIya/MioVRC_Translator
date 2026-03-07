"""Mio RealTime Translator のエントリーポイント。"""

import os
import sys

# プロジェクトルートを `sys.path` に追加する。
sys.path.insert(0, os.path.dirname(__file__))

from src.ui.main_window import MainWindow
from src.utils import config_manager


def main():
    config = config_manager.load_config()
    app = MainWindow(config)
    app.mainloop()


if __name__ == "__main__":
    main()
