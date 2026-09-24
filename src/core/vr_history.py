# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""What the player read in the headset, kept for this session.

VRHandsFrame keeps captures as quick memos on the wrist and brings one back
with a point and a pull; OVR Overlay Translator pins one to each wrist. Mio
keeps the last reads here - each one's picture with its translations, the
plain picture and the text pairs - so the wrist panel can bring any of them
back as a panel. A pinned read is kept when newer ones push the rest out.
Nothing is written to disk: a sign the player read is not theirs to archive.

The pictures are kept as they are (QImages, shrunk by the caller), not
encoded: encoding a card on the UI thread cost more than keeping it.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any

MAX_ENTRIES = 12


@dataclass
class ReadEntry:
    entry_id: int
    created: float
    card: Any = None
    picture: Any = None
    pairs: list[tuple[str, str]] = field(default_factory=list)
    title: str = ""
    pinned: bool = False
    # http(s) links from QR codes in the picture, for the panel's link button.
    links: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """The first translated line, for a button label."""

        for original, translated in self.pairs:
            text = str(translated or original or "").strip()
            if text:
                return text
        return ""


class ReadHistory:
    """Thread-safe, bounded, newest first."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max = max(1, int(max_entries))
        self._entries: list[ReadEntry] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def add(
        self,
        *,
        card: Any = None,
        picture: Any = None,
        pairs: list[tuple[str, str]] | None = None,
        title: str = "",
        links: list[str] | None = None,
    ) -> ReadEntry:
        entry = ReadEntry(
            entry_id=next(self._ids),
            created=time.time(),
            card=card,
            picture=picture,
            pairs=list(pairs or []),
            title=str(title or ""),
            links=[str(link) for link in (links or [])],
        )
        with self._lock:
            self._entries.insert(0, entry)
            self._trim()
        return entry

    def _trim(self) -> None:
        while len(self._entries) > self._max:
            # The oldest unpinned read goes first; only pins left: the oldest pin.
            for index in range(len(self._entries) - 1, -1, -1):
                if not self._entries[index].pinned:
                    del self._entries[index]
                    break
            else:
                del self._entries[-1]

    def get(self, entry_id: int) -> ReadEntry | None:
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    return entry
        return None

    def set_pinned(self, entry_id: int, pinned: bool) -> bool:
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    entry.pinned = bool(pinned)
                    return True
        return False

    def recent(self, limit: int | None = None) -> list[ReadEntry]:
        """Pinned reads first, then the rest; each group newest first."""

        with self._lock:
            ordered = [e for e in self._entries if e.pinned] + [e for e in self._entries if not e.pinned]
        return ordered if limit is None else ordered[: max(0, int(limit))]

    def clear(self) -> None:
        with self._lock:
            self._entries = [entry for entry in self._entries if entry.pinned]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
