# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""2D codes (QR) in a read picture.

VRHandsFrame reads the QR codes worlds hang on posters and opens their link.
Mio reads them in the same pass as the text: OpenCV is already bundled for
the OCR, and its detector needs nothing else. A code's text comes back as
it is; whether it is a link worth opening is :func:`is_link`'s call.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Larger pictures are shrunk to this side first: a whole-view capture is
# 3000+ px and detection time grows with it, while a code a player can read
# is never that small.
MAX_SIDE = 2048


def decode_codes(png: bytes) -> list[str]:
    """Every QR code's text in the PNG, in reading order, without repeats.

    Never raises; an unreadable picture or a missing OpenCV gives [].
    """

    if not png:
        return []
    try:
        import cv2
        import numpy as np
    except Exception:
        logger.debug("OpenCV unavailable for 2D codes", exc_info=True)
        return []
    try:
        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            return []
        height, width = image.shape[:2]
        side = max(height, width)
        if side > MAX_SIDE:
            factor = MAX_SIDE / float(side)
            image = cv2.resize(image, (max(1, int(width * factor)), max(1, int(height * factor))))
        detector = cv2.QRCodeDetector()
        found: list[tuple[float, float, str]] = []
        ok, texts, points, _ = detector.detectAndDecodeMulti(image)
        if ok and texts is not None:
            for index, text in enumerate(texts):
                if not text:
                    continue
                corner = points[index][0] if points is not None and index < len(points) else (0.0, 0.0)
                found.append((float(corner[1]), float(corner[0]), str(text)))
        if not found:
            text, points, _ = detector.detectAndDecode(image)
            if text:
                found.append((0.0, 0.0, str(text)))
    except Exception:
        logger.debug("2D code detection failed", exc_info=True)
        return []
    codes: list[str] = []
    for _y, _x, text in sorted(found):
        cleaned = text.strip()
        if cleaned and cleaned not in codes:
            codes.append(cleaned)
    return codes


def is_link(text: str) -> bool:
    """An http(s) address a browser can open; nothing else is ever opened."""

    try:
        parsed = urlparse(str(text or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
