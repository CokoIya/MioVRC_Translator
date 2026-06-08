from __future__ import annotations

from collections.abc import Callable
import logging
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

from src.utils.locale_detect import get_system_language
from src.utils.sponsor_fetcher import get_sponsors

logger = logging.getLogger(__name__)

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 520
QR_MAX_EDGE = 240

_WINDOW_TITLES = {
    "zh": "感谢赞助 · 赞助入口",
    "ja": "感謝リスト · 支援",
    "en": "Sponsors · Support",
    "ko": "감사 명단 · 후원",
    "ru": "Список благодарностей · Поддержка",
}
_LIST_PAGE_TITLE = {
    "zh": "感谢赞助者",
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
_REFRESH_BTN = {"zh": "刷新", "ja": "更新", "en": "Refresh", "ko": "새로고침", "ru": "Обновить"}
_DONATE_PAGE_BTN = {"zh": "赞助页", "ja": "支援ページ", "en": "Donate Page", "ko": "후원 페이지", "ru": "Страница поддержки"}
_BACK_BTN = {"zh": "返回名单", "ja": "名簿に戻る", "en": "Back to List", "ko": "명단으로 돌아가기", "ru": "Назад к списку"}
_QR_REFRESH_HINT = {
    "zh": "名单更新请返回上一页点击刷新。",
    "ja": "リストを更新するには前のページに戻って更新してください。",
    "en": "Go back to the list page to refresh the sponsors.",
    "ko": "명단을 새로고침하려면 목록 페이지로 돌아가세요.",
    "ru": "Чтобы обновить список, вернитесь на страницу списка.",
}
_NO_IMAGE_TEXT = {
    "zh": "请将赞助二维码图片放到 assets/sponsor_qr.png",
    "ja": "支援用 QR 画像を assets/sponsor_qr.png に配置してください",
    "en": "Put the sponsor QR image at assets/sponsor_qr.png",
    "ko": "후원 QR 이미지를 assets/sponsor_qr.png 에 넣어 주세요",
    "ru": "Поместите QR-код спонсора в assets/sponsor_qr.png",
}

# Keep the visible Qt sponsor window text clean even when older migrated tables
# were decoded through the wrong code page in previous iterations.
_WINDOW_TITLES.update({
    "zh": "感谢赞助 · 赞助入口",
    "ja": "感謝リスト · 支援",
    "en": "Sponsors · Support",
    "ko": "감사 명단 · 후원",
    "ru": "Sponsors · Support",
})
_LIST_PAGE_TITLE.update({
    "zh": "感谢赞助者",
    "ja": "ご支援ありがとうございます",
    "en": "Thanks to these supporters",
    "ko": "후원해 주신 분들께 감사드립니다",
    "ru": "Thanks to these supporters",
})
_QR_PAGE_TITLE.update({
    "zh": "赞助页",
    "ja": "支援ページ",
    "en": "Donate Page",
    "ko": "후원 페이지",
    "ru": "Donate Page",
})
_LIST_NOTICE.update({
    "zh": "赞助打赏请点击赞助页，点击后会自动翻页到二维码页。",
    "ja": "ご支援は「支援ページ」をクリックしてください。自動で QR 画面に切り替わります。",
    "en": "Please click the Donate Page button. It will flip to the QR page automatically.",
    "ko": "후원은 후원 페이지를 눌러 주세요. 자동으로 QR 페이지로 전환됩니다.",
    "ru": "Please click the Donate Page button. It will flip to the QR page automatically.",
})
_QR_TIP.update({
    "zh": "扫描二维码即可赞助打赏，助力作者创作下一个插件和社区。",
    "ja": "QR を読み取ってご支援いただけると、次のプラグインとコミュニティ制作の力になります。",
    "en": "Scan the QR code to support the project and help build the next plugin and community.",
    "ko": "QR 코드를 스캔해 후원하면 다음 플러그인과 커뮤니티 제작에 도움이 됩니다.",
    "ru": "Scan the QR code to support the project and help build the next plugin and community.",
})
_LOADING_TEXT.update({
    "zh": "名单加载中...",
    "ja": "名簿を読み込み中...",
    "en": "Loading sponsors...",
    "ko": "후원자 명단을 불러오는 중...",
    "ru": "Loading sponsors...",
})
_NO_SPONSORS.update({
    "zh": "（暂无赞助者记录）",
    "ja": "（まだ記録がありません）",
    "en": "(No records yet)",
    "ko": "(아직 기록이 없습니다)",
    "ru": "(No records yet)",
})
_TOTAL_FMT.update({
    "zh": "共 {n} 位，感谢每一位的支持",
    "ja": "合計 {n} 名、ありがとうございます",
    "en": "{n} supporters - thank you all",
    "ko": "총 {n}명 - 모두 감사합니다",
    "ru": "{n} supporters - thank you all",
})
_REFRESH_BTN.update({"zh": "刷新", "ja": "更新", "en": "Refresh", "ko": "새로고침", "ru": "Refresh"})
_DONATE_PAGE_BTN.update({"zh": "赞助页", "ja": "支援ページ", "en": "Donate Page", "ko": "후원 페이지", "ru": "Donate Page"})
_BACK_BTN.update({"zh": "返回名单", "ja": "名簿に戻る", "en": "Back to List", "ko": "명단으로 돌아가기", "ru": "Back to List"})
_QR_REFRESH_HINT.update({
    "zh": "名单更新请返回上一页点击刷新。",
    "ja": "リストを更新するには前のページに戻って更新してください。",
    "en": "Go back to the list page to refresh the sponsors.",
    "ko": "명단을 새로고침하려면 목록 페이지로 돌아가 주세요.",
    "ru": "Go back to the list page to refresh the sponsors.",
})
_NO_IMAGE_TEXT.update({
    "zh": "请将赞助二维码图片放到 assets/sponsor_qr.png",
    "ja": "支援用 QR 画像を assets/sponsor_qr.png に配置してください",
    "en": "Put the sponsor QR image at assets/sponsor_qr.png",
    "ko": "후원 QR 이미지를 assets/sponsor_qr.png 에 넣어 주세요",
    "ru": "Put the sponsor QR image at assets/sponsor_qr.png",
})


def _t(table: dict[str, str], lang: str | None = None) -> str:
    code = (lang or get_system_language()).lower().split("-")[0]
    return table.get(code) or table.get("en") or next(iter(table.values()), "")


class _SponsorBridge(QObject):
    loaded = Signal(dict)


class SponsorWindow(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        sponsor_image_path: Path | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._image_path = sponsor_image_path
        self._on_close = on_close
        self._destroying = False
        self._lang = get_system_language().lower().split("-")[0]
        self._bridge = _SponsorBridge(self)
        self._bridge.loaded.connect(self._apply_data)

        self.setWindowTitle(_t(_WINDOW_TITLES, self._lang))
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._build()
        self._center_on_parent(parent)
        get_sponsors(lambda data: self._bridge.loaded.emit(data))

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._page_header = QLabel(_t(_LIST_PAGE_TITLE, self._lang))
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
        self._loading_label = QLabel(_t(_LOADING_TEXT, self._lang))
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._list_layout.addWidget(self._loading_label)
        self._list_layout.addStretch(1)
        self._list_area.setWidget(self._list_content)
        layout.addWidget(self._list_area, 1)

        notice = QLabel(_t(_LIST_NOTICE, self._lang))
        notice.setObjectName("noticeLabel")
        notice.setWordWrap(True)
        layout.addWidget(notice)

        footer = QHBoxLayout()
        self._total_label = QLabel("")
        self._total_label.setObjectName("mutedLabel")
        footer.addWidget(self._total_label, 1)
        refresh_btn = QPushButton(_t(_REFRESH_BTN, self._lang))
        refresh_btn.clicked.connect(self._refresh)
        footer.addWidget(refresh_btn)
        donate_btn = QPushButton(_t(_DONATE_PAGE_BTN, self._lang))
        donate_btn.setObjectName("primaryButton")
        donate_btn.clicked.connect(lambda: self._show_page("qr"))
        footer.addWidget(donate_btn)
        layout.addLayout(footer)
        return page

    def _build_qr_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._qr_tip_label = QLabel(_t(_QR_TIP, self._lang))
        self._qr_tip_label.setWordWrap(True)
        self._qr_tip_label.setObjectName("mutedLabel")
        layout.addWidget(self._qr_tip_label)

        hint = QLabel(_t(_QR_REFRESH_HINT, self._lang))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

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
        back_btn = QPushButton(_t(_BACK_BTN, self._lang))
        back_btn.clicked.connect(lambda: self._show_page("list"))
        footer.addWidget(back_btn)
        layout.addLayout(footer)
        return page

    def _no_image_label(self) -> QLabel:
        label = QLabel(_t(_NO_IMAGE_TEXT, self._lang))
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("mutedLabel")
        return label

    def _show_page(self, page: str) -> None:
        if page == "list":
            self._stack.setCurrentWidget(self._list_page)
            self._page_header.setText(_t(_LIST_PAGE_TITLE, self._lang))
        elif page == "qr":
            self._stack.setCurrentWidget(self._qr_page)
            self._page_header.setText(_t(_QR_PAGE_TITLE, self._lang))

    def _apply_data(self, data: dict) -> None:
        lang = self._lang
        tip_map = data.get("tip") if isinstance(data, dict) else {}
        remote_tip = ""
        if isinstance(tip_map, dict):
            remote_tip = str(tip_map.get(lang) or tip_map.get("en") or tip_map.get("zh") or "").strip()
        self._qr_tip_label.setText(f"{_t(_QR_TIP, lang)}\n{remote_tip}" if remote_tip else _t(_QR_TIP, lang))
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
            label = QLabel(_t(_NO_SPONSORS, self._lang))
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
            label.setWordWrap(True)
            label.setObjectName("sponsorName")
            card_layout.addWidget(label)
            self._list_layout.insertWidget(index, card)
        self._total_label.setText(_t(_TOTAL_FMT, self._lang).format(n=len(names)))

    def _refresh(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        loading = QLabel(_t(_LOADING_TEXT, self._lang))
        loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading.setObjectName("mutedLabel")
        self._list_layout.insertWidget(0, loading)
        self._total_label.setText("")
        get_sponsors(lambda data: self._bridge.loaded.emit(data), force_refresh=True)

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
