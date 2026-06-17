# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SpeechPipeline(QObject):
    partial_result = Signal(str)
    final_result = Signal(str, str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._language = "auto"

    def feed_audio(self, audio_segment) -> None:
        return None

    def set_language(self, lang: str) -> None:
        self._language = str(lang or "auto")
