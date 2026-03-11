from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import customtkinter as ctk

SCREEN_PADDING = 18
ANIMATION_OFFSET_Y = 18
ANIMATION_STEPS = 8
ANIMATION_INTERVAL_MS = 16
APP_ICON_ICO_FILE = "app_icon_mio.ico"
PREFERRED_APP_ICON_ICO_PATH = Path(r"B:\python_project\vrc-translator\assets\icons\app_icon_mio.ico")
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON = 11
SM_CYICON = 12
SM_CXSMICON = 49
SM_CYSMICON = 50

if sys.platform == "win32":
    _USER32 = ctypes.windll.user32
    _USER32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    _USER32.LoadImageW.restype = ctypes.c_void_p
    _USER32.SendMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    _USER32.SendMessageW.restype = ctypes.c_ssize_t
    _USER32.DestroyIcon.argtypes = [ctypes.c_void_p]
    _USER32.DestroyIcon.restype = ctypes.c_int
    _USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _USER32.GetSystemMetrics.restype = ctypes.c_int
else:
    _USER32 = None


def _measure_window(window: ctk.CTkToplevel) -> tuple[int, int]:
    window.update_idletasks()
    width = max(window.winfo_width(), window.winfo_reqwidth(), 260)
    height = max(window.winfo_height(), window.winfo_reqheight(), 140)
    return width, height


def _explicit_geometry_size(window: ctk.CTkToplevel) -> tuple[int, int] | None:
    try:
        geometry = window.geometry().split("+", 1)[0]
        width_text, height_text = geometry.split("x", 1)
        width = int(width_text)
        height = int(height_text)
        if width > 1 and height > 1:
            return width, height
    except Exception:
        return None
    return None


def _preferred_window_size(window: ctk.CTkToplevel) -> tuple[int, int]:
    explicit_size = getattr(window, "_popup_size", None)
    if (
        isinstance(explicit_size, tuple)
        and len(explicit_size) == 2
        and all(isinstance(value, (int, float)) for value in explicit_size)
    ):
        width, height = explicit_size
        if width > 1 and height > 1:
            return int(width), int(height)

    geometry_size = _explicit_geometry_size(window)
    if geometry_size is not None:
        return geometry_size

    return _measure_window(window)


def _runtime_base_dirs() -> list[Path]:
    if not getattr(sys, "frozen", False):
        return [Path(__file__).resolve().parents[2]]

    dirs = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_path = Path(meipass)
        if meipass_path not in dirs:
            dirs.append(meipass_path)
    return dirs


def _assets_dirs() -> list[Path]:
    dirs = []
    for base in _runtime_base_dirs():
        assets_dir = base / "assets"
        if assets_dir.exists():
            dirs.append(assets_dir)
    if dirs:
        return dirs
    return [_runtime_base_dirs()[0] / "assets"]


def _icons_dirs() -> list[Path]:
    dirs = []
    for assets_dir in _assets_dirs():
        icons_dir = assets_dir / "icons"
        if icons_dir.exists():
            dirs.append(icons_dir)
    if dirs:
        return dirs
    return [_assets_dirs()[0] / "icons"]


def _find_icon_file(filename: str) -> Path | None:
    for icons_dir in _icons_dirs():
        candidate = icons_dir / filename
        if candidate.exists():
            return candidate
    for assets_dir in _assets_dirs():
        candidate = assets_dir / filename
        if candidate.exists():
            return candidate
    return None


def _destroy_window_icons(window) -> None:
    if _USER32 is None:
        return
    handles = getattr(window, "_window_icon_handles", None)
    if not isinstance(handles, dict):
        return
    for handle in handles.values():
        if handle:
            try:
                _USER32.DestroyIcon(ctypes.c_void_p(handle))
            except Exception:
                pass
    window._window_icon_handles = {}


def _bind_window_icon_cleanup(window) -> None:
    if getattr(window, "_window_icon_cleanup_bound", False):
        return

    def _cleanup(_event=None) -> None:
        _destroy_window_icons(window)

    try:
        window.bind("<Destroy>", _cleanup, add="+")
        window._window_icon_cleanup_bound = True
    except Exception:
        pass


def _apply_windows_titlebar_icon(window, ico_path: Path) -> None:
    if _USER32 is None or not ico_path.exists():
        return

    try:
        window.update_idletasks()
        hwnd = int(window.winfo_id())
    except Exception:
        return

    if hwnd <= 0:
        return

    sizes = (
        (ICON_BIG, _USER32.GetSystemMetrics(SM_CXICON), _USER32.GetSystemMetrics(SM_CYICON)),
        (ICON_SMALL, _USER32.GetSystemMetrics(SM_CXSMICON), _USER32.GetSystemMetrics(SM_CYSMICON)),
    )

    handles: dict[int, int] = {}
    for icon_type, width, height in sizes:
        try:
            handle = _USER32.LoadImageW(
                None,
                str(ico_path),
                IMAGE_ICON,
                max(0, width),
                max(0, height),
                LR_LOADFROMFILE,
            )
        except Exception:
            handle = None
        if handle:
            try:
                _USER32.SendMessageW(hwnd, WM_SETICON, icon_type, handle)
                handles[icon_type] = int(handle)
            except Exception:
                try:
                    _USER32.DestroyIcon(handle)
                except Exception:
                    pass

    if not handles:
        return

    _destroy_window_icons(window)
    window._window_icon_handles = handles
    _bind_window_icon_cleanup(window)


def apply_window_icon(window) -> None:
    ico_path = (
        PREFERRED_APP_ICON_ICO_PATH
        if PREFERRED_APP_ICON_ICO_PATH.exists()
        else _find_icon_file(APP_ICON_ICO_FILE)
    )

    def _apply() -> None:
        if ico_path:
            for setter_name in ("iconbitmap", "wm_iconbitmap"):
                try:
                    getattr(window, setter_name)(str(ico_path))
                except Exception:
                    pass
            _apply_windows_titlebar_icon(window, ico_path)

    _apply()
    try:
        window.after(0, _apply)
    except Exception:
        pass
    try:
        window.after(80, _apply)
    except Exception:
        pass


def clamp_window_geometry(
    window: ctk.CTkToplevel,
    *,
    x: int,
    y: int,
    width: int | None = None,
    height: int | None = None,
    padding: int = SCREEN_PADDING,
) -> tuple[int, int, int, int]:
    measured_width, measured_height = _measure_window(window)
    safe_width = int(width or measured_width)
    safe_height = int(height or measured_height)

    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    max_x = max(padding, screen_width - safe_width - padding)
    max_y = max(padding, screen_height - safe_height - padding)
    safe_x = max(padding, min(int(x), max_x))
    safe_y = max(padding, min(int(y), max_y))
    return safe_x, safe_y, safe_width, safe_height


def center_popup(
    window: ctk.CTkToplevel,
    *,
    parent=None,
    padding: int = SCREEN_PADDING,
) -> tuple[int, int, int, int]:
    width, height = _preferred_window_size(window)

    if parent is not None and getattr(parent, "winfo_exists", lambda: False)():
        parent.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2 - 8
    else:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 2

    return clamp_window_geometry(
        window,
        x=x,
        y=y,
        width=width,
        height=height,
        padding=padding,
    )


def present_popup(
    window: ctk.CTkToplevel,
    *,
    parent=None,
    padding: int = SCREEN_PADDING,
    animate: bool = True,
) -> None:
    try:
        window.withdraw()
    except Exception:
        pass

    x, y, width, height = center_popup(window, parent=parent, padding=padding)
    window.geometry(f"{width}x{height}+{x}+{y}")
    supports_alpha = True
    try:
        window.attributes("-alpha", 0.0 if animate else 1.0)
    except Exception:
        supports_alpha = False

    window.deiconify()
    window.lift()
    apply_window_icon(window)

    if not animate:
        return

    start_y = min(
        window.winfo_screenheight() - height - padding,
        y + ANIMATION_OFFSET_Y,
    )
    window.geometry(f"{width}x{height}+{x}+{start_y}")

    def animate_step(step: int = 0) -> None:
        if not window.winfo_exists():
            return

        progress = min(1.0, step / max(ANIMATION_STEPS - 1, 1))
        eased = 1.0 - (1.0 - progress) ** 3
        current_y = round(start_y - (start_y - y) * eased)
        window.geometry(f"{width}x{height}+{x}+{current_y}")
        if supports_alpha:
            window.attributes("-alpha", 0.18 + 0.82 * eased)

        if progress < 1.0:
            window.after(ANIMATION_INTERVAL_MS, lambda: animate_step(step + 1))
            return

        if supports_alpha:
            window.attributes("-alpha", 1.0)
        window.geometry(f"{width}x{height}+{x}+{y}")
        apply_window_icon(window)

    window.after(0, animate_step)
