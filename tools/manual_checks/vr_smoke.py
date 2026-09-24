"""Pre-release VR smoke check: the parts unit tests cannot reach.

Run it on a PC with SteamVR already running (a headset on the desk is enough;
nobody needs to wear it). It never starts SteamVR: when SteamVR is not running
it says so and exits with code 2. It uses its own overlay key, so Mio may be
running at the same time.

    .venv\\Scripts\\python tools\\manual_checks\\vr_smoke.py

Checks:
  1. GL texture upload - a two-colour picture goes up through the same
     OverlayTextureUploader Mio uses and is read back with
     getOverlayImageData; the colours must come back where they were drawn.
  2. Pinned texture size - a second picture of another size is letterboxed
     onto the first picture's canvas, and picture_fraction maps the texture
     centre back to the picture centre.
  3. Eye capture freshness - a red overlay is shown in front of the head, the
     left eye is captured, and the centre must be red; after hiding it the
     next capture must not be. This is the "every read shows the previous
     picture" failure. If the compositor is idle (headset asleep) the check
     reports INCONCLUSIVE rather than FAIL.

Exit code 0 when every check passed, 1 on a failure, 2 when SteamVR is not
running.
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROBE_KEY = "mio.smoke.probe"
PROBE_NAME = "Mio smoke probe"
RED = (230, 20, 20)
BLUE = (20, 40, 230)

results: list[tuple[str, str, str]] = []


def record(name: str, outcome: str, detail: str = "") -> None:
    results.append((name, outcome, detail))
    print(f"[{outcome}] {name}" + (f" - {detail}" if detail else ""))


def close_enough(pixel: tuple[int, int, int], wanted: tuple[int, int, int], tolerance: int = 40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, wanted))


def read_back(overlay, handle, width: int, height: int) -> tuple[bytes, int, int]:
    size = width * height * 4
    buffer = (ctypes.c_ubyte * size)()
    got_w, got_h = overlay.getOverlayImageData(handle, buffer, size)
    return bytes(buffer), int(got_w), int(got_h)


def pixel(data: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 4
    return data[offset], data[offset + 1], data[offset + 2]


def two_colour_image(width: int, height: int):
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*BLUE))
    painter = QPainter(image)
    try:
        painter.fillRect(0, 0, width // 2, height, QColor(*RED))
    finally:
        painter.end()
    return image


def check_gl_upload(openvr, overlay, handle, uploader) -> None:
    image = two_colour_image(256, 128)
    if not uploader.upload_image(image):
        record("GL upload", "FAIL", "upload_image returned False")
        return
    path = "gl" if uploader.mouse_y_is_top_down else "raw"
    if path != "gl":
        record("GL upload", "FAIL", "fell back to the raw path; the GL context is unusable")
    time.sleep(0.2)
    data, width, height = read_back(overlay, handle, 256, 128)
    left = pixel(data, width, width // 4, height // 2)
    right = pixel(data, width, 3 * width // 4, height // 2)
    if close_enough(left, RED) and close_enough(right, BLUE):
        record("GL upload", "PASS", f"{width}x{height} via {path}")
    else:
        record("GL upload", "FAIL", f"left={left} right={right} (expected red | blue)")


def check_pinned_canvas(overlay, handle, uploader) -> None:
    image = two_colour_image(128, 128)  # another size than the first picture
    if not uploader.upload_image(image):
        record("Pinned canvas", "FAIL", "second upload failed")
        return
    time.sleep(0.2)
    canvas = uploader.canvas or (256, 128)
    data, width, height = read_back(overlay, handle, *canvas)
    if (width, height) != tuple(canvas):
        record("Pinned canvas", "FAIL", f"texture is {width}x{height}, canvas {canvas}")
        return
    centre = uploader.picture_fraction(0.5, 0.5)
    left_edge = uploader.picture_fraction(0.25 + 0.01, 0.5)[0]
    if abs(centre[0] - 0.5) < 0.01 and abs(centre[1] - 0.5) < 0.01 and abs(left_edge) < 0.05:
        record("Pinned canvas", "PASS", f"picture {uploader.drawn_size} on canvas {canvas}")
    else:
        record("Pinned canvas", "FAIL", f"centre->{centre} left edge->{left_edge:.3f}")


def check_eye_capture(openvr, overlay, handle, uploader) -> None:
    from src.core.steamvr_inplace import _identity_pose, _to_hmd_matrix
    from src.core.vr_eye_capture import shared_eye_capture

    from PySide6.QtGui import QColor, QImage

    red = QImage(512, 512, QImage.Format.Format_RGBA8888)
    red.fill(QColor(*RED))
    uploader.upload_image(red)
    rows = [list(row) for row in _identity_pose()]
    rows[2][3] = -1.0  # one metre in front of the head
    overlay.setOverlayTransformTrackedDeviceRelative(
        handle, openvr.k_unTrackedDeviceIndex_Hmd, _to_hmd_matrix(openvr, rows)
    )
    overlay.setOverlayWidthInMeters(handle, uploader.quad_width(1.5))
    overlay.showOverlay(handle)
    time.sleep(0.25)
    capture = shared_eye_capture()
    shown = capture.capture(openvr)
    overlay.hideOverlay(handle)
    time.sleep(0.25)
    hidden = capture.capture(openvr)
    if shown is None or hidden is None:
        record("Eye capture", "FAIL", f"no frame ({capture.unavailable_reason or 'unknown'})")
        return

    def centre_colour(frame) -> tuple[int, int, int]:
        x, y = frame.width // 2, frame.height // 2
        r, g, b = pixel(frame.rgba, frame.width, x, y)
        return (b, g, r) if frame.bgra else (r, g, b)

    with_overlay = centre_colour(shown)
    without = centre_colour(hidden)
    if close_enough(with_overlay, RED, 70) and not close_enough(without, RED, 70):
        record("Eye capture", "PASS", f"{shown.width}x{shown.height}, fresh frames")
    elif with_overlay == without:
        record(
            "Eye capture",
            "INCONCLUSIVE",
            "both captures identical - the compositor may be idle (headset asleep)",
        )
    else:
        record(
            "Eye capture",
            "FAIL",
            f"centre with overlay {with_overlay}, without {without}: stale or wrong frame",
        )


def main() -> int:
    try:
        import openvr
    except Exception as exc:
        print(f"openvr is not installed: {exc}")
        return 2
    try:
        # A background app never starts SteamVR; it fails when it is not running.
        openvr.init(openvr.VRApplication_Background)
        openvr.shutdown()
    except Exception as exc:
        print(
            f"SteamVR is not running ({type(exc).__name__}: {exc}); start it first. "
            "Nothing was changed."
        )
        return 2

    from PySide6.QtGui import QGuiApplication

    from src.core.overlay_texture import OverlayTextureUploader

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    openvr.init(openvr.VRApplication_Overlay)
    overlay = openvr.IVROverlay()
    handle = overlay.createOverlay(PROBE_KEY, PROBE_NAME)
    try:
        uploader = OverlayTextureUploader(openvr, overlay, handle)
        for check in (check_gl_upload, check_pinned_canvas):
            try:
                if check is check_gl_upload:
                    check(openvr, overlay, handle, uploader)
                else:
                    check(overlay, handle, uploader)
            except Exception as exc:
                record(check.__name__, "FAIL", f"{type(exc).__name__}: {exc}")
        # The eye check needs its own texture size: a fresh uploader/handle.
        eye_handle = overlay.createOverlay(PROBE_KEY + ".eye", PROBE_NAME + " (eye)")
        try:
            check_eye_capture(openvr, overlay, eye_handle, OverlayTextureUploader(openvr, overlay, eye_handle))
        except Exception as exc:
            record("Eye capture", "FAIL", f"{type(exc).__name__}: {exc}")
        finally:
            overlay.destroyOverlay(eye_handle)
    finally:
        try:
            overlay.destroyOverlay(handle)
        finally:
            openvr.shutdown()
            del app
    failed = [name for name, outcome, _ in results if outcome == "FAIL"]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks did not fail.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
