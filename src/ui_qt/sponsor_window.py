from __future__ import annotations

from collections.abc import Callable
import logging
import weakref
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language
from src.utils.sponsor_fetcher import get_sponsors

logger = logging.getLogger(__name__)

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 520
QR_MAX_EDGE = 240


class _SponsorBridge(QObject):
    loaded = Signal(dict)


class SponsorWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        sponsor_image_path: Path | None = None,
        on_close: Callable[[], None] | None = None,
        ui_language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_path = sponsor_image_path
        self._on_close = on_close
        self._destroying = False
        parent_language = str(getattr(parent, "_ui_lang", "") or "").strip()
        self._ui_lang = normalize_ui_language(ui_language or parent_language)
        self._last_data: dict = {}
        self._data_loaded = False
        self._request_generation = 0
        self._bridge = _SponsorBridge(self)
        self._bridge.loaded.connect(self._apply_data)

        self.setWindowTitle(self._t("sponsor_window_title"))
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._build()
        self._center_on_parent(parent)
        self._request_sponsors()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self._ui_lang, key, **kwargs)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._page_header = QLabel(self._t("sponsor_list_title"))
        self._page_header.setObjectName("pageHeader")
        root.addWidget(self._page_header)

        self._stack = QStackedWidget()
        self._list_page = self._build_list_page()
        self._qr_page = self._build_qr_page()
        self._stack.addWidget(self._list_page)
        self._stack.addWidget(self._qr_page)
        root.addWidget(self._stack, 1)

        self._apply_style()

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._list_area = QScrollArea()
        self._list_area.setWidgetResizable(True)
        self._list_area.setFrameShape(QFrame.Shape.NoFrame)
        self._list_content = QWidget()
        self._list_layout = QVBoxLayout(self._list_content)
        self._list_layout.setContentsMargins(2, 2, 2, 2)
        self._list_layout.setSpacing(6)
        self._loading_label = QLabel(self._t("sponsor_loading"))
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.addWidget(self._loading_label)
        self._list_layout.addStretch(1)
        self._list_area.setWidget(self._list_content)
        layout.addWidget(self._list_area, 1)

        self._notice_label = QLabel(self._t("sponsor_list_notice"))
        self._notice_label.setObjectName("noticeLabel")
        self._notice_label.setWordWrap(True)
        layout.addWidget(self._notice_label)

        footer = QHBoxLayout()
        self._total_label = QLabel("")
        self._total_label.setObjectName("mutedLabel")
        footer.addWidget(self._total_label, 1)
        self._refresh_btn = QPushButton(self._t("sponsor_refresh"))
        self._refresh_btn.clicked.connect(self._refresh)
        footer.addWidget(self._refresh_btn)
        self._donate_btn = QPushButton(self._t("sponsor_donate_page"))
        self._donate_btn.setObjectName("primaryButton")
        self._donate_btn.clicked.connect(lambda: self._show_page("qr"))
        footer.addWidget(self._donate_btn)
        layout.addLayout(footer)
        return page

    def _build_qr_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._qr_tip_label = QLabel(self._t("sponsor_qr_tip"))
        self._qr_tip_label.setTextFormat(Qt.TextFormat.PlainText)
        self._qr_tip_label.setWordWrap(True)
        self._qr_tip_label.setObjectName("mutedLabel")
        layout.addWidget(self._qr_tip_label)

        self._qr_refresh_hint = QLabel(self._t("sponsor_refresh_hint"))
        self._qr_refresh_hint.setObjectName("hintLabel")
        self._qr_refresh_hint.setWordWrap(True)
        layout.addWidget(self._qr_refresh_hint)

        card = QFrame()
        card.setObjectName("qrCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._image_path and self._image_path.exists():
            pixmap = QPixmap(str(self._image_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(QR_MAX_EDGE, QR_MAX_EDGE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                image_label = QLabel()
                image_label.setPixmap(scaled)
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(image_label)
            else:
                card_layout.addWidget(self._no_image_label())
        else:
            card_layout.addWidget(self._no_image_label())
        layout.addWidget(card, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._back_btn = QPushButton(self._t("sponsor_back"))
        self._back_btn.clicked.connect(lambda: self._show_page("list"))
        footer.addWidget(self._back_btn)
        layout.addLayout(footer)
        return page

    def _no_image_label(self) -> QLabel:
        label = QLabel(self._t("sponsor_missing_qr"))
        self._no_image_text_label = label
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("mutedLabel")
        return label

    def _show_page(self, page: str) -> None:
        if page == "list":
            self._stack.setCurrentWidget(self._list_page)
            self._page_header.setText(self._t("sponsor_list_title"))
        elif page == "qr":
            self._stack.setCurrentWidget(self._qr_page)
            self._page_header.setText(self._t("sponsor_qr_title"))

    def _apply_data(self, data: dict) -> None:
        if self._destroying:
            return
        self._last_data = dict(data) if isinstance(data, dict) else {}
        self._data_loaded = True
        lang = self._ui_lang
        tip_map = data.get("tip") if isinstance(data, dict) else {}
        remote_tip = ""
        if isinstance(tip_map, dict):
            remote_tip = str(
                tip_map.get(lang)
                or tip_map.get(lang.split("-", 1)[0])
                or tip_map.get("en")
                or tip_map.get("zh")
                or ""
            ).strip()
        base_tip = self._t("sponsor_qr_tip")
        self._qr_tip_label.setText(f"{base_tip}\n{remote_tip}" if remote_tip else base_tip)
        sponsors = data.get("sponsors") if isinstance(data, dict) else []
        self._render_sponsor_list(sponsors if isinstance(sponsors, list) else [])

    def _render_sponsor_list(self, sponsors: list) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        names = [str(s.get("name") or "").strip() for s in sponsors if isinstance(s, dict) and s.get("name")]
        names = [name for name in names if name]
        if not names:
            label = QLabel(self._t("sponsor_empty"))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("mutedLabel")
            self._list_layout.insertWidget(0, label)
            self._total_label.setText("")
            return

        for index, name in enumerate(names):
            card = QFrame()
            card.setObjectName("sponsorCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            label = QLabel(name)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setObjectName("sponsorName")
            card_layout.addWidget(label)
            self._list_layout.insertWidget(index, card)
        self._total_label.setText(self._t("sponsor_total", count=len(names)))

    def _refresh(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        loading = QLabel(self._t("sponsor_loading"))
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setObjectName("mutedLabel")
        self._list_layout.insertWidget(0, loading)
        self._total_label.setText("")
        self._request_sponsors(force_refresh=True)

    def _request_sponsors(self, *, force_refresh: bool = False) -> None:
        self._request_generation += 1
        generation = self._request_generation
        window_ref = weakref.ref(self)

        def on_result(data: dict) -> None:
            window = window_ref()
            if (
                window is None
                or window._destroying
                or generation != window._request_generation
            ):
                return
            try:
                window._bridge.loaded.emit(data)
            except RuntimeError:
                # The remote fetch may finish after Qt has deleted the window.
                return

        get_sponsors(on_result, force_refresh=force_refresh)

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = normalize_ui_language(ui_language)
        self.setWindowTitle(self._t("sponsor_window_title"))
        current_page = "qr" if self._stack.currentWidget() is self._qr_page else "list"
        self._show_page(current_page)
        self._notice_label.setText(self._t("sponsor_list_notice"))
        self._qr_refresh_hint.setText(self._t("sponsor_refresh_hint"))
        self._refresh_btn.setText(self._t("sponsor_refresh"))
        self._donate_btn.setText(self._t("sponsor_donate_page"))
        self._back_btn.setText(self._t("sponsor_back"))
        if hasattr(self, "_no_image_text_label"):
            self._no_image_text_label.setText(self._t("sponsor_missing_qr"))
        if self._data_loaded:
            self._apply_data(self._last_data)
        else:
            self._qr_tip_label.setText(self._t("sponsor_qr_tip"))
            if getattr(self, "_loading_label", None) is not None:
                self._loading_label.setText(self._t("sponsor_loading"))

    def _center_on_parent(self, parent: QWidget | None) -> None:
        if parent is None:
            return
        parent_geo = parent.frameGeometry()
        geo = self.frameGeometry()
        geo.moveCenter(parent_geo.center())
        self.move(geo.topLeft())

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._destroying:
            self._destroying = True
            self._request_generation += 1
            if self._on_close is not None:
                try:
                    self._on_close()
                except Exception:
                    logger.debug("Failed to notify sponsor window close", exc_info=True)
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
        QDialog { background: #f5f5f7; }
        #pageHeader { color: #1d1d1f; font-size: 16px; font-weight: 700; }
        QScrollArea { background: transparent; border: none; }
        #noticeLabel {
            background: #fff7df;
            border: 1px solid #f1d27b;
            border-radius: 10px;
            color: #8a5a00;
            padding: 10px;
            font-weight: 600;
        }
        #mutedLabel { color: #6e6e73; }
        #hintLabel { color: #8e8e93; font-size: 12px; }
        #sponsorCard {
            background: #fffbf0;
            border: 1px solid #e4e7ed;
            border-radius: 10px;
        }
        #sponsorName { color: #d4a638; font-size: 20px; font-weight: 700; }
        #qrCard {
            background: #ffffff;
            border: 1px solid #e4e7ed;
            border-radius: 14px;
        }
        QPushButton {
            background: #eef1f5;
            border: 1px solid #e4e7ed;
            border-radius: 10px;
            color: #1d1d1f;
            padding: 7px 14px;
            font-weight: 600;
        }
        QPushButton:hover { background: #e0e4ea; }
        #primaryButton { background: #0071e3; color: #ffffff; border: 0; }
        #primaryButton:hover { background: #0059b8; }
        """)
