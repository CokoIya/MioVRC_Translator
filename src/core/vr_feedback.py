# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Vibration and sound cues for what the player does in the headset.

VRHandsFrame answers every step of its gesture with a buzz and a short
sound: the frame is ready, it is reading, the result is in. Without them a
player cannot tell whether a gesture was recognised until something appears,
which is why a missed wrist twist looked like a dead panel. Mio had neither.

A cue is a name; what it means (which hands buzz, how hard, which sound) is
the table below. Vibration goes through the controller input (the haptic
action, or the legacy pulse on controllers whose bindings never went active).
Sounds are tiny tones generated once into a cache folder and played
asynchronously by Windows, so no audio stream or device is involved. As in
VRHandsFrame, a player can replace any of them: a WAV file named after the
sound (``ready.wav``, ``done.wav`` ... see :data:`SOUND_NAMES`) in the
custom folder is played instead.
"""

from __future__ import annotations

import io
import logging
import math
import struct
import sys
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

BOTH = ("left", "right")
SAMPLE_RATE = 22050
# The same cue this soon again is one cue (a double-fired edge, a flicker).
REPEAT_GUARD_SECONDS = 0.08

# name -> (hands to buzz, seconds, amplitude, sound or None). ``hand`` in the
# tuple means "the hand that did it", filled in by the caller.
CUES: dict[str, tuple[tuple[str, ...], float, float, str | None]] = {
    "frame_ready": (BOTH, 0.025, 0.35, "ready"),
    "reading": (BOTH, 0.015, 0.2, None),
    "result": (BOTH, 0.04, 0.5, "done"),
    "capture": (BOTH, 0.05, 0.7, "shutter"),
    "error": (BOTH, 0.09, 0.6, "error"),
    "click": (("hand",), 0.015, 0.4, "click"),
    "toggle": (("hand",), 0.035, 0.5, "toggle"),
    "grab": (("hand",), 0.02, 0.45, None),
    "talk_on": (("hand",), 0.02, 0.4, "talk_on"),
    "talk_off": (("hand",), 0.015, 0.25, "talk_off"),
}

# name -> list of (frequency Hz or 0 for a noise burst, seconds)
_TONES: dict[str, tuple[tuple[float, float], ...]] = {
    "ready": ((660.0, 0.05), (880.0, 0.06)),
    "done": ((880.0, 0.06), (1320.0, 0.08)),
    "shutter": ((0.0, 0.035), (1800.0, 0.02)),
    "error": ((330.0, 0.09), (262.0, 0.12)),
    "click": ((1250.0, 0.025),),
    "toggle": ((740.0, 0.05),),
    "talk_on": ((1000.0, 0.04),),
    "talk_off": ((620.0, 0.04),),
}


# The sounds a player can replace, by file name without ".wav".
SOUND_NAMES = tuple(_TONES)


def tone_wav(name: str, volume: float) -> bytes:
    """A short cue as 16-bit mono WAV bytes, faded in and out so it never clicks."""

    parts = _TONES.get(name, ())
    volume = max(0.0, min(1.0, float(volume)))
    frames = bytearray()
    seed = 12345
    for frequency, seconds in parts:
        count = max(1, int(SAMPLE_RATE * seconds))
        fade = max(1, int(SAMPLE_RATE * 0.006))
        for i in range(count):
            envelope = min(1.0, i / fade, (count - 1 - i) / fade)
            if frequency <= 0:
                # A tiny linear congruential generator: deterministic noise.
                seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
                sample = (seed / 0x3FFFFFFF) - 1.0
            else:
                sample = math.sin(2.0 * math.pi * frequency * i / SAMPLE_RATE)
            value = int(max(-1.0, min(1.0, sample * envelope * volume * 0.55)) * 32767)
            frames.extend(struct.pack("<h", value))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return buffer.getvalue()


def _default_player(path: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winsound

        winsound.PlaySound(
            str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
        return True
    except Exception:
        logger.debug("Cue sound failed", exc_info=True)
        return False


class VRFeedback:
    """Plays cues; every call is cheap and never raises."""

    def __init__(
        self,
        pulse: Callable[..., bool] | None,
        *,
        sound_dir: Path | None,
        haptics: bool = True,
        sounds: bool = True,
        volume: float = 0.5,
        player: Callable[[Path], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        custom_dir: Path | None = None,
    ) -> None:
        self._pulse = pulse
        self._sound_dir = Path(sound_dir) if sound_dir is not None else None
        self._custom_dir = Path(custom_dir) if custom_dir is not None else None
        self._haptics = bool(haptics)
        self._sounds = bool(sounds)
        self._volume = max(0.0, min(1.0, float(volume)))
        self._player = player or _default_player
        self._clock = clock
        self._last: dict[str, float] = {}
        self._written: set[str] = set()
        self._lock = threading.Lock()

    def configure(self, *, haptics: bool, sounds: bool, volume: float) -> None:
        self._haptics = bool(haptics)
        self._sounds = bool(sounds)
        self._volume = max(0.0, min(1.0, float(volume)))

    def set_pulse(self, pulse: Callable[..., bool] | None) -> None:
        self._pulse = pulse

    def cue(self, name: str, hand: str | None = None) -> None:
        spec = CUES.get(str(name))
        if spec is None:
            return
        now = self._clock()
        key = f"{name}:{hand or ''}"
        with self._lock:
            if now - self._last.get(key, -1e9) < REPEAT_GUARD_SECONDS:
                return
            self._last[key] = now
        hands, seconds, amplitude, sound = spec
        if self._haptics and callable(self._pulse):
            targets = [hand if target == "hand" else target for target in hands]
            for target in targets:
                if target not in BOTH:
                    continue
                try:
                    self._pulse(target, seconds=seconds, amplitude=amplitude)
                except Exception:
                    logger.debug("Haptic cue failed", exc_info=True)
        if sound and self._sounds and self._volume > 0:
            path = self._sound_path(sound)
            if path is not None:
                self._player(path)

    def _sound_path(self, sound: str) -> Path | None:
        if self._custom_dir is not None:
            custom = self._custom_dir / f"{sound}.wav"
            try:
                if custom.is_file():
                    return custom
            except OSError:
                pass
        if self._sound_dir is None:
            return None
        level = int(round(self._volume * 100))
        path = self._sound_dir / f"mio_cue_{sound}_{level}.wav"
        name = path.name
        if name in self._written and path.exists():
            return path
        try:
            self._sound_dir.mkdir(parents=True, exist_ok=True)
            data = tone_wav(sound, self._volume)
            if not path.exists() or path.stat().st_size != len(data):
                path.write_bytes(data)
            self._written.add(name)
            return path
        except Exception:
            logger.debug("Could not write the cue sound %s", name, exc_info=True)
            return None
