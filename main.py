import os
import sys
import logging

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

    from src.utils.logger import setup_logging
    log_file = setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Application startup requested")
    logger.info("Log file ready at %s", log_file)

    from src.ui.main_window import MainWindow
    from src.utils import config_manager

    try:
        config = config_manager.load_config()
        logger.info("Configuration loaded successfully")
        app = MainWindow(config)
        logger.info("Main window initialized")
        app.mainloop()
        logger.info("Application closed normally")
        return 0
    except Exception:
        logger.exception("Fatal error during application startup/runtime")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
