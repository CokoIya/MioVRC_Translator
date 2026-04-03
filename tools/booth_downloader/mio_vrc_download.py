from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import locale
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk


DEFAULT_MANIFEST_URL = "https://78hejiu.top/installer_manifest.json"
DEFAULT_HOMEPAGE_URL = "https://78hejiu.top"
DEFAULT_APP_EXE = "MioTranslator.exe"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_ROOT_DIRNAME = "MioVRCDownloader"
DOWNLOAD_CACHE_DIRNAME = "installer_cache"


COPY = {
    "zh": {
        "window_title": "Mio VRC 下载器",
        "headline": "一键下载并安装正式版",
        "subheadline": "从官网获取最新正式版，静默安装到你指定的目录。",
        "install_path": "安装目录",
        "browse": "浏览...",
        "install": "开始安装",
        "busy": "安装中...",
        "open_folder": "打开安装目录",
        "ready": "准备就绪。",
        "fetch_manifest": "正在获取正式版信息...",
        "manifest_ready": "最新正式版 {version} 已就绪。",
        "download_start": "正在下载正式版安装器...",
        "download_progress": "正在下载正式版安装器... {percent:.1f}% ({downloaded} / {total})",
        "download_progress_unknown": "正在下载正式版安装器... {downloaded}",
        "verify": "正在校验安装包完整性...",
        "installing": "正在静默安装正式版...",
        "launching": "正在启动 MioTranslator...",
        "finish": "安装完成，MioTranslator 已启动。",
        "finish_manual_launch": "安装完成，但主程序未能自动启动，请手动打开。",
        "path_invalid": "请选择一个有效的安装目录。",
        "path_root_invalid": "不能把安装目录设置为磁盘根目录，请选择一个文件夹。",
        "busy_close": "安装正在进行中，请稍候完成。",
        "error_title": "安装失败",
        "manifest_error": "无法从官网获取正式版信息：{detail}",
        "download_error": "下载安装器失败：{detail}",
        "size_mismatch": "下载文件大小与官网清单不一致。",
        "hash_mismatch": "下载文件校验失败，请稍后重试。",
        "install_error": "正式版安装失败，退出码：{code}",
        "launch_error": "安装完成，但无法自动启动主程序：{detail}",
        "folder_error": "无法打开安装目录：{detail}",
        "details_idle": "官网：{homepage}",
    },
    "ja": {
        "window_title": "Mio VRC ???????",
        "headline": "正式版をワンクリックで導入",
        "subheadline": "公式サイトから最新の正式版を取得し、指定したフォルダへサイレントインストールします。",
        "install_path": "インストール先",
        "browse": "参照...",
        "install": "インストール開始",
        "busy": "インストール中...",
        "open_folder": "インストール先を開く",
        "ready": "準備完了。",
        "fetch_manifest": "正式版の情報を取得しています...",
        "manifest_ready": "最新の正式版 {version} を準備しました。",
        "download_start": "正式版インストーラーをダウンロードしています...",
        "download_progress": "正式版インストーラーをダウンロードしています... {percent:.1f}% ({downloaded} / {total})",
        "download_progress_unknown": "正式版インストーラーをダウンロードしています... {downloaded}",
        "verify": "インストーラーを検証しています...",
        "installing": "正式版をサイレントインストールしています...",
        "launching": "MioTranslator を起動しています...",
        "finish": "インストールが完了し、MioTranslator を起動しました。",
        "finish_manual_launch": "インストールは完了しましたが、自動起動できませんでした。手動で起動してください。",
        "path_invalid": "有効なインストール先を指定してください。",
        "path_root_invalid": "ドライブ直下は選べません。フォルダを指定してください。",
        "busy_close": "インストール中です。完了までお待ちください。",
        "error_title": "インストール失敗",
        "manifest_error": "公式サイトから正式版情報を取得できませんでした: {detail}",
        "download_error": "インストーラーのダウンロードに失敗しました: {detail}",
        "size_mismatch": "ダウンロードしたファイルサイズが公式 manifest と一致しません。",
        "hash_mismatch": "ダウンロードしたファイルの検証に失敗しました。時間をおいて再試行してください。",
        "install_error": "正式版インストールに失敗しました。終了コード: {code}",
        "launch_error": "インストールは完了しましたが、自動起動に失敗しました: {detail}",
        "folder_error": "インストール先を開けませんでした: {detail}",
        "details_idle": "公式サイト: {homepage}",
    },
    "en": {
        "window_title": "Mio VRC Downloader",
        "headline": "Install the official release in one click",
        "subheadline": "Fetch the latest official release from the website and silently install it to your chosen folder.",
        "install_path": "Install folder",
        "browse": "Browse...",
        "install": "Install",
        "busy": "Installing...",
        "open_folder": "Open install folder",
        "ready": "Ready.",
        "fetch_manifest": "Fetching the latest official release info...",
        "manifest_ready": "Latest official release {version} is ready.",
        "download_start": "Downloading the official installer...",
        "download_progress": "Downloading the official installer... {percent:.1f}% ({downloaded} / {total})",
        "download_progress_unknown": "Downloading the official installer... {downloaded}",
        "verify": "Verifying the installer package...",
        "installing": "Silently installing the official release...",
        "launching": "Launching MioTranslator...",
        "finish": "Installation complete. MioTranslator has been launched.",
        "finish_manual_launch": "Installation completed, but the app could not be launched automatically.",
        "path_invalid": "Please choose a valid install folder.",
        "path_root_invalid": "Please choose a folder instead of a drive root.",
        "busy_close": "Installation is still running. Please wait for it to finish.",
        "error_title": "Installation Failed",
        "manifest_error": "Could not fetch release info from the official site: {detail}",
        "download_error": "Failed to download the installer: {detail}",
        "size_mismatch": "The downloaded file size does not match the manifest.",
        "hash_mismatch": "The downloaded file failed verification. Please try again later.",
        "install_error": "The official installer failed with exit code: {code}",
        "launch_error": "Installation completed, but the app could not be launched automatically: {detail}",
        "folder_error": "Could not open the install folder: {detail}",
        "details_idle": "Official site: {homepage}",
    },
}


@dataclass(frozen=True)
class InstallerManifest:
    version: str
    installer_url: str
    installer_name: str
    sha256: str
    size_bytes: int | None
    app_exe: str
    homepage_url: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--manifest-url", default=os.environ.get("MIO_VRC_DOWNLOAD_MANIFEST_URL", DEFAULT_MANIFEST_URL))
    parser.add_argument("--no-launch", action="store_true")
    return parser.parse_args(argv[1:])


def detect_language() -> str:
    tag = ""
    try:
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        tag = locale.windows_locale.get(lcid, "")
    except Exception:
        pass
    if not tag:
        try:
            tag = locale.getlocale()[0] or ""
        except Exception:
            tag = ""
    normalized = tag.lower().replace("_", "-")
    if normalized.startswith("ja"):
        return "ja"
    if normalized.startswith("zh"):
        return "zh"
    return "en"


def text(lang: str, key: str, **kwargs: object) -> str:
    template = COPY.get(lang, COPY["en"]).get(key, COPY["en"][key])
    if kwargs:
        return template.format(**kwargs)
    return template


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative_path


def default_install_dir() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / "Mio RealTime Translator"


def downloader_data_dir() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / DOWNLOAD_ROOT_DIRNAME


def _safe_path_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    return cleaned or "latest"


def cached_installer_path(manifest: InstallerManifest) -> Path:
    return (
        downloader_data_dir()
        / DOWNLOAD_CACHE_DIRNAME
        / _safe_path_component(manifest.version)
        / manifest.installer_name
    )


def installer_log_path(release_version: str) -> Path:
    return downloader_data_dir() / "logs" / f"install-{_safe_path_component(release_version)}.log"


def normalize_install_dir(raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"')
    if not cleaned:
        raise ValueError("empty path")
    path = Path(os.path.expandvars(os.path.expanduser(cleaned))).resolve(strict=False)
    if path == Path(path.anchor):
        raise ValueError("root path")
    return path


def format_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "?"
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def request_url(url: str, timeout: int = 20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MioVRCDownloader/1.0 (+https://78hejiu.top)",
            "Cache-Control": "no-cache",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def load_manifest(manifest_url: str) -> InstallerManifest:
    try:
        with request_url(manifest_url) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("manifest is not a JSON object")

    version = str(payload.get("version") or "").strip()
    installer_url = str(payload.get("installer_url") or payload.get("url") or "").strip()
    sha256 = str(payload.get("sha256") or "").strip().lower()
    installer_name = str(payload.get("installer_name") or "").strip()
    homepage_url = str(payload.get("homepage_url") or DEFAULT_HOMEPAGE_URL).strip() or DEFAULT_HOMEPAGE_URL
    app_exe = str(payload.get("app_exe") or DEFAULT_APP_EXE).strip() or DEFAULT_APP_EXE

    if not version:
        raise RuntimeError("manifest is missing version")
    if not installer_url:
        raise RuntimeError("manifest is missing installer_url")
    if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
        raise RuntimeError("manifest sha256 is invalid")
    if not installer_name:
        parsed = urllib.parse.urlparse(installer_url)
        installer_name = Path(parsed.path).name or "MioTranslator-Setup.exe"

    size_value = payload.get("size_bytes")
    size_bytes: int | None = None
    if size_value is not None and str(size_value).strip():
        try:
            size_bytes = int(size_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("manifest size_bytes is invalid") from exc
        if size_bytes <= 0:
            size_bytes = None

    return InstallerManifest(
        version=version,
        installer_url=installer_url,
        installer_name=installer_name,
        sha256=sha256,
        size_bytes=size_bytes,
        app_exe=app_exe,
        homepage_url=homepage_url,
    )


def verify_downloaded_file(path: Path, expected_size: int | None, expected_sha256: str) -> bool:
    if not path.is_file():
        return False
    if expected_size is not None and path.stat().st_size != expected_size:
        return False

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest().lower() == expected_sha256.lower()


def download_installer(
    installer_url: str,
    destination: Path,
    expected_size: int | None,
    expected_sha256: str,
    progress_callback,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = destination.with_suffix(destination.suffix + ".download")
    if partial_path.exists():
        partial_path.unlink()

    hasher = hashlib.sha256()
    total_bytes = expected_size
    downloaded = 0

    try:
        with request_url(installer_url, timeout=60) as response, partial_path.open("wb") as handle:
            header_size = response.headers.get("Content-Length")
            if total_bytes is None and header_size and header_size.isdigit():
                total_bytes = int(header_size)
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                progress_callback(downloaded, total_bytes)
    except urllib.error.URLError as exc:
        if partial_path.exists():
            partial_path.unlink()
        raise RuntimeError(str(exc)) from exc
    except Exception:
        if partial_path.exists():
            partial_path.unlink()
        raise

    if expected_size is not None and downloaded != expected_size:
        if partial_path.exists():
            partial_path.unlink()
        raise RuntimeError("size_mismatch")
    if hasher.hexdigest().lower() != expected_sha256.lower():
        if partial_path.exists():
            partial_path.unlink()
        raise RuntimeError("hash_mismatch")

    os.replace(partial_path, destination)


def build_silent_install_command(installer_path: Path, install_dir: Path, log_path: Path) -> list[str]:
    return [
        str(installer_path),
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/SP-",
        "/NORESTART",
        "/CURRENTUSER",
        f"/DIR={install_dir}",
        f"/LOG={log_path}",
    ]


def run_silent_install(installer_path: Path, install_dir: Path, release_version: str) -> None:
    log_path = installer_log_path(release_version)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        build_silent_install_command(installer_path, install_dir, log_path),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(str(completed.returncode))


def launch_app(app_path: Path) -> None:
    if not app_path.is_file():
        raise FileNotFoundError(str(app_path))
    subprocess.Popen([str(app_path)], cwd=str(app_path.parent))


def open_folder(folder_path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(folder_path))
        return
    subprocess.Popen([str(folder_path)])


class DownloaderApp:
    def __init__(self, root: tk.Tk, manifest_url: str, no_launch: bool) -> None:
        self.root = root
        self.lang = detect_language()
        self.manifest_url = manifest_url
        self.no_launch = no_launch
        self.busy = False
        self.installed_dir: Path | None = None
        self.manifest: InstallerManifest | None = None

        self.root.title(text(self.lang, "window_title"))
        self.root.geometry("660x320")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            icon_path = resource_path("assets/icons/app_icon_mio.ico")
            if icon_path.exists():
                self.root.iconbitmap(default=str(icon_path))
        except Exception:
            pass

        self.install_dir_var = tk.StringVar(value=str(default_install_dir()))
        self.status_var = tk.StringVar(value=text(self.lang, "ready"))
        self.details_var = tk.StringVar(value=text(self.lang, "details_idle", homepage=DEFAULT_HOMEPAGE_URL))
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_ui()

    def _build_ui(self) -> None:
        wrapper = ttk.Frame(self.root, padding=20)
        wrapper.pack(fill="both", expand=True)
        wrapper.columnconfigure(0, weight=1)

        ttk.Label(wrapper, text=text(self.lang, "headline"), font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            wrapper,
            text=text(self.lang, "subheadline"),
            wraplength=610,
            foreground="#4b5563",
        ).grid(row=1, column=0, sticky="w", pady=(8, 20))

        path_frame = ttk.Frame(wrapper)
        path_frame.grid(row=2, column=0, sticky="ew")
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text=text(self.lang, "install_path")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.path_entry = ttk.Entry(path_frame, textvariable=self.install_dir_var)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(path_frame, text=text(self.lang, "browse"), command=self._choose_folder)
        self.browse_button.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self.progress = ttk.Progressbar(wrapper, mode="determinate", maximum=100.0, variable=self.progress_var)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(24, 8))

        ttk.Label(wrapper, textvariable=self.status_var, wraplength=610).grid(row=4, column=0, sticky="w")
        ttk.Label(wrapper, textvariable=self.details_var, wraplength=610, foreground="#6b7280").grid(
            row=5, column=0, sticky="w", pady=(6, 18)
        )

        button_frame = ttk.Frame(wrapper)
        button_frame.grid(row=6, column=0, sticky="e")

        self.open_folder_button = ttk.Button(
            button_frame,
            text=text(self.lang, "open_folder"),
            command=self._open_installed_folder,
            state="disabled",
        )
        self.open_folder_button.pack(side="right")

        self.install_button = ttk.Button(button_frame, text=text(self.lang, "install"), command=self._start_install)
        self.install_button.pack(side="right", padx=(0, 10))

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.install_dir_var.get() or str(default_install_dir()))
        if selected:
            self.install_dir_var.set(selected)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.install_button.configure(text=text(self.lang, "busy") if busy else text(self.lang, "install"))
        self.install_button.configure(state="disabled" if busy else "normal")
        self.browse_button.configure(state="disabled" if busy else "normal")
        self.path_entry.configure(state="disabled" if busy else "normal")

    def _set_progress(self, percent: float) -> None:
        self.progress.configure(mode="determinate")
        self.progress.stop()
        self.progress_var.set(max(0.0, min(100.0, percent)))

    def _set_progress_indeterminate(self) -> None:
        self.progress_var.set(100.0)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

    def _on_close(self) -> None:
        if self.busy:
            messagebox.showinfo(text(self.lang, "window_title"), text(self.lang, "busy_close"))
            return
        self.root.destroy()

    def _open_installed_folder(self) -> None:
        if self.installed_dir is None:
            return
        try:
            open_folder(self.installed_dir)
        except Exception as exc:
            messagebox.showerror(text(self.lang, "error_title"), text(self.lang, "folder_error", detail=str(exc)))

    def _start_install(self) -> None:
        try:
            install_dir = normalize_install_dir(self.install_dir_var.get())
        except ValueError as exc:
            key = "path_root_invalid" if "root" in str(exc) else "path_invalid"
            messagebox.showerror(text(self.lang, "error_title"), text(self.lang, key))
            return

        self.installed_dir = install_dir
        self.open_folder_button.configure(state="disabled")
        self._set_busy(True)
        self._set_progress(0.0)
        self.status_var.set(text(self.lang, "fetch_manifest"))
        self.details_var.set(text(self.lang, "details_idle", homepage=DEFAULT_HOMEPAGE_URL))

        worker = threading.Thread(target=self._install_worker, args=(install_dir,), daemon=True)
        worker.start()

    def _install_worker(self, install_dir: Path) -> None:
        try:
            manifest = load_manifest(self.manifest_url)
            self.manifest = manifest
            installer_path = cached_installer_path(manifest)
            self.root.after(
                0,
                lambda: self._on_manifest_ready(manifest),
            )

            if not verify_downloaded_file(installer_path, manifest.size_bytes, manifest.sha256):
                self.root.after(0, lambda: self.status_var.set(text(self.lang, "download_start")))
                download_installer(
                    manifest.installer_url,
                    installer_path,
                    manifest.size_bytes,
                    manifest.sha256,
                    self._schedule_download_progress,
                )

            self.root.after(0, lambda: self._on_verify_started(manifest, installer_path))
            run_silent_install(installer_path, install_dir, manifest.version)

            self.root.after(0, lambda: self._on_install_completed(install_dir, manifest))
        except Exception as exc:
            self.root.after(0, lambda err=exc: self._on_error(err))

    def _schedule_download_progress(self, downloaded: int, total: int | None) -> None:
        self.root.after(0, lambda: self._on_download_progress(downloaded, total))

    def _on_manifest_ready(self, manifest: InstallerManifest) -> None:
        self.status_var.set(text(self.lang, "manifest_ready", version=manifest.version))
        self.details_var.set(
            f"{manifest.installer_name} | {format_bytes(manifest.size_bytes)} | SHA256 {manifest.sha256[:12]}... | {manifest.homepage_url}"
        )

    def _on_download_progress(self, downloaded: int, total: int | None) -> None:
        if total and total > 0:
            percent = downloaded / total * 100.0
            self._set_progress(percent)
            self.status_var.set(
                text(
                    self.lang,
                    "download_progress",
                    percent=percent,
                    downloaded=format_bytes(downloaded),
                    total=format_bytes(total),
                )
            )
        else:
            self.status_var.set(
                text(self.lang, "download_progress_unknown", downloaded=format_bytes(downloaded))
            )
        installer_path = cached_installer_path(self.manifest) if self.manifest else None
        if installer_path is not None:
            self.details_var.set(f"{installer_path} | {text(self.lang, 'details_idle', homepage=self.manifest.homepage_url)}")
        else:
            self.details_var.set(
                text(self.lang, "details_idle", homepage=DEFAULT_HOMEPAGE_URL)
            )

    def _on_verify_started(self, manifest: InstallerManifest, installer_path: Path) -> None:
        self._set_progress(100.0)
        self.status_var.set(text(self.lang, "verify"))
        self.details_var.set(f"{manifest.installer_name} | SHA256 {manifest.sha256[:12]}... | {installer_path}")
        self.root.after(150, lambda: self.status_var.set(text(self.lang, "installing")))
        self.root.after(150, self._set_progress_indeterminate)

    def _on_install_completed(self, install_dir: Path, manifest: InstallerManifest) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_var.set(100.0)
        self.installed_dir = install_dir
        app_path = install_dir / manifest.app_exe

        if self.no_launch:
            self.status_var.set(text(self.lang, "finish_manual_launch"))
            self.details_var.set(str(app_path))
            self._set_busy(False)
            self.open_folder_button.configure(state="normal")
            return

        self.status_var.set(text(self.lang, "launching"))
        self.details_var.set(str(app_path))

        try:
            launch_app(app_path)
        except Exception as exc:
            self.status_var.set(text(self.lang, "finish_manual_launch"))
            self.details_var.set(text(self.lang, "launch_error", detail=str(exc)))
        else:
            self.status_var.set(text(self.lang, "finish"))
            self.details_var.set(str(app_path))

        self._set_busy(False)
        self.open_folder_button.configure(state="normal")

    def _on_error(self, exc: Exception) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_busy(False)
        self.progress_var.set(0.0)

        detail = str(exc).strip() or exc.__class__.__name__
        if detail == "size_mismatch":
            message = text(self.lang, "size_mismatch")
        elif detail == "hash_mismatch":
            message = text(self.lang, "hash_mismatch")
        elif detail.isdigit():
            message = text(self.lang, "install_error", code=detail)
        elif self.manifest is None:
            message = text(self.lang, "manifest_error", detail=detail)
        elif "urlopen error" in detail.lower() or "timed out" in detail.lower():
            message = text(self.lang, "download_error", detail=detail)
        else:
            message = text(self.lang, "download_error", detail=detail)

        self.status_var.set(message)
        self.details_var.set(
            text(self.lang, "details_idle", homepage=(self.manifest.homepage_url if self.manifest else DEFAULT_HOMEPAGE_URL))
        )
        messagebox.showerror(text(self.lang, "error_title"), message)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv)
    root = tk.Tk()
    ttk.Style(root).theme_use("clam")
    DownloaderApp(root, manifest_url=args.manifest_url, no_launch=args.no_launch)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
