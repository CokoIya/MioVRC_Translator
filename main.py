"""Mio RealTime Translator — エントリポイント。"""

import sys
import os

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(__file__))

from src.utils import config_manager
from src.ui.main_window import MainWindow


def main():
    config = config_manager.load_config()
    app = MainWindow(config)
    app.mainloop()


if __name__ == "__main__":
    main()
