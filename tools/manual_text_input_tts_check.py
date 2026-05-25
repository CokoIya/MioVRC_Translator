"""Manual check for text-input TTS playback to the configured output device."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tts.manager import TTSManager, resolve_output_device  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

setup_logging()
logger = get_logger(__name__)


def run_check() -> bool:
    config_path = REPO_ROOT / "config.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    tts_config = config.get("tts", {})
    output_device = tts_config.get("output_device")
    output_device_name = str(tts_config.get("output_device_name") or "").strip()
    output_to_vrchat = tts_config.get("output_to_vrchat")
    if output_to_vrchat is None:
        output_to_vrchat = output_device is not None and output_device != -1
    engine = tts_config.get("engine", "edge")
    resolved_device = resolve_output_device(
        output_device,
        output_device_name,
        prefer_virtual=bool(output_to_vrchat),
    )

    print(f"TTS Engine: {engine}")
    print(f"Output to VRChat: {bool(output_to_vrchat)}")
    print(f"Configured Output Device: {output_device}")
    print(f"Configured Output Device Name: {output_device_name or '(none)'}")
    print(f"Resolved Output Device: {resolved_device or '(system default)'}")

    manager = TTSManager(
        engine_name=engine,
        cache_enabled=True,
        allow_fallback=False,
        output_device=output_device,
        output_device_name=output_device_name,
        prefer_virtual_output=bool(output_to_vrchat),
    )
    if not manager.is_available():
        print(f"ERROR: TTS engine '{engine}' is not available")
        return False

    manager.start()
    engine_config = tts_config.get(engine, {})
    voice = engine_config.get("voice")
    rate = engine_config.get("rate", 1.0)
    volume = engine_config.get("volume", 0.8)
    text = "This is a manual TTS output check for Mio Translator."

    print(f"Voice: {voice}")
    print(f"Rate: {rate}")
    print(f"Volume: {volume}")
    print(f"\nPlaying text: {text}")

    success = False
    error_msg = ""

    def callback(ok: bool, msg: str) -> None:
        nonlocal success, error_msg
        success = ok
        error_msg = msg

    accepted = manager.speak(text, voice, rate, volume, callback=callback)
    if not accepted:
        print("ERROR: TTS request was not accepted")
        return False

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if success or error_msg:
            break
        time.sleep(0.1)

    if error_msg:
        print(f"ERROR: {error_msg}")
        return False
    if success:
        print("SUCCESS: TTS playback completed")
        return True

    print("TIMEOUT: TTS playback did not complete")
    return False


if __name__ == "__main__":
    try:
        sys.exit(0 if run_check() else 1)
    except Exception as exc:
        print(f"EXCEPTION: {exc}")
        logger.exception("Manual TTS check failed")
        sys.exit(1)
