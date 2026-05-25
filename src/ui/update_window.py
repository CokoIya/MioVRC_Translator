from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import customtkinter as ctk
import requests

from src.updater.update_checker import UpdateInfo, is_trusted_download_url
from src.utils.i18n import tr
from src.version import (
    TRUSTED_INSTALLER_SIGNER_THUMBPRINTS,
    TRUSTED_INSTALLER_SIGNER_SUBJECTS,
)
from .window_effects import apply_window_icon

logger = logging.getLogger(__name__)

BG = "#f5f5f7"
CARD_BG = "#ffffff"
CARD_BORDER = "#d8dde6"
ACCENT = "#0071e3"
ACCENT_HOVER = "#0059b8"
BTN_SECONDARY_BG = "#eef1f5"
BTN_SECONDARY_HOVER = "#e0e4ea"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#6e6e73"
TEXT_MUTED = "#8e8e93"
SUCCESS_COLOR = "#1f6b3d"

_CHUNK = 65_536


def _installer_filename(update_info: UpdateInfo) -> str:
    raw_name = update_info.installer_name
    if not raw_name:
        raw_name = Path(urlparse(update_info.download_url).path).name
    filename = Path(str(raw_name or "MioTranslator-Setup.exe")).name
    if not filename.lower().endswith(".exe"):
        raise ValueError("Update installer must be a Windows .exe file")
    return filename


def _verify_windows_signature(path: Path, *, required: bool = False) -> bool:
    trusted_thumbprints = {
        str(value or "").replace(" ", "").lower()
        for value in TRUSTED_INSTALLER_SIGNER_THUMBPRINTS
        if str(value or "").strip()
    }
    trusted_subjects = tuple(
        str(value or "").strip()
        for value in TRUSTED_INSTALLER_SIGNER_SUBJECTS
        if str(value or "").strip()
    )
    if not trusted_thumbprints and not trusted_subjects:
        logger.info("Installer Authenticode trust list is empty; using signed manifest SHA256 trust path")
        return False
    if os.name != "nt":
        if required:
            raise RuntimeError("Authenticode installer signature verification requires Windows")
        return False
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; "
        "if ($sig.Status -ne 'Valid') { "
        "Write-Error ($sig.Status.ToString() + ': ' + $sig.StatusMessage); exit 1 }; "
        "$cert = $sig.SignerCertificate; "
        "if ($null -eq $cert) { Write-Error 'Signer certificate is missing'; exit 1 }; "
        "$payload = [pscustomobject]@{ "
        "Thumbprint = $cert.Thumbprint; "
        "Subject = $cert.Subject }; "
        "$payload | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        if required:
            raise RuntimeError("PowerShell is required to verify the installer signature") from exc
        logger.info("PowerShell is unavailable; skipping optional installer Authenticode verification")
        return False
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if required:
            raise RuntimeError(detail or "Installer signature verification failed")
        logger.info("Optional installer Authenticode verification failed: %s", detail or "unknown error")
        return False
    try:
        payload = json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError as exc:
        if required:
            raise RuntimeError("Installer signature verification returned invalid metadata") from exc
        logger.info("Optional installer Authenticode verification returned invalid metadata")
        return False
    thumbprint = str(payload.get("Thumbprint", "") or "").replace(" ", "").lower()
    subject = str(payload.get("Subject", "") or "")
    if trusted_thumbprints and thumbprint in trusted_thumbprints:
        return True
    subject_folded = subject.casefold()
    if trusted_subjects and any(item.casefold() in subject_folded for item in trusted_subjects):
        return True
    if required:
        raise RuntimeError("Installer signer identity is not trusted")
    logger.info("Optional installer Authenticode signer is not trusted")
    return False


def _display_version(version: str) -> str:
    text = str(version or "").strip()
    if not text:
        return "v?"
    return text if text.lower().startswith("v") else f"v{text}"


def _localized_update_notes(update_info: UpdateInfo, ui_lang: str) -> str:
    localized = getattr(update_info, "localized_notes", None) or {}
    normalized = str(ui_lang or "").strip().lower().replace("_", "-")
    base = normalized.split("-", 1)[0]
    candidates = [normalized, base]
    if base == "zh":
        candidates.extend(["zh-cn", "zh-hans", "zh"])
    for lang in candidates:
        note = str(localized.get(lang, "") or "").strip()
        if note:
            return note
    return update_info.notes


class UpdateWindow(ctk.CTkToplevel):
    """Non-modal update dialog with safe background download lifecycle."""

    def __init__(
        self,
        parent,
        update_info: UpdateInfo,
        ui_lang: str,
    ) -> None:
        super().__init__(parent)
        self._update_info = update_info
        self._version = update_info.version
        self._download_url = update_info.download_url
        self._notes = _localized_update_notes(update_info, ui_lang)
        self._expected_size = update_info.size_bytes
        self._expected_sha256 = update_info.sha256.lower()
        self._ui_lang = ui_lang
        self._download_total = 0
        self._download_done = False
        self._downloading = False
        self._minimized = False
        self._destroying = False
        self._download_thread: threading.Thread | None = None

        filename = _installer_filename(update_info)
        self._final_path = Path(tempfile.gettempdir()) / filename
        self._wip_path = Path(tempfile.gettempdir()) / (filename + ".tmp")

        if self._wip_path.exists():
            try:
                self._wip_path.unlink()
            except Exception:
                pass

        # Avoid reusing a same-named installer from a previous run; the new
        # download must pass the current manifest checksum before it can run.
        self._installer_path: Path | None = None

        self.title(self._t("update_title"))
        apply_window_icon(self)
        self.geometry("440x360")
        self._popup_size = (440, 360)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self._build()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _schedule_if_alive(self, callback, delay_ms: int = 0) -> bool:
        if self._destroying:
            return False
        try:
            if not self.winfo_exists():
                return False
        except Exception:
            return False
        try:
            self.after(delay_ms, callback)
            return True
        except Exception:
            return False

    def _build(self) -> None:
        outer = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=20,
            border_width=1,
            border_color=CARD_BORDER,
        )
        outer.pack(fill="both", expand=True, padx=14, pady=14)
        outer.pack_propagate(True)

        badge_row = ctk.CTkFrame(outer, fg_color="transparent")
        badge_row.pack(fill="x", padx=20, pady=(18, 0))

        self._title_label = ctk.CTkLabel(
            badge_row,
            text=self._t("update_title"),
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=TEXT_PRI,
            anchor="w",
        )
        self._title_label.pack(side="left")

        self._version_badge = ctk.CTkLabel(
            badge_row,
            text=_display_version(self._version),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
            fg_color="#e8f2ff",
            corner_radius=999,
            padx=10,
            pady=2,
        )
        self._version_badge.pack(side="left", padx=(10, 0))

        self._content_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._content_frame.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        self._notes_box = ctk.CTkTextbox(
            self._content_frame,
            fg_color="#f7f8fc",
            border_color=CARD_BORDER,
            border_width=1,
            corner_radius=12,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SEC,
            wrap="word",
            state="normal",
            activate_scrollbars=True,
        )
        self._notes_box.insert("1.0", self._notes or self._t("update_no_notes"))
        self._notes_box.configure(state="disabled")
        self._notes_box.pack(fill="both", expand=True)

        self._progress_label = ctk.CTkLabel(
            self._content_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SEC,
            anchor="w",
        )
        self._progress_bar = ctk.CTkProgressBar(
            self._content_frame,
            fg_color="#e0e4ea",
            progress_color=ACCENT,
            corner_radius=6,
            height=8,
        )
        self._progress_bar.set(0)

        self._sub_label = ctk.CTkLabel(
            self._content_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )

        self._btn_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._btn_frame.pack(fill="x", padx=20, pady=(12, 18))

        self._btn_secondary = ctk.CTkButton(
            self._btn_frame,
            text=self._t("update_later"),
            width=110,
            height=34,
            fg_color=BTN_SECONDARY_BG,
            hover_color=BTN_SECONDARY_HOVER,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=12,
            text_color=TEXT_PRI,
            font=ctk.CTkFont(size=12),
            command=self._on_window_close,
        )
        self._btn_secondary.pack(side="left")

        self._btn_primary = ctk.CTkButton(
            self._btn_frame,
            text=self._t("update_now"),
            width=130,
            height=34,
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            corner_radius=12,
            text_color="#ffffff",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._start_download,
        )
        self._btn_primary.pack(side="right")

    def _switch_to_downloading(self) -> None:
        self._notes_box.pack_forget()

        self._progress_label.configure(text=self._t("update_downloading"))
        self._progress_label.pack(anchor="w", pady=(0, 6))
        self._progress_bar.pack(fill="x", pady=(0, 4))
        self._sub_label.configure(text="", text_color=TEXT_MUTED)
        self._sub_label.pack(anchor="w")

        self._btn_secondary.configure(
            text=self._t("update_minimize"),
            command=self._minimize_to_background,
        )
        self._btn_primary.configure(state="disabled", text=self._t("update_downloading_btn"))

    def _switch_to_ready(self) -> None:
        self._notes_box.pack_forget()
        if not self._progress_label.winfo_manager():
            self._progress_label.pack(anchor="w", pady=(0, 6))
        if not self._progress_bar.winfo_manager():
            self._progress_bar.pack(fill="x", pady=(0, 4))
        if not self._sub_label.winfo_manager():
            self._sub_label.pack(anchor="w")

        self._title_label.configure(text=self._t("update_ready_title"))
        self._progress_label.configure(
            text=self._t("update_ready_desc"),
            text_color=SUCCESS_COLOR,
        )
        self._progress_bar.set(1.0)
        self._sub_label.configure(
            text=self._t("update_install_note"),
            text_color=TEXT_MUTED,
        )

        self._btn_secondary.configure(
            text=self._t("update_install_later"),
            command=self.withdraw,
        )
        self._btn_secondary.pack(side="left")
        self._btn_primary.configure(
            state="normal",
            text=self._t("update_install_now"),
            fg_color=SUCCESS_COLOR,
            hover_color="#175c34",
            command=self._run_installer,
        )

    def _switch_to_error(self, message: str) -> None:
        self._progress_label.configure(
            text=self._t("update_error"),
            text_color="#b91c1c",
        )
        self._sub_label.configure(text=message, text_color=TEXT_MUTED)
        self._btn_secondary.configure(text=self._t("update_close"), command=self.destroy)
        self._btn_primary.configure(
            state="normal",
            text=self._t("update_retry"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            command=self._start_download,
        )

    def _start_download(self) -> None:
        if self._downloading and self._download_thread and self._download_thread.is_alive():
            return
        self._downloading = True
        self._switch_to_downloading()
        self._download_thread = threading.Thread(
            target=self._download_worker,
            daemon=True,
        )
        self._download_thread.start()

    def _download_worker(self) -> None:
        try:
            if not self._expected_sha256:
                raise RuntimeError("Update manifest is missing SHA256 verification data")
            if not is_trusted_download_url(self._download_url):
                raise RuntimeError("Update download URL is not trusted")
            response = requests.get(self._download_url, stream=True, timeout=60)
            try:
                response.raise_for_status()
                if not is_trusted_download_url(response.url, allow_release_asset_redirect=True):
                    raise RuntimeError("Update download redirected to an untrusted URL")
                total = int(response.headers.get("content-length", 0))
                if self._expected_size is not None and total > 0 and total != self._expected_size:
                    raise RuntimeError("Update download size does not match the manifest")
                self._download_total = total

                hasher = hashlib.sha256()
                downloaded = 0
                with open(self._wip_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        self._schedule_if_alive(
                            lambda d=downloaded, t=total or self._expected_size or 0: self._on_progress(d, t)
                        )

                if self._expected_size is not None and downloaded != self._expected_size:
                    raise RuntimeError("Update download size does not match the manifest")
                if hasher.hexdigest().lower() != self._expected_sha256:
                    raise RuntimeError("Update download checksum verification failed")
                _verify_windows_signature(self._wip_path, required=False)
                self._wip_path.replace(self._final_path)
                self._installer_path = self._final_path
                self._schedule_if_alive(self._on_download_complete)
            finally:
                response.close()
        except Exception as exc:
            try:
                if self._wip_path.exists():
                    self._wip_path.unlink()
            except Exception:
                pass
            self._schedule_if_alive(lambda m=str(exc): self._on_download_error(m))

    def _on_progress(self, downloaded: int, total: int) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if total > 0:
            ratio = downloaded / total
            dl_mb = downloaded / 1_048_576
            total_mb = total / 1_048_576
            pct = int(ratio * 100)
            self._progress_bar.set(ratio)
            self._sub_label.configure(
                text=self._t(
                    "update_progress_mb",
                    downloaded=f"{dl_mb:.1f}",
                    total=f"{total_mb:.1f}",
                    pct=pct,
                )
            )
            return

        dl_mb = downloaded / 1_048_576
        self._sub_label.configure(
            text=self._t("update_progress_mb_unknown", downloaded=f"{dl_mb:.1f}")
        )

    def _on_download_complete(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._downloading = False
        self._download_done = True
        if self._minimized:
            self._minimized = False
            self.deiconify()
            self.lift()
        self._switch_to_ready()

    def _on_download_error(self, message: str) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._downloading = False
        self._switch_to_error(message)

    def _minimize_to_background(self) -> None:
        self._minimized = True
        self.withdraw()

    def _run_installer(self) -> None:
        if self._installer_path is None or not self._installer_path.exists():
            return
        try:
            subprocess.Popen(
                [
                    str(self._installer_path),
                    "/CLOSEAPPLICATIONS",
                    "/NORESTARTAPPLICATIONS",
                ]
            )
        except Exception:
            pass
        self._schedule_if_alive(self._destroy_master_if_alive, delay_ms=200)

    def _on_window_close(self) -> None:
        if self._minimized:
            return
        if self._download_done:
            self.destroy()
            return
        if self._downloading:
            self._minimize_to_background()
            return
        self.destroy()

    def _destroy_master_if_alive(self) -> None:
        master = self.master
        if master is None:
            return
        try:
            if master.winfo_exists():
                master.destroy()
        except Exception:
            pass

    def destroy(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        super().destroy()
