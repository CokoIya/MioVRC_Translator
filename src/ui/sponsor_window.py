"""Sponsor list + donation window with a two-page layout."""
from __future__ import annotations

import logging
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from src.ui.window_effects import apply_window_icon, clamp_window_geometry
from src.utils.locale_detect import get_system_language
from src.utils.sponsor_fetcher import get_sponsors

logger = logging.getLogger(__name__)

BG = "#f5f5f7"
ACCENT = "#0071e3"
ACCENT_HOVER = "#0059b8"
TEXT_PRI = "#1d1d1f"
TEXT_SEC = "#6e6e73"
TEXT_MUTED = "#8e8e93"
GOLD = "#D4A638"
GOLD_BG = "#fffbf0"
NOTICE_BG = "#fff7df"
NOTICE_BORDER = "#f1d27b"
PANEL_BG = "#ffffff"
PANEL_BORDER = "#e4e7ed"
WINDOW_WIDTH = 520
MIN_WINDOW_HEIGHT = 420
LIST_FONT_SIZE = 20
QR_MAX_EDGE = 240
SCREEN_MARGIN_Y = 80

_WINDOW_TITLES = {
    "zh": "感谢名单 · 赞助入口",
    "ja": "感謝リスト · 支援",
    "en": "Sponsors · Support",
    "ko": "감사 명단 · 후원",
    "ru": "Список благодарностей · Поддержка",
}
_LIST_PAGE_TITLE = {
    "zh": "感谢以下赞助者",
    "ja": "ご支援ありがとうございます",
    "en": "Thanks to these supporters",
    "ko": "후원해 주신 분들께 감사드립니다",
    "ru": "Спасибо этим спонсорам",
}
_QR_PAGE_TITLE = {
    "zh": "赞助页",
    "ja": "支援ページ",
    "en": "Donate Page",
    "ko": "후원 페이지",
    "ru": "Страница поддержки",
}
_LIST_NOTICE = {
    "zh": "赞助打赏请点击赞助页，点击后会自动翻页到二维码页。",
    "ja": "ご支援は「支援ページ」をクリックしてください。自動で QR 画面に切り替わります。",
    "en": "Please click the Donate Page button. It will flip to the QR page automatically.",
    "ko": "후원은 후원 페이지를 눌러 주세요. 누르면 QR 페이지로 자동 전환됩니다.",
    "ru": "Нажмите кнопку страницы поддержки. Откроется страница с QR-кодом.",
}
_QR_TIP = {
    "zh": "扫描二维码即可赞助打赏，助力作者创作下一个插件和社区。",
    "ja": "QR を読み取ってご支援いただけると、次のプラグインとコミュニティ制作の力になります。",
    "en": "Scan the QR code to support the project and help build the next plugin and community.",
    "ko": "QR 코드를 스캔해 후원해 주시면 다음 플러그인과 커뮤니티 제작에 큰 힘이 됩니다.",
    "ru": "Сканируйте QR-код, чтобы поддержать автора и помочь сделать следующий плагин и сообщество.",
}
_LOADING_TEXT = {
    "zh": "名单加载中...",
    "ja": "名簿を読み込み中...",
    "en": "Loading sponsors...",
    "ko": "후원자 명단을 불러오는 중...",
    "ru": "Загрузка списка...",
}
_NO_SPONSORS = {
    "zh": "（暂无赞助者记录）",
    "ja": "（まだ記録がありません）",
    "en": "(No records yet)",
    "ko": "(아직 기록이 없습니다)",
    "ru": "(Пока нет записей)",
}
_TOTAL_FMT = {
    "zh": "共 {n} 位，感谢每一位的支持",
    "ja": "合計 {n} 名、ありがとうございます",
    "en": "{n} supporters - thank you all",
    "ko": "총 {n}명 - 응원 감사합니다",
    "ru": "Всего {n} человек - спасибо каждому",
}
_REFRESH_BTN = {
    "zh": "刷新",
    "ja": "更新",
    "en": "Refresh",
    "ko": "새로고침",
    "ru": "Обновить",
}
_QR_REFRESH_HINT = {
    "zh": "名单更新请返回上一页点击刷新。",
    "ja": "リストを更新するには前のページに戻って更新してください。",
    "en": "Go back to the list page to refresh the sponsors.",
    "ko": "명단을 새로고침하려면 목록 페이지로 돌아가세요.",
    "ru": "Чтобы обновить список, вернитесь на страницу списка.",
}
_DONATE_PAGE_BTN = {
    "zh": "赞助页",
    "ja": "支援ページ",
    "en": "Donate Page",
    "ko": "후원 페이지",
    "ru": "Страница поддержки",
}
_BACK_BTN = {
    "zh": "返回名单",
    "ja": "名簿に戻る",
    "en": "Back to List",
    "ko": "명단으로 돌아가기",
    "ru": "Назад к списку",
}
_NO_IMAGE_TEXT = {
    "zh": "请将赞助二维码图片放到 assets/sponsor_qr.png",
    "ja": "支援用 QR 画像を assets/sponsor_qr.png に配置してください",
    "en": "Put the sponsor QR image at assets/sponsor_qr.png",
    "ko": "후원 QR 이미지를 assets/sponsor_qr.png 에 넣어 주세요",
    "ru": "Поместите QR-код спонсора в assets/sponsor_qr.png",
}


def _t(table: dict, lang: str | None = None) -> str:
    l = (lang or get_system_language()).lower().split("-")[0]
    return table.get(l) or table.get("en") or next(iter(table.values()), "")


class SponsorWindow(ctk.CTkToplevel):
    """Combined sponsor list + donation window with a stable fixed size."""

    def __init__(
        self,
        parent,
        sponsor_image_path: Path | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self._image_path = sponsor_image_path
        self._on_close = on_close
        self._destroying = False
        self._sponsor_data: dict = {}
        self._ctk_img = None
        self._current_page: str | None = None
        self._page_visible = False
        self._window_height = self._calculate_window_height()

        self._lang = get_system_language().lower().split("-")[0]
        self.title(_t(_WINDOW_TITLES, self._lang))
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        apply_window_icon(self)

        self._build(self._lang)

        self.withdraw()
        self.update_idletasks()
        self._sync_locked_geometry(center=True)
        self.deiconify()

        get_sponsors(self._on_sponsors_loaded)

    def _build(self, lang: str) -> None:
        root = ctk.CTkFrame(self, fg_color=BG)
        root.pack(fill="both", expand=True)

        self._page_header = ctk.CTkLabel(
            root,
            text=_t(_LIST_PAGE_TITLE, lang),
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_PRI,
            anchor="w",
        )
        self._page_header.pack(fill="x", padx=12, pady=(10, 6))

        self._page_host = ctk.CTkFrame(root, fg_color="transparent")
        self._page_host.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._page_host.pack_propagate(False)

        self._page_list = ctk.CTkFrame(self._page_host, fg_color="transparent")
        self._page_qr = ctk.CTkFrame(self._page_host, fg_color="transparent")

        self._build_list_page(self._page_list, lang)
        self._build_qr_page(self._page_qr, lang)
        self._show_page("list", animate=False)

    def _build_list_page(self, parent, lang: str) -> None:
        self._list_container = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            corner_radius=0,
            height=self._list_area_height(),
        )
        self._list_container.pack(fill="x", expand=False, pady=(0, 8))

        self._loading_label = ctk.CTkLabel(
            self._list_container,
            text=_t(_LOADING_TEXT, lang),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        )
        self._loading_label.pack(pady=12)

        self._notice_box = ctk.CTkFrame(
            parent,
            fg_color=NOTICE_BG,
            border_width=1,
            border_color=NOTICE_BORDER,
            corner_radius=10,
        )
        self._notice_box.pack(fill="x", pady=(0, 8))

        self._notice_label = ctk.CTkLabel(
            self._notice_box,
            text=_t(_LIST_NOTICE, lang),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#8a5a00",
            justify="left",
            wraplength=WINDOW_WIDTH - 56,
            anchor="w",
        )
        self._notice_label.pack(fill="x", padx=12, pady=(10, 8))

        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(fill="x")

        ctk.CTkButton(
            footer,
            text=_t(_REFRESH_BTN, lang),
            width=64,
            height=28,
            font=ctk.CTkFont(size=10),
            fg_color="#eef1f5",
            hover_color="#e0e4ea",
            text_color=TEXT_SEC,
            corner_radius=8,
            command=self._refresh,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            footer,
            text=_t(_DONATE_PAGE_BTN, lang),
            width=104,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            corner_radius=10,
            command=lambda: self._show_page("qr"),
        ).pack(side="right")

        self._total_label = ctk.CTkLabel(
            footer,
            text="",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self._total_label.pack(side="left", fill="x", expand=True)

    def _build_qr_page(self, parent, lang: str) -> None:
        self._qr_tip_label = ctk.CTkLabel(
            parent,
            text=_t(_QR_TIP, lang),
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SEC,
            justify="left",
            wraplength=WINDOW_WIDTH - 24,
            anchor="w",
        )
        self._qr_tip_label.pack(fill="x", pady=(0, 4))

        self._qr_refresh_hint_label = ctk.CTkLabel(
            parent,
            text=_t(_QR_REFRESH_HINT, lang),
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=WINDOW_WIDTH - 24,
            anchor="w",
        )
        self._qr_refresh_hint_label.pack(fill="x", pady=(0, 8))

        self._qr_card = ctk.CTkFrame(
            parent,
            fg_color=PANEL_BG,
            border_width=1,
            border_color=PANEL_BORDER,
            corner_radius=14,
            height=self._qr_card_height(),
        )
        self._qr_card.pack(fill="x", expand=False)
        self._qr_card.pack_propagate(False)

        qr_inner = ctk.CTkFrame(self._qr_card, fg_color="transparent")
        qr_inner.pack(fill="both", expand=True, padx=14, pady=14)

        if self._image_path and self._image_path.exists():
            try:
                from PIL import Image

                image = Image.open(self._image_path)
                max_edge = self._qr_max_edge()
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:  # pragma: no cover - old Pillow fallback
                    resample = Image.LANCZOS
                image = image.copy()
                image.thumbnail((max_edge, max_edge), resample)
                self._ctk_img = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                image_label = ctk.CTkLabel(qr_inner, text="", image=self._ctk_img)
                image_label.image = self._ctk_img
                image_label.pack(expand=True)
            except Exception:
                logger.debug("Failed to load sponsor image", exc_info=True)
                self._no_image_label(qr_inner)
        else:
            self._no_image_label(qr_inner)

        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(fill="x", pady=(8, 0))

        ctk.CTkButton(
            footer,
            text=_t(_BACK_BTN, lang),
            width=104,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color="#eef1f5",
            hover_color="#e0e4ea",
            text_color=TEXT_SEC,
            corner_radius=10,
            command=lambda: self._show_page("list"),
        ).pack(side="right")

    def _qr_max_edge(self) -> int:
        available = self._content_fit_height() - 190
        width_limit = WINDOW_WIDTH - 84
        return max(140, min(QR_MAX_EDGE, available, width_limit))

    def _list_area_height(self) -> int:
        return max(220, self._content_fit_height() - 190)

    def _qr_card_height(self) -> int:
        return max(240, self._content_fit_height() - 200)

    def _no_image_label(self, parent) -> None:
        ctk.CTkLabel(
            parent,
            text=_t(_NO_IMAGE_TEXT, self._lang),
            font=ctk.CTkFont(size=10),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=WINDOW_WIDTH - 56,
        ).pack(padx=12, pady=16, anchor="w")

    def _show_page(self, page: str, *, animate: bool = True) -> None:
        if page not in {"list", "qr"}:
            return
        selected = self._page_list if page == "list" else self._page_qr
        if page == self._current_page and self._page_visible:
            return

        self._current_page = page
        self._page_header.configure(text=_t(_LIST_PAGE_TITLE if page == "list" else _QR_PAGE_TITLE, self._lang))

        self._page_list.pack_forget()
        self._page_qr.pack_forget()
        selected.pack(fill="both", expand=True)
        self._page_visible = True

        if animate:
            self._animate_page_switch()
        self._sync_locked_geometry()

    def _animate_page_switch(self) -> None:
        return

    def _calculate_window_height(self) -> int:
        try:
            screen_height = int(self.winfo_screenheight() or 0)
        except Exception:
            screen_height = 0
        screen_height = max(screen_height, 1)
        preferred = 520
        max_allowed = max(screen_height - 160, MIN_WINDOW_HEIGHT)
        return min(preferred, max_allowed)

    def _content_fit_height(self) -> int:
        height = getattr(self, "_window_height", 0)
        if height:
            return max(int(height), 1)
        return self._calculate_window_height()

    def _sync_locked_geometry(self, *, center: bool = False) -> None:
        self.update_idletasks()
        width = WINDOW_WIDTH
        height = self._content_fit_height()
        self._locked_height = height

        try:
            if center:
                px = self._parent.winfo_x()
                py = self._parent.winfo_y()
                pw = self._parent.winfo_width()
                ph = self._parent.winfo_height()
                x = px + (pw - width) // 2
                y = py + (ph - height) // 2
            else:
                x = self.winfo_x()
                y = self.winfo_y()
        except Exception:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = (sw - width) // 2
            y = (sh - height) // 2
        x, y, width, height = clamp_window_geometry(self, x=x, y=y, width=width, height=height)
        self.minsize(width, 1)
        self.maxsize(width, max(height, 1))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(width, height)
        self.maxsize(width, height)

    def _on_sponsors_loaded(self, data: dict) -> None:
        try:
            self.after(0, lambda: self._apply_data(data))
        except Exception:
            pass

    def _apply_data(self, data: dict) -> None:
        try:
            lang = self._lang
            self._sponsor_data = data
            tip_map = data.get("tip") or {}
            remote_tip = ""
            if isinstance(tip_map, dict):
                remote_tip = str(tip_map.get(lang) or tip_map.get("en") or tip_map.get("zh") or "").strip()
            if remote_tip:
                self._qr_tip_label.configure(text=f"{_t(_QR_TIP, lang)}\n{remote_tip}")
            else:
                self._qr_tip_label.configure(text=_t(_QR_TIP, lang))

            sponsors: list = data.get("sponsors") or []
            self._render_sponsor_list(sponsors, lang)
            self._sync_locked_geometry()
        except tk.TclError:
            pass

    def _render_sponsor_list(self, sponsors: list, lang: str) -> None:
        for child in self._list_container.winfo_children():
            child.destroy()

        names = [str(s.get("name") or "").strip() for s in (sponsors or []) if isinstance(s, dict) and s.get("name")]
        names = [name for name in names if name]

        if not names:
            ctk.CTkLabel(
                self._list_container,
                text=_t(_NO_SPONSORS, lang),
                font=ctk.CTkFont(size=11),
                text_color=TEXT_MUTED,
            ).pack(pady=12)
            self._total_label.configure(text="")
            return

        for name in names:
            card = ctk.CTkFrame(
                self._list_container,
                fg_color=GOLD_BG,
                border_width=1,
                border_color=PANEL_BORDER,
                corner_radius=10,
            )
            card.pack(fill="x", pady=(0, 6))
            ctk.CTkLabel(
                card,
                text=name,
                font=ctk.CTkFont(size=LIST_FONT_SIZE, weight="bold"),
                text_color=GOLD,
                justify="left",
                wraplength=WINDOW_WIDTH - 56,
                anchor="w",
            ).pack(fill="x", padx=12, pady=8)

        self._total_label.configure(text=_t(_TOTAL_FMT, lang).format(n=len(names)))

    def _refresh(self) -> None:
        lang = self._lang
        for child in self._list_container.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self._list_container,
            text=_t(_LOADING_TEXT, lang),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(pady=12)
        self._total_label.configure(text="")
        get_sponsors(self._on_sponsors_loaded, force_refresh=True)

    def destroy(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        on_close = self._on_close
        self._on_close = None
        try:
            super().destroy()
        finally:
            if on_close is not None:
                try:
                    on_close()
                except Exception:
                    logger.debug("Failed to notify sponsor window close", exc_info=True)
