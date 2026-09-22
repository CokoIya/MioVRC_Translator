# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""A Mio tab in the SteamVR dashboard.

The dashboard is the one place in VR where laser input is always safe to take:
SteamVR already owns the controllers while it is open, so a panel there can
have buttons without ever stealing a trigger from the game. That makes it the
home for everything a player wants to reach with the headset on - starting
and stopping, switching services, sizing and moving the subtitle panel, and
reading a screenshot when the controller button binding is not delivered.

The tab is a plain RGBA texture drawn by :mod:`src.ui_qt.vr_dashboard_panel`;
mouse events from the laser are mapped back to pixel coordinates and handed
to the panel's hit test. No Qt widgets are involved, so there are no popups
or focus rules to fight.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from typing import Any

from src.core.overlay_texture import OverlayTextureUploader

logger = logging.getLogger(__name__)

DASHBOARD_KEY = "mio.translator.dashboard"
DASHBOARD_NAME = "Mio Translator"
DASHBOARD_WIDTH_METERS = 2.6
MAX_EVENTS_PER_POLL = 64


def thumbnail_path() -> str:
    """The dashboard icon, frozen or from source."""

    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", "icons", "app_icon_mio.png")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(
        os.path.join(here, "..", "..", "assets", "icons", "app_icon_mio.png")
    )


class SteamVRDashboard:
    """Owns the dashboard overlay and routes laser clicks to the panel."""

    def __init__(
        self,
        panel_factory: Callable[[], Any],
        *,
        on_action: Callable[[str], None] | None = None,
    ) -> None:
        self._panel_factory = panel_factory
        self._panel: Any | None = None
        self._on_action = on_action
        self._openvr: Any | None = None
        self._overlay: Any | None = None
        self._handle: Any | None = None
        self._thumbnail: Any | None = None
        self._uploader: OverlayTextureUploader | None = None
        self._available = False
        self._visible = False
        self._hover: str | None = None
        self._pressed: str | None = None
        self._dirty = True

    # ------------------------------------------------------------ status
    @property
    def available(self) -> bool:
        return self._available

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def panel(self) -> Any | None:
        return self._panel

    def _ok(self, action: Callable[[], Any], label: str) -> bool:
        try:
            action()
        except Exception:
            logger.debug("Dashboard %s failed", label, exc_info=True)
            return False
        return True

    # ------------------------------------------------------------ lifecycle
    def start(self) -> bool:
        """Create the dashboard tab. Needs an initialised OpenVR runtime."""

        if self._available:
            return True
        try:
            import openvr
        except Exception:
            logger.debug("openvr unavailable for the dashboard", exc_info=True)
            return False
        self._openvr = openvr
        try:
            self._overlay = openvr.IVROverlay()
            self._handle, self._thumbnail = self._overlay.createDashboardOverlay(
                DASHBOARD_KEY, DASHBOARD_NAME
            )
        except Exception:
            logger.debug("Failed to create the dashboard overlay", exc_info=True)
            self._overlay = None
            self._handle = None
            self._thumbnail = None
            return False
        overlay, handle = self._overlay, self._handle
        self._uploader = OverlayTextureUploader(openvr, overlay, handle)
        panel = self._ensure_panel()
        if panel is None:
            self.stop()
            return False
        width, height = panel.size
        self._ok(
            lambda: overlay.setOverlayWidthInMeters(handle, DASHBOARD_WIDTH_METERS),
            "setOverlayWidthInMeters",
        )
        self._ok(
            lambda: overlay.setOverlayInputMethod(handle, openvr.VROverlayInputMethod_Mouse),
            "setOverlayInputMethod",
        )
        self._ok(
            lambda: overlay.setOverlayMouseScale(
                handle, openvr.HmdVector2_t(float(width), float(height))
            ),
            "setOverlayMouseScale",
        )
        icon = thumbnail_path()
        if os.path.isfile(icon) and self._thumbnail:
            thumbnail = self._thumbnail
            self._ok(lambda: overlay.setOverlayFromFile(thumbnail, icon), "setOverlayFromFile")
        self._available = True
        self._dirty = True
        self.push()
        return True

    def stop(self) -> None:
        overlay, handle = self._overlay, self._handle
        self._available = False
        self._visible = False
        self._overlay = None
        self._handle = None
        self._thumbnail = None
        uploader, self._uploader = self._uploader, None
        if uploader is not None:
            uploader.release()
        if overlay is not None and handle is not None:
            try:
                overlay.destroyOverlay(handle)
            except Exception:
                logger.debug("Failed to destroy the dashboard overlay", exc_info=True)
        panel, self._panel = self._panel, None
        if panel is not None and hasattr(panel, "deleteLater"):
            try:
                panel.deleteLater()
            except Exception:
                logger.debug("Failed to release the dashboard panel", exc_info=True)

    def _ensure_panel(self) -> Any | None:
        if self._panel is None:
            try:
                self._panel = self._panel_factory()
            except Exception:
                logger.exception("Failed to build the dashboard panel")
                return None
        return self._panel

    # ------------------------------------------------------------ content
    def set_state(self, state: dict) -> None:
        """Hand the panel fresh facts; redraw only if something changed."""

        panel = self._ensure_panel()
        if panel is None:
            return
        try:
            if panel.set_state(state):
                self._dirty = True
        except Exception:
            logger.debug("Dashboard state update failed", exc_info=True)

    def push(self) -> None:
        if not self._available or self._overlay is None or self._handle is None:
            return
        panel = self._ensure_panel()
        if panel is None:
            return
        overlay, handle = self._overlay, self._handle
        try:
            render_image = getattr(panel, "render_image", None)
            if self._uploader is not None and callable(render_image):
                if self._uploader.upload_image(render_image(hover=self._hover, pressed=self._pressed)):
                    self._dirty = False
                    return
            buffer, width, height = panel.render_rgba(hover=self._hover, pressed=self._pressed)
        except Exception:
            logger.debug("Dashboard render failed", exc_info=True)
            return
        if self._ok(
            lambda: overlay.setOverlayRaw(handle, buffer, width, height, 4), "setOverlayRaw"
        ):
            self._dirty = False

    # ------------------------------------------------------------ input
    def poll(self) -> None:
        """Drain laser events and redraw when needed. Safe every tick."""

        overlay, handle, openvr = self._overlay, self._handle, self._openvr
        if not self._available or overlay is None or handle is None or openvr is None:
            return
        try:
            self._visible = bool(overlay.isOverlayVisible(handle))
        except Exception:
            self._visible = False
        event = openvr.VREvent_t()
        for _ in range(MAX_EVENTS_PER_POLL):
            try:
                result = overlay.pollNextOverlayEvent(handle, event)
            except Exception:
                logger.debug("Dashboard event poll failed", exc_info=True)
                break
            has_event = result[0] if isinstance(result, tuple) else result
            if not has_event:
                break
            self._handle_event(event)
        if self._visible and self._dirty:
            self.push()

    def _point(self, event: Any) -> tuple[float, float]:
        panel = self._panel
        width, height = panel.size if panel is not None else (1, 1)
        mouse = event.data.mouse
        y = float(mouse.y)
        # Mouse y is in texture space; whether that counts from the top or
        # the bottom of the picture depends on how the picture was uploaded.
        uploader = self._uploader
        if uploader is None or not uploader.mouse_y_is_top_down:
            y = float(height) - y
        return (float(mouse.x), y)

    def _handle_event(self, event: Any) -> None:
        openvr = self._openvr
        panel = self._panel
        if openvr is None or panel is None:
            return
        kind = int(event.eventType)
        if kind == openvr.VREvent_MouseMove:
            hover = panel.hit_test(*self._point(event))
            if hover != self._hover:
                self._hover = hover
                self._dirty = True
        elif kind == openvr.VREvent_MouseButtonDown:
            self._pressed = panel.hit_test(*self._point(event))
            self._dirty = True
        elif kind == openvr.VREvent_MouseButtonUp:
            released = panel.hit_test(*self._point(event))
            pressed, self._pressed = self._pressed, None
            self._dirty = True
            if released is not None and released == pressed and callable(self._on_action):
                try:
                    self._on_action(released)
                except Exception:
                    logger.exception("Dashboard action %s failed", released)
        elif kind == openvr.VREvent_FocusLeave:
            self._hover = None
            self._pressed = None
            self._dirty = True
        elif kind == openvr.VREvent_OverlayShown:
            self._visible = True
            self._dirty = True
        elif kind == openvr.VREvent_OverlayHidden:
            self._visible = False
