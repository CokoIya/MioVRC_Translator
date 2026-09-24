# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""A kept translation, drawn as a panel in the headset.

VRHandsFrame's result panel: where the frame was, it stays until closed, it
can be grabbed and moved, and its header switches between the original and
the translation. Mio's shows either the read picture with its translations
laid on it (the card) or the text as pages, and every control is a large flat
button read from arm's length and clicked with the laser.

Painted, not built from widgets, for the same reason as the dashboard tab:
the whole panel is one hit-tested picture in an overlay texture. The panel
knows only what it is handed; every button reports an action id. Its look
(colour preset, background opacity, tooltips) is a :class:`PanelStyle`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen

PANEL_SIZE = (1000, 820)
MARGIN = 26
BAR_HEIGHT = 78
GAP = 12
RADIUS = 14
BUTTON_PX = 26
TITLE_PX = 28
MIN_TEXT_PX = 22
MAX_TEXT_PX = 60
DEFAULT_TEXT_PX = 34
CURSOR_RADIUS = 13

BACKGROUND = QColor(14, 16, 24, 238)
BODY = QColor(0, 0, 0, 90)
BUTTON = QColor(255, 255, 255, 28)
BUTTON_HOVER = QColor(255, 255, 255, 56)
BUTTON_PRESSED = QColor(255, 255, 255, 86)
ACTIVE = QColor(80, 160, 255, 225)
DANGER = QColor(220, 80, 80, 225)
TEXT = QColor(245, 247, 250)
TEXT_DIM = QColor(186, 194, 208)
CURSOR = QColor(255, 255, 255, 235)
CURSOR_RING = QColor(20, 24, 36, 200)



Rgba = tuple[int, int, int, int]


def _rgba(color: QColor) -> Rgba:
    return (color.red(), color.green(), color.blue(), color.alpha())


@dataclass(frozen=True)
class PanelStyle:
    """How a panel looks, as RGBA tuples; ``opacity`` scales the background only."""

    background: Rgba = _rgba(BACKGROUND)
    body: Rgba = _rgba(BODY)
    button: Rgba = _rgba(BUTTON)
    button_hover: Rgba = _rgba(BUTTON_HOVER)
    button_pressed: Rgba = _rgba(BUTTON_PRESSED)
    text: Rgba = _rgba(TEXT)
    text_dim: Rgba = _rgba(TEXT_DIM)
    opacity: float = 1.0
    # Resting the laser on a button shows what it does in the title bar.
    tooltips: bool = True

    def color(self, name: str) -> QColor:
        return QColor(*getattr(self, name))


PANEL_PRESETS = {
    "default": PanelStyle(),
    "dark": PanelStyle(
        background=(0, 0, 0, 245),
        body=(255, 255, 255, 10),
        button=(255, 255, 255, 24),
        button_hover=(255, 255, 255, 48),
        button_pressed=(255, 255, 255, 76),
        text=(240, 240, 240, 255),
        text_dim=(170, 170, 170, 255),
    ),
    "light": PanelStyle(
        background=(238, 240, 244, 245),
        body=(255, 255, 255, 200),
        button=(0, 0, 0, 20),
        button_hover=(0, 0, 0, 40),
        button_pressed=(0, 0, 0, 64),
        text=(22, 24, 30, 255),
        text_dim=(84, 90, 102, 255),
    ),
}


def panel_style(preset: str = "default", *, opacity: float = 1.0, tooltips: bool = True) -> PanelStyle:
    base = PANEL_PRESETS.get(str(preset or "").lower(), PANEL_PRESETS["default"])
    try:
        opacity = max(0.3, min(1.0, float(opacity)))
    except (TypeError, ValueError):
        opacity = 1.0
    return replace(base, opacity=opacity, tooltips=bool(tooltips))


# Font families: the same Song face as the headset board and the plates.
TEXT_FAMILIES = ["SimSun", "宋体", "NSimSun", "MS Mincho", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic", "Segoe UI"]


@dataclass(frozen=True)
class PanelButton:
    action: str
    label: str
    rect: QRect
    active: bool = False
    danger: bool = False


def _font(px: int, *, bold: bool = False, families: list[str] | None = None) -> QFont:
    font = QFont()
    if families:
        font.setFamilies(families)
    font.setPixelSize(max(1, int(px)))
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.DemiBold)
    return font


class VRResultPanelView:
    """Layout, painting and hit testing for one kept result (or the tutorial)."""

    def __init__(self, texts: dict[str, str], style: PanelStyle | None = None) -> None:
        self._texts = dict(texts or {})
        self._style = style or PanelStyle()
        self._links: list[str] = []
        self._title = ""
        self._card: QImage | None = None
        self._picture: QImage | None = None
        self._pairs: list[tuple[str, str]] = []
        self._lines: list[str] = []
        self._tutorial = False
        self._mode = "picture"
        self._show_original = False
        self._text_px = DEFAULT_TEXT_PX
        self._page = 0
        self._pages: list[list[str]] = []
        self._pinned = False
        self._buttons: list[PanelButton] = []
        self._dirty = True

    # ------------------------------------------------------------ content
    @property
    def size(self) -> tuple[int, int]:
        return PANEL_SIZE

    def set_result(
        self,
        *,
        title: str,
        card: QImage | None,
        picture: QImage | None,
        pairs: list[tuple[str, str]],
        pinned: bool = False,
        links: list[str] | None = None,
    ) -> None:
        self._tutorial = False
        self._links = [str(link) for link in (links or []) if str(link or "").strip()]
        self._title = str(title or "")
        self._card = card if card is not None and not card.isNull() else None
        self._picture = picture if picture is not None and not picture.isNull() else None
        self._pairs = [(str(o or ""), str(t or "")) for o, t in pairs]
        self._mode = "picture" if self._card is not None else "text"
        self._pinned = bool(pinned)
        self._page = 0
        self._dirty = True

    def set_style(self, style: PanelStyle) -> None:
        self._style = style or PanelStyle()
        self._dirty = True

    @property
    def style(self) -> PanelStyle:
        return self._style

    @property
    def links(self) -> list[str]:
        return list(self._links)

    def set_tutorial(self, title: str, lines: list[str]) -> None:
        self._tutorial = True
        self._links = []
        self._title = str(title or "")
        self._lines = [str(line) for line in lines if str(line).strip()]
        self._mode = "text"
        self._page = 0
        self._dirty = True

    def set_pinned(self, pinned: bool) -> None:
        if bool(pinned) != self._pinned:
            self._pinned = bool(pinned)
            self._dirty = True

    @property
    def pinned(self) -> bool:
        return self._pinned

    @property
    def tutorial(self) -> bool:
        return self._tutorial

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def show_original(self) -> bool:
        return self._show_original

    @property
    def text_px(self) -> int:
        return self._text_px

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        if self._dirty:
            self._layout()
        return max(1, len(self._pages))

    def apply(self, action: str) -> bool:
        """Handle the panel's own buttons; True when the picture changed.

        Pin, close, copy, chatbox and open_link are the owner's business and
        return False.
        """

        if action == "view" and self._card is not None and not self._tutorial:
            self._mode = "text" if self._mode == "picture" else "picture"
            self._page = 0
        elif action == "original" and not self._tutorial:
            self._show_original = not self._show_original
            self._page = 0
        elif action == "font_up":
            self._text_px = min(MAX_TEXT_PX, self._text_px + 4)
            self._page = 0
        elif action == "font_down":
            self._text_px = max(MIN_TEXT_PX, self._text_px - 4)
            self._page = 0
        elif action == "page_next":
            if self._page + 1 >= self.page_count:
                return False
            self._page += 1
        elif action == "page_prev":
            if self._page <= 0:
                return False
            self._page -= 1
        else:
            return False
        self._dirty = True
        return True

    # ------------------------------------------------------------ layout
    def _label(self, key: str, default: str = "") -> str:
        return str(self._texts.get(key, default) or default)

    def _body_rect(self) -> QRect:
        width, height = PANEL_SIZE
        return QRect(MARGIN, MARGIN + BAR_HEIGHT + GAP, width - 2 * MARGIN, height - 2 * MARGIN - 2 * (BAR_HEIGHT + GAP))

    def _text_rows(self) -> list[str]:
        if self._tutorial:
            return list(self._lines)
        rows = []
        for original, translated in self._pairs:
            text = original if self._show_original else (translated or original)
            if text.strip():
                rows.append(text.strip())
        return rows

    def _paginate(self) -> list[list[str]]:
        body = self._body_rect()
        font = _font(self._text_px, families=TEXT_FAMILIES)
        metrics = QFontMetrics(font)
        line_h = metrics.lineSpacing()
        usable_h = body.height() - 24
        pages: list[list[str]] = [[]]
        used = 0
        for row in self._text_rows():
            wrapped = metrics.boundingRect(
                QRect(0, 0, body.width() - 36, 10_000), int(Qt.TextFlag.TextWordWrap), row
            ).height()
            block = max(line_h, wrapped) + int(line_h * 0.45)
            if used and used + block > usable_h:
                pages.append([])
                used = 0
            pages[-1].append(row)
            used += block
        return [page for page in pages if page] or [[]]

    def _layout(self) -> None:
        width, height = PANEL_SIZE
        self._buttons = []
        self._pages = self._paginate() if self._mode == "text" else [[]]
        self._page = max(0, min(self._page, len(self._pages) - 1))
        top = MARGIN

        def row(items: list[tuple[str, str, bool, bool]], y: int, right_align: bool = False) -> None:
            count = len(items)
            if not count:
                return
            button_w = 150 if right_align else int((width - 2 * MARGIN - GAP * (count - 1)) / count)
            x = width - MARGIN - count * button_w - GAP * (count - 1) if right_align else MARGIN
            for action, label, active, danger in items:
                self._buttons.append(PanelButton(action, label, QRect(x, y, button_w, BAR_HEIGHT), active, danger))
                x += button_w + GAP

        header: list[tuple[str, str, bool, bool]] = []
        if not self._tutorial:
            if self._links:
                header.append(("open_link", self._label("open_link"), False, False))
            if self._card is not None:
                header.append(
                    (
                        "view",
                        self._label("text_view") if self._mode == "picture" else self._label("picture_view"),
                        False,
                        False,
                    )
                )
            header.append(
                (
                    "original",
                    self._label("show_translation") if self._show_original else self._label("show_original"),
                    self._show_original,
                    False,
                )
            )
        row(header, top, right_align=True)

        footer: list[tuple[str, str, bool, bool]] = []
        if self._mode == "text":
            footer += [("font_down", "A-", False, False), ("font_up", "A+", False, False)]
            if len(self._pages) > 1:
                footer += [("page_prev", "◀", False, False), ("page_next", "▶", False, False)]
        if not self._tutorial:
            footer.append(("copy", self._label("copy"), False, False))
            footer.append(("chatbox", self._label("chatbox"), False, False))
            footer.append(("pin", self._label("unpin") if self._pinned else self._label("pin"), self._pinned, False))
        footer.append(("close", self._label("close"), False, True))
        row(footer, height - MARGIN - BAR_HEIGHT)
        self._dirty = False

    def hit_test(self, x: float, y: float) -> str | None:
        if self._dirty:
            self._layout()
        px, py = int(x), int(y)
        for button in self._buttons:
            if button.rect.contains(px, py):
                return button.action
        return None

    # ------------------------------------------------------------ painting
    def render_image(
        self,
        *,
        hover: str | None = None,
        pressed: str | None = None,
        cursor: tuple[float, float] | None = None,
        dwell: float = 0.0,
    ) -> QImage:
        if self._dirty:
            self._layout()
        width, height = PANEL_SIZE
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            style = self._style
            background = style.color("background")
            background.setAlpha(int(round(background.alpha() * max(0.3, min(1.0, style.opacity)))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(QRectF(0, 0, width, height), 26, 26)

            # Title, left of the header buttons; while the laser rests on a
            # button, what that button does.
            header_buttons = [b for b in self._buttons if b.rect.top() == MARGIN]
            title_right = min((b.rect.left() for b in header_buttons), default=width - MARGIN) - GAP
            tip = self._label(f"tip_{hover}") if (style.tooltips and hover) else ""
            painter.setFont(_font(TITLE_PX, bold=not tip))
            painter.setPen(style.color("text_dim") if tip else style.color("text"))
            title_rect = QRect(MARGIN + 6, MARGIN, max(10, title_right - MARGIN - 6), BAR_HEIGHT)
            painter.drawText(
                title_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                QFontMetrics(painter.font()).elidedText(
                    tip or self._title, Qt.TextElideMode.ElideRight, title_rect.width()
                ),
            )

            body = self._body_rect()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(style.color("body"))
            painter.drawRoundedRect(QRectF(body), RADIUS, RADIUS)
            if self._mode == "picture":
                self._paint_picture(painter, body)
            else:
                self._paint_text(painter, body)

            for button in self._buttons:
                self._paint_button(painter, button, hover, pressed)

            if cursor is not None:
                cx, cy = float(cursor[0]), float(cursor[1])
                if 0 <= cx <= width and 0 <= cy <= height:
                    pen = QPen(CURSOR_RING)
                    pen.setWidth(4)
                    painter.setPen(pen)
                    painter.setBrush(CURSOR)
                    painter.drawEllipse(QRectF(cx - CURSOR_RADIUS, cy - CURSOR_RADIUS, 2 * CURSOR_RADIUS, 2 * CURSOR_RADIUS))
                    if dwell > 0:
                        ring = QPen(CURSOR)
                        ring.setWidth(6)
                        painter.setPen(ring)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        radius = CURSOR_RADIUS * 2.2
                        span = int(round(-360.0 * 16 * max(0.0, min(1.0, float(dwell)))))
                        painter.drawArc(QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius), 90 * 16, span)
        finally:
            painter.end()
        return image

    def _paint_picture(self, painter: QPainter, body: QRect) -> None:
        picture = self._picture if (self._show_original and self._picture is not None) else self._card
        if picture is None:
            return
        area = body.adjusted(10, 10, -10, -10)
        scale = min(area.width() / max(1, picture.width()), area.height() / max(1, picture.height()))
        w, h = picture.width() * scale, picture.height() * scale
        target = QRectF(area.left() + (area.width() - w) / 2.0, area.top() + (area.height() - h) / 2.0, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(target, RADIUS, RADIUS)
        painter.save()
        painter.setClipPath(clip)
        painter.drawImage(target, picture)
        painter.restore()

    def _paint_text(self, painter: QPainter, body: QRect) -> None:
        rows = self._pages[self._page] if self._pages else []
        font = _font(self._text_px, families=TEXT_FAMILIES)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        line_h = metrics.lineSpacing()
        y = body.top() + 14
        for row_text in rows:
            rect = QRect(body.left() + 18, y, body.width() - 36, body.bottom() - y)
            bounds = metrics.boundingRect(rect, int(Qt.TextFlag.TextWordWrap), row_text)
            painter.setPen(
                self._style.color("text_dim") if (self._show_original and not self._tutorial) else self._style.color("text")
            )
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), row_text)
            y += max(line_h, bounds.height()) + int(line_h * 0.45)
        if len(self._pages) > 1:
            painter.setFont(_font(20))
            painter.setPen(self._style.color("text_dim"))
            painter.drawText(
                QRect(body.left(), body.bottom() - 30, body.width() - 14, 26),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{self._page + 1}/{len(self._pages)}",
            )

    def _paint_button(self, painter: QPainter, button: PanelButton, hover: str | None, pressed: str | None) -> None:
        style = self._style
        if button.action == pressed:
            fill = style.color("button_pressed")
        elif button.danger:
            fill = DANGER if button.action == hover else QColor(DANGER.red(), DANGER.green(), DANGER.blue(), 150)
        elif button.active:
            fill = ACTIVE
        elif button.action == hover:
            fill = style.color("button_hover")
        else:
            fill = style.color("button")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(button.rect, RADIUS, RADIUS)
        font = _font(BUTTON_PX, bold=button.active)
        painter.setFont(font)
        # Coloured buttons carry white text whatever the preset.
        painter.setPen(TEXT if (button.active or button.danger) else style.color("text"))
        label = QFontMetrics(font).elidedText(button.label, Qt.TextElideMode.ElideRight, button.rect.width() - 16)
        painter.drawText(button.rect, int(Qt.AlignmentFlag.AlignCenter), label)
