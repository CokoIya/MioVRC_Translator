# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""Read the picture SteamVR is showing one eye, straight from the compositor.

Capturing VRChat's desktop window was the wrong source for a headset: that
window is a separate camera with its own field of view, so labels placed by
its pixels landed the wrong size and the wrong distance from the text they
belonged to, and a framed region read the wrong patch of the world. The
compositor's mirror texture is the eye's own frame - full field of view, in
the projection SteamVR reports through ``getProjectionRaw`` - so a pixel in
it maps to a direction from the eye exactly.

The texture is shared through Direct3D 11. No D3D binding is shipped with
Mio, so the handful of COM calls needed - open the view, copy to a staging
texture, map, read - are made through ctypes with hand-written vtable slots.
Measured: a 3312x3312 frame reads back in about 35 ms.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import POINTER, Structure, WINFUNCTYPE, byref, c_long, c_uint, c_void_p
from typing import Any

logger = logging.getLogger(__name__)

# DXGI formats the mirror has been seen in; all are 4 bytes per pixel, R first.
_RGBA_FORMATS = {27, 28, 29}  # R8G8B8A8_TYPELESS, _UNORM, _UNORM_SRGB
_BGRA_FORMATS = {90, 87, 91}  # B8G8R8A8_TYPELESS, _UNORM, _UNORM_SRGB

_D3D_DRIVER_TYPE_HARDWARE = 1
_D3D11_SDK_VERSION = 7
_D3D11_USAGE_STAGING = 3
_D3D11_CPU_ACCESS_READ = 0x20000
_D3D11_MAP_READ = 1

# COM vtable slots (IUnknown 0-2, ID3D11DeviceChild 3-6, then the interface).
_SLOT_RELEASE = 2
_SLOT_VIEW_GET_RESOURCE = 7
_SLOT_TEXTURE2D_GET_DESC = 10
_SLOT_DEVICE_CREATE_TEXTURE2D = 5
_SLOT_CONTEXT_MAP = 14
_SLOT_CONTEXT_UNMAP = 15
_SLOT_CONTEXT_COPY_RESOURCE = 47


class _GUID(Structure):
    _fields_ = [
        ("d1", ctypes.c_uint32),
        ("d2", ctypes.c_uint16),
        ("d3", ctypes.c_uint16),
        ("d4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    import uuid

    parsed = uuid.UUID(text)
    value = _GUID()
    value.d1, value.d2, value.d3 = parsed.time_low, parsed.time_mid, parsed.time_hi_version
    value.d4[:] = parsed.bytes[8:]
    return value


_IID_ID3D11TEXTURE2D = _guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class _SampleDesc(Structure):
    _fields_ = [("Count", c_uint), ("Quality", c_uint)]


class _Texture2DDesc(Structure):
    _fields_ = [
        ("Width", c_uint),
        ("Height", c_uint),
        ("MipLevels", c_uint),
        ("ArraySize", c_uint),
        ("Format", c_uint),
        ("SampleDesc", _SampleDesc),
        ("Usage", c_uint),
        ("BindFlags", c_uint),
        ("CPUAccessFlags", c_uint),
        ("MiscFlags", c_uint),
    ]


class _Mapped(Structure):
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


def _method(interface: c_void_p, slot: int, restype, *argtypes):
    vtable = ctypes.cast(ctypes.cast(interface, POINTER(c_void_p))[0], POINTER(c_void_p))
    prototype = WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(vtable[slot])


def _release(interface: c_void_p | None) -> None:
    if interface is None or not interface.value:
        return
    try:
        _method(interface, _SLOT_RELEASE, c_uint)(interface)
    except Exception:
        logger.debug("COM release failed", exc_info=True)


class EyeFrame:
    """One frame from the compositor: RGBA bytes and the eye's projection."""

    __slots__ = ("rgba", "width", "height", "tangents", "bgra")

    def __init__(self, rgba: bytes, width: int, height: int, tangents, bgra: bool) -> None:
        self.rgba = rgba
        self.width = width
        self.height = height
        self.tangents = tangents
        self.bgra = bgra


# The compositor draws into the mirror texture only while someone holds it,
# and it does so on its next frame, not at the moment of the request. A copy
# taken straight after the request therefore holds whatever the compositor
# drew the LAST time the mirror was held: the previous capture. Measured
# live: a red overlay shown 250 ms before a capture was missing from it and
# turned up in the capture after, and the first capture of a fresh process
# was the last frame of the process before. Every screenshot read came out
# as "the previous picture" until the copy waited for new frames.
FRESH_FRAMES = 2
FRESH_FRAME_TIMEOUT_S = 0.25
# Without frame timing, wait long enough for a couple of frames at 72 Hz.
FRESH_FRAME_FALLBACK_S = 0.045


def compositor_frame_index(compositor: Any) -> int | None:
    """The compositor's frame counter, or None when it cannot be read."""

    try:
        result = compositor.getFrameTiming(0)
    except Exception:
        return None
    timing = result[1] if isinstance(result, tuple) else result
    if isinstance(result, tuple) and not result[0]:
        return None
    try:
        return int(timing.m_nFrameIndex)
    except (TypeError, ValueError, AttributeError):
        return None


def wait_for_fresh_frame(
    compositor: Any,
    *,
    frames: int = FRESH_FRAMES,
    timeout: float = FRESH_FRAME_TIMEOUT_S,
    sleep: Any = time.sleep,
    clock: Any = time.perf_counter,
) -> int | None:
    """Block until the compositor has drawn ``frames`` more frames.

    Returns how many frames passed, or None when the counter was not
    available and a fixed pause stood in for it. Gives up after ``timeout``
    (a headset in standby draws nothing; the copy then takes what there is).
    """

    start = compositor_frame_index(compositor)
    if start is None:
        sleep(FRESH_FRAME_FALLBACK_S)
        return None
    deadline = clock() + timeout
    while True:
        sleep(0.004)
        current = compositor_frame_index(compositor)
        if current is not None and current - start >= frames:
            return current - start
        if clock() >= deadline:
            logger.debug("Compositor drew no new frame within %.0f ms", timeout * 1000.0)
            return (current - start) if current is not None else 0


VIEW_EYES = ("left", "right")
DEFAULT_VIEW_EYE = "right"


def normalize_view_eye(value: object) -> str:
    """The eye a read is taken from: the player's dominant one.

    A frame held between the hands at arm's length covers a different part
    of a far sign for each eye; what the player sees inside it is what the
    dominant eye sees. Most people are right-eyed, as VRHandsFrame assumes.
    """

    eye = str(value or "").strip().lower()
    return eye if eye in VIEW_EYES else DEFAULT_VIEW_EYE


def eye_index(openvr_module: Any, eye: str) -> Any:
    return openvr_module.Eye_Right if normalize_view_eye(eye) == "right" else openvr_module.Eye_Left


class VREyeCapture:
    """Owns a D3D11 device and a staging texture; reads an eye on demand."""

    def __init__(self) -> None:
        self._device: c_void_p | None = None
        self._context: c_void_p | None = None
        self._staging: c_void_p | None = None
        self._staging_key: tuple[int, int, int] | None = None
        self._lock = threading.Lock()
        self._failed = ""

    @property
    def unavailable_reason(self) -> str:
        return self._failed

    # ------------------------------------------------------------ lifecycle
    def _ensure_device(self) -> bool:
        if self._device is not None:
            return True
        if self._failed:
            return False
        try:
            d3d11 = ctypes.WinDLL("d3d11")
            device, context, level = c_void_p(), c_void_p(), c_uint()
            hr = d3d11.D3D11CreateDevice(
                None, _D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0, _D3D11_SDK_VERSION,
                byref(device), byref(level), byref(context),
            )
            if hr != 0 or not device.value:
                self._failed = f"D3D11CreateDevice:0x{hr & 0xFFFFFFFF:08x}"
                return False
        except Exception as exc:
            self._failed = f"d3d11:{type(exc).__name__}"
            logger.info("D3D11 unavailable for eye capture: %s", exc)
            return False
        self._device, self._context = device, context
        return True

    def close(self) -> None:
        with self._lock:
            _release(self._staging)
            _release(self._context)
            _release(self._device)
            self._staging = self._context = self._device = None
            self._staging_key = None

    def _staging_for(self, desc: _Texture2DDesc) -> c_void_p | None:
        key = (int(desc.Width), int(desc.Height), int(desc.Format))
        if self._staging is not None and self._staging_key == key:
            return self._staging
        _release(self._staging)
        self._staging = None
        staging_desc = _Texture2DDesc(
            desc.Width, desc.Height, 1, 1, desc.Format, _SampleDesc(1, 0),
            _D3D11_USAGE_STAGING, 0, _D3D11_CPU_ACCESS_READ, 0,
        )
        staging = c_void_p()
        hr = _method(self._device, _SLOT_DEVICE_CREATE_TEXTURE2D, c_long, POINTER(_Texture2DDesc), c_void_p, POINTER(c_void_p))(
            self._device, byref(staging_desc), None, byref(staging)
        )
        if hr != 0 or not staging.value:
            logger.debug("CreateTexture2D(staging) failed: 0x%08x", hr & 0xFFFFFFFF)
            return None
        self._staging, self._staging_key = staging, key
        return staging

    # ------------------------------------------------------------ capture
    def capture(self, openvr_module: Any, eye: str = DEFAULT_VIEW_EYE) -> EyeFrame | None:
        """Read ``eye`` now. Needs an initialised OpenVR runtime."""

        with self._lock:
            if not self._ensure_device():
                return None
            started = time.perf_counter()
            srv = c_void_p()
            resource = c_void_p()
            texture = c_void_p()
            try:
                compositor = openvr_module.VRCompositor()
                error = compositor.function_table.getMirrorTextureD3D11(
                    eye_index(openvr_module, eye), self._device, byref(srv)
                )
                if error != 0 or not srv.value:
                    logger.debug("getMirrorTextureD3D11 failed: %s", error)
                    return None
                # Now that the mirror is held the compositor will draw into
                # it; wait for that, or the copy is the previous capture.
                waited = wait_for_fresh_frame(compositor)
                _method(srv, _SLOT_VIEW_GET_RESOURCE, None, POINTER(c_void_p))(srv, byref(resource))
                hr = _method(resource, 0, c_long, POINTER(_GUID), POINTER(c_void_p))(
                    resource, byref(_IID_ID3D11TEXTURE2D), byref(texture)
                )
                if hr != 0 or not texture.value:
                    return None
                desc = _Texture2DDesc()
                _method(texture, _SLOT_TEXTURE2D_GET_DESC, None, POINTER(_Texture2DDesc))(texture, byref(desc))
                staging = self._staging_for(desc)
                if staging is None:
                    return None
                _method(self._context, _SLOT_CONTEXT_COPY_RESOURCE, None, c_void_p, c_void_p)(
                    self._context, staging, texture
                )
                mapped = _Mapped()
                hr = _method(self._context, _SLOT_CONTEXT_MAP, c_long, c_void_p, c_uint, c_uint, c_uint, POINTER(_Mapped))(
                    self._context, staging, 0, _D3D11_MAP_READ, 0, byref(mapped)
                )
                if hr != 0:
                    logger.debug("Map failed: 0x%08x", hr & 0xFFFFFFFF)
                    return None
                try:
                    width, height = int(desc.Width), int(desc.Height)
                    row_bytes = width * 4
                    if mapped.RowPitch == row_bytes:
                        rgba = ctypes.string_at(mapped.pData, row_bytes * height)
                    else:
                        rgba = b"".join(
                            ctypes.string_at(mapped.pData + y * mapped.RowPitch, row_bytes)
                            for y in range(height)
                        )
                finally:
                    _method(self._context, _SLOT_CONTEXT_UNMAP, None, c_void_p, c_uint)(self._context, staging, 0)
                try:
                    tangents = tuple(
                        float(v) for v in openvr_module.VRSystem().getProjectionRaw(eye_index(openvr_module, eye))
                    )
                except Exception:
                    tangents = None
                logger.info(
                    "Eye frame (%s) %dx%d read in %.0f ms after %s new compositor frames",
                    normalize_view_eye(eye),
                    width,
                    height,
                    (time.perf_counter() - started) * 1000.0,
                    "?" if waited is None else waited,
                )
                return EyeFrame(rgba, width, height, tangents, bgra=int(desc.Format) in _BGRA_FORMATS)
            except Exception:
                logger.debug("Eye capture failed", exc_info=True)
                return None
            finally:
                _release(texture)
                _release(resource)
                if srv.value:
                    try:
                        compositor.function_table.releaseMirrorTextureD3D11(srv)
                    except Exception:
                        logger.debug("releaseMirrorTextureD3D11 failed", exc_info=True)


_shared: VREyeCapture | None = None
_shared_lock = threading.Lock()


def shared_eye_capture() -> VREyeCapture:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = VREyeCapture()
        return _shared


def eye_frame_size(openvr_module: Any) -> tuple[int, int] | None:
    """The eye frame's size, for the selection frame's aspect ratio.

    The mirror can be larger than this (supersampling), but shares its aspect.
    """

    try:
        width, height = openvr_module.VRSystem().getRecommendedRenderTargetSize()
    except Exception:
        return None
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def capture_eye(openvr_module: Any, eye: str = DEFAULT_VIEW_EYE):
    """One eye as a :class:`src.core.screen_capture.Capture`, or an empty one."""

    from PySide6.QtGui import QImage

    from src.core.screen_capture import Capture, encode_image

    frame = shared_eye_capture().capture(openvr_module, eye)
    if frame is None:
        return Capture(b"", 0, 0, "vr_eye")
    fmt = QImage.Format.Format_ARGB32 if frame.bgra else QImage.Format.Format_RGBA8888
    image = QImage(frame.rgba, frame.width, frame.height, frame.width * 4, fmt).copy()
    png = encode_image(image)
    if not png:
        return Capture(b"", 0, 0, "vr_eye")
    return Capture(
        png=png,
        width=frame.width,
        height=frame.height,
        source="vr_eye",
        frame_width=frame.width,
        frame_height=frame.height,
        tangents=frame.tangents,
    )
