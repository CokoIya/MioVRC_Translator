import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

def _run_selftest() -> int:
    from src.asr.sensevoice_asr import validate_runtime_dependencies

    ok, message = validate_runtime_dependencies()
    output = sys.stdout if ok else sys.stderr
    print(message, file=output)
    return 0 if ok else 1


def main() -> int:
    if os.environ.get("MIO_TRANSLATOR_SELFTEST") == "1":
        return _run_selftest()

    from src.utils.app_paths import writable_app_dir
    from src.utils.logger import get_logger, setup_logging

    log_file = setup_logging(log_dir=writable_app_dir() / "logs")
    logger = get_logger("main")
    if log_file:
        print(f"[MioTranslator] Log file: {log_file}", flush=True)
    logger.info("MioTranslator starting. Python %s, platform %s", sys.version, sys.platform)

    from src.ui.main_window import MainWindow
    from src.utils import config_manager

    config = config_manager.load_config()
    app = MainWindow(config)
    app.mainloop()
    logger.info("MioTranslator exiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

