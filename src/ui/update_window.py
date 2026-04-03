from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

import customtkinter as ctk
import requests

from src.utils.i18n import tr
from .window_effects import apply_window_icon

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
SUCCESS_BG = "#ecf7ef"

_CHUNK = 65_536


class UpdateWindow(ctk.CTkToplevel):
    """Non-modal update dialog.

    States
    ------
    available   – shows release notes + "Update" / "Later" buttons
    downloading – shows progress bar + "Minimize" button
    ready       – shows "Install & Restart" button
    error       – shows error message + retry option
    """

    def __init__(
        self,
        parent,
        version: str,
        download_url: str,
        notes: str,
        ui_lang: str,
    ) -> None:
        super().__init__(parent)
        self._version = version
        self._download_url = download_url
        self._notes = notes
        self._ui_lang = ui_lang
        self._download_total = 0
        self._download_done = False
        self._minimized = False

        # Resolve paths for download integrity check
        filename = download_url.split("/")[-1].split("?")[0] or "MioTranslator-Setup.exe"
        self._final_path = Path(tempfile.gettempdir()) / filename
        self._wip_path = Path(tempfile.gettempdir()) / (filename + ".tmp")

        # Clean up any leftover incomplete download from a previous interrupted session
        if self._wip_path.exists():
            try:
                self._wip_path.unlink()
            except Exception:
                pass

        # Only treat the final file as a valid cached installer
        self._installer_path: Path | None = self._final_path if self._final_path.exists() else None

        self.title(self._t("update_title"))
        apply_window_icon(self)
        self.geometry("440x360")
        self._popup_size = (440, 360)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG)
        # Non-modal: no grab_set so the user can keep using the app
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self._build()

        # If installer was already cached, skip straight to install-ready
        if self._installer_path is not None:
            self.after(0, self._on_download_complete)

    # ------------------------------------------------------------------ i18n

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    # ------------------------------------------------------------------ build

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

        # ── version badge row ──
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
            text=f"v{self._version}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT,
            fg_color="#e8f2ff",
            corner_radius=999,
            padx=10,
            pady=2,
        )
        self._version_badge.pack(side="left", padx=(10, 0))

        # ── notes / progress area ──
        self._content_frame = ctk.CTkFrame(outer, fg_color="transparent")
        self._content_frame.pack(fill="both", expand=True, padx=20, pady=(12, 0))

        # notes textbox (state: available)
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

        # progress widgets (state: downloading / ready) — hidden initially
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

        # ── button row ──
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

    # ------------------------------------------------------------------ state transitions

    def _switch_to_downloading(self) -> None:
        """Swap notes box for progress widgets."""
        self._notes_box.pack_forget()

        self._progress_label.configure(text=self._t("update_downloading"))
        self._progress_label.pack(anchor="w", pady=(0, 6))
        self._progress_bar.pack(fill="x", pady=(0, 4))
        self._sub_label.configure(text="")
        self._sub_label.pack(anchor="w")

        self._btn_secondary.configure(
            text=self._t("update_minimize"),
            command=self._minimize_to_background,
        )
        self._btn_primary.configure(state="disabled", text=self._t("update_downloading_btn"))

    def _switch_to_ready(self) -> None:
        """Show install-ready state (works whether coming from downloading or directly from cache)."""
        # Ensure notes box is hidden and progress widgets are visible
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
        self._sub_label.configure(text=self._t("update_install_note"), text_color=TEXT_MUTED)

        # "稍后安装" — hides the window; next launch will detect cached installer and skip download
        self._btn_secondary.configure(
            text=self._t("update_install_later"),
            command=self.withdraw,
        )
        self._btn_secondary.pack(side="left")  # ensure visible if previously hidden
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

    # ------------------------------------------------------------------ download

    def _start_download(self) -> None:
        self._switch_to_downloading()
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self) -> None:
        try:
            resp = requests.get(self._download_url, stream=True, timeout=60)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            self._download_total = total

            downloaded = 0
            # Write to .tmp first — a complete file only exists after rename succeeds
            with open(self._wip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.after(0, lambda d=downloaded, t=total: self._on_progress(d, t))

            # Atomic rename: only now does the clean final file exist
            if self._final_path.exists():
                self._final_path.unlink()
            self._wip_path.rename(self._final_path)
            self._installer_path = self._final_path
            self.after(0, self._on_download_complete)
        except Exception as exc:
            # Clean up the partial .tmp on any error
            try:
                if self._wip_path.exists():
                    self._wip_path.unlink()
            except Exception:
                pass
            self.after(0, lambda m=str(exc): self._on_download_error(m))

    def _on_progress(self, downloaded: int, total: int) -> None:
        if not self.winfo_exists():
            return
        if total > 0:
            ratio = downloaded / total
            self._progress_bar.set(ratio)
            dl_mb = downloaded / 1_048_576
            total_mb = total / 1_048_576
            pct = int(ratio * 100)
            self._sub_label.configure(
                text=self._t("update_progress_mb", downloaded=f"{dl_mb:.1f}", total=f"{total_mb:.1f}", pct=pct)
            )
        else:
            dl_mb = downloaded / 1_048_576
            self._sub_label.configure(
                text=self._t("update_progress_mb_unknown", downloaded=f"{dl_mb:.1f}")
            )

    def _on_download_complete(self) -> None:
        if not self.winfo_exists():
            return
        self._download_done = True
        # If user minimized the window, bring it back
        if self._minimized:
            self._minimized = False
            self.deiconify()
            self.lift()
        self._switch_to_ready()

    def _on_download_error(self, message: str) -> None:
        if not self.winfo_exists():
            return
        self._switch_to_error(message)

    # ------------------------------------------------------------------ minimize

    def _minimize_to_background(self) -> None:
        self._minimized = True
        self.withdraw()

    # ------------------------------------------------------------------ install

    def _run_installer(self) -> None:
        if self._installer_path is None or not self._installer_path.exists():
            return
        try:
            subprocess.Popen([str(self._installer_path)])
        except Exception:
            pass
        # Give the installer a moment to launch, then close the app
        self.after(600, lambda: self.master.destroy() if self.master.winfo_exists() else None)

    # ------------------------------------------------------------------ close

    def _on_window_close(self) -> None:
        if self._minimized:
            return
        # Download complete: installer is on disk, safe to close — next launch detects it
        if self._download_done:
            self.destroy()
            return
        # Download in progress: minimize so the download continues in background
        try:
            bar_val = self._progress_bar.get()
        except Exception:
            bar_val = 0.0
        if bar_val > 0.0:
            self._minimize_to_background()
        else:
            self.destroy()
