# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Text recognition that ships inside Mio.

Windows' own recognizer was the first choice for its size, and it fails
players in two ways that showed up live: it needs a language pack the player
has to install by hand, and it reads one language at a time - run with the
Japanese pack on a Chinese sign it turned 恭喜 into 《喜 and dropped half the
line. The recognizer here is PaddleOCR's mobile models through ONNX Runtime:
detection and Chinese/English recognition come with the package, Japanese and
Korean recognition ship in ``assets/ocr``. Every detected line is read by
every recognizer that could apply and the most confident reading wins, so a
Chinese poster next to a Japanese sign comes out right without anyone
choosing a language.

Timing measured on a 2560x1511 frame: about 1.8 s on one CPU core, most of it
detection; a framed region reads in a fraction of that.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.core.screen_ocr import (
    OcrLine,
    OcrResult,
    normalize_recognized_text,
    oriented_box_from_quad,
)

logger = logging.getLogger(__name__)

# Recognizers beyond the package's Chinese/English one, keyed by the language
# code used elsewhere in Mio. Files live in assets/ocr; the character list is
# embedded in each model.
EXTRA_RECOGNIZERS = {
    "ja": "japan_PP-OCRv4_rec_mobile.onnx",
    "ko": "korean_PP-OCRv4_rec_mobile.onnx",
}
# A reading below this is noise from a texture or an edge, not text.
MIN_LINE_SCORE = 0.55
# Above this the Chinese/English reading is trusted without asking the other
# recognizers; below it they get a look at the same crop.
CONFIDENT_SCORE = 0.85
# Detection is run at most this wide; the mobile detector gains nothing from
# more pixels and costs a second per megapixel.
MAX_DETECT_WIDTH = 1600


def models_directory() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", "ocr")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "assets", "ocr"))


def local_ocr_available() -> bool:
    """True when the bundled recognizer can be imported (models load lazily)."""

    try:
        import rapidocr_onnxruntime  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


@dataclass
class _Reading:
    text: str
    score: float
    language: str


class LocalOcr:
    """One detector, several recognizers, best reading per line."""

    def __init__(self, *, languages: Iterable[str] = ("zh", "ja", "ko"), gpu: bool | None = None) -> None:
        self._languages = [str(code) for code in languages]
        self._gpu_wanted = _gpu_preference if gpu is None else bool(gpu)
        self._gpu_active = False
        self._engine: Any | None = None
        self._extra: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._load_error = ""

    @property
    def load_error(self) -> str:
        return self._load_error

    # ------------------------------------------------------------ loading
    def _load(self) -> bool:
        if self._engine is not None:
            return True
        if self._load_error:
            return False
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:
            self._load_error = f"import:{type(exc).__name__}"
            logger.info("Bundled OCR unavailable: %s", exc)
            return False
        started = time.perf_counter()
        # DirectML runs the detector on any DirectX 12 GPU: measured 2.5x
        # faster than the CPU on a whole eye frame. It costs a longer first
        # call (shader compilation), which the warm-up thread absorbs. A GPU
        # that refuses falls back to the CPU rather than to no OCR at all.
        options = self._engine_options()
        try:
            self._engine = RapidOCR(**options)
        except Exception as exc:
            if options:
                logger.warning("GPU OCR failed to start (%s); using the CPU", exc)
                options = {}
                self._gpu_active = False
                try:
                    self._engine = RapidOCR()
                except Exception as retry_exc:
                    self._load_error = f"engine:{type(retry_exc).__name__}"
                    logger.warning("Bundled OCR failed to start: %s", retry_exc)
                    return False
            else:
                self._load_error = f"engine:{type(exc).__name__}"
                logger.warning("Bundled OCR failed to start: %s", exc)
                return False
        directory = models_directory()
        for code, filename in EXTRA_RECOGNIZERS.items():
            if code not in self._languages:
                continue
            path = os.path.join(directory, filename)
            if not os.path.isfile(path):
                logger.info("OCR model for %s not bundled at %s", code, path)
                continue
            try:
                # A second engine whose recognizer is the other language; its
                # detector is never used, so it is cheap to keep around.
                self._extra[code] = RapidOCR(rec_model_path=path, **options)
            except Exception:
                logger.warning("OCR model for %s failed to load", code, exc_info=True)
        logger.info(
            "Bundled OCR ready in %.0f ms (%s, extra recognizers: %s)",
            (time.perf_counter() - started) * 1000.0,
            "GPU via DirectML" if self._gpu_active else "CPU",
            ", ".join(sorted(self._extra)) or "none",
        )
        return True

    @property
    def gpu_active(self) -> bool:
        return self._gpu_active

    def _engine_options(self) -> dict:
        """RapidOCR keyword arguments: the DirectML switches when wanted and available."""

        self._gpu_active = False
        if not self._gpu_wanted or not directml_available():
            return {}
        self._gpu_active = True
        return {"det_use_dml": True, "cls_use_dml": True, "rec_use_dml": True}

    def warm_up(self) -> bool:
        """Load the models now so the first recognition is not slow.

        On the GPU the first inference compiles shaders - measured at five
        seconds on a whole frame - so a blank frame of the working size is
        run here, in the warm-up thread, where nobody is waiting on it.
        """

        with self._lock:
            if not self._load():
                return False
            if self._gpu_active and not getattr(self, "_gpu_warmed", False):
                self._gpu_warmed = True
                try:
                    import numpy as np

                    blank = np.zeros((MAX_DETECT_WIDTH * 9 // 16, MAX_DETECT_WIDTH, 3), dtype=np.uint8)
                    started = time.perf_counter()
                    self._engine(blank)
                    for extra in self._extra.values():
                        extra.text_rec([np.zeros((32, 320, 3), dtype=np.uint8)])
                    logger.info("GPU OCR shaders compiled in %.0f ms", (time.perf_counter() - started) * 1000.0)
                except Exception:
                    logger.debug("GPU OCR warm-up inference failed", exc_info=True)
            return True

    # ------------------------------------------------------------ recognition
    def recognize(self, png: bytes, *, language_hint: str = "") -> OcrResult:
        """Read every line in a PNG. Blocking; call it off the UI thread."""

        if not png:
            return OcrResult(error="empty_image")
        with self._lock:
            if not self._load():
                return OcrResult(error=f"local_ocr_unavailable:{self._load_error}")
            try:
                return self._recognize_locked(png, language_hint)
            except Exception as exc:
                logger.debug("Bundled OCR failed", exc_info=True)
                return OcrResult(error=f"ocr_failed:{type(exc).__name__}")

    def _recognize_locked(self, png: bytes, language_hint: str) -> OcrResult:
        import cv2
        import numpy as np

        engine = self._engine
        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return OcrResult(error="empty_image")
        raw_h, raw_w = image.shape[:2]
        scale = 1.0
        if raw_w > MAX_DETECT_WIDTH:
            scale = MAX_DETECT_WIDTH / float(raw_w)
            image = cv2.resize(
                image, (MAX_DETECT_WIDTH, max(1, int(round(raw_h * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        # Replicates RapidOCR.__call__ up to recognition so the same crops can
        # be read by more than one recognizer.
        work, ratio_h, ratio_w = engine.preprocess(image)
        op_record = {"preprocess": {"ratio_h": ratio_h, "ratio_w": ratio_w}}
        work, op_record = engine.maybe_add_letterbox(work, op_record)
        boxes, _det_ms = engine.auto_text_det(work)
        if boxes is None or len(boxes) == 0:
            return OcrResult(lines=[], language=language_hint or "local")
        crops = engine.get_crop_img_list(work, boxes)
        if engine.use_cls:
            crops, _cls_res, _cls_ms = engine.text_cls(crops)

        readings: list[list[_Reading]] = [[] for _ in crops]
        primary, _ms = engine.text_rec(crops)
        for index, (text, score) in enumerate(primary):
            readings[index].append(_Reading(str(text), float(score), "zh"))
        hint = str(language_hint or "").lower()[:2]
        for code, extra in self._extra.items():
            # A second recognizer costs as much as the first. It reads every
            # line when its script is the one being listened to, otherwise
            # only the lines the Chinese/English model was unsure about.
            wanted = [
                index
                for index, (_text, score) in enumerate(primary)
                if code == hint or float(score) < CONFIDENT_SCORE
            ]
            if not wanted:
                continue
            try:
                alternative, _ms = extra.text_rec([crops[index] for index in wanted])
            except Exception:
                logger.debug("Extra recognizer %s failed", code, exc_info=True)
                continue
            for index, (text, score) in zip(wanted, alternative):
                readings[index].append(_Reading(str(text), float(score), code))

        origin_boxes = engine._get_origin_points(boxes, op_record, image.shape[0], image.shape[1])
        lines: list[OcrLine] = []
        for box, candidates in zip(origin_boxes, readings):
            best = self._choose(candidates, language_hint)
            if best is None or best.score < MIN_LINE_SCORE:
                continue
            text = normalize_recognized_text(best.text)
            if not text:
                continue
            # The detector reports four corners; keep the box's own tilt and
            # size rather than the taller upright box around it.
            left, top, width, height, angle = oriented_box_from_quad(box)
            lines.append(
                OcrLine(
                    text=text,
                    left=left / scale,
                    top=top / scale,
                    width=width / scale,
                    height=height / scale,
                    angle=angle,
                )
            )
        return OcrResult(lines=lines, language=language_hint or "local")

    @staticmethod
    def _choose(candidates: list[_Reading], language_hint: str) -> _Reading | None:
        """The most confident reading; a tie goes to the language being listened to."""

        if not candidates:
            return None
        hint = str(language_hint or "").lower()[:2]

        def rank(reading: _Reading) -> tuple[float, int]:
            bonus = 1 if reading.language == hint else 0
            return (round(reading.score, 2), bonus)

        best = max(candidates, key=rank)
        return best if best.text.strip() else None


_shared: LocalOcr | None = None
_shared_lock = threading.Lock()
# Whether the shared engine should use the GPU; the settings page sets this
# before the first warm-up.
_gpu_preference = True


def directml_available() -> bool:
    """True when the installed onnxruntime offers the DirectML provider."""

    try:
        import onnxruntime

        providers = onnxruntime.get_available_providers()
    except Exception:
        return False
    return "DmlExecutionProvider" in list(providers or [])


def set_gpu_preference(enabled: bool) -> None:
    """Choose the GPU for the shared engine. Takes effect at the next load."""

    global _gpu_preference, _shared
    wanted = bool(enabled)
    if wanted == _gpu_preference:
        return
    _gpu_preference = wanted
    with _shared_lock:
        engine = _shared
        if engine is not None and engine.gpu_active != (wanted and directml_available()):
            # Built with the other choice: let the next warm-up rebuild it.
            _shared = None


def shared_local_ocr() -> LocalOcr:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = LocalOcr(gpu=_gpu_preference)
        return _shared


def recognize_png_local(png: bytes, language_tag: str = "", **_ignored) -> OcrResult:
    """Drop-in for :func:`src.core.screen_ocr.recognize_png` using the bundled models."""

    return shared_local_ocr().recognize(png, language_hint=language_tag)
