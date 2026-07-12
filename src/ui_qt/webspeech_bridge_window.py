from __future__ import annotations

import logging

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from src.utils.i18n import tr
from src.utils.localization import normalize_ui_language

logger = logging.getLogger(__name__)


class WebSpeechBridgeWindow(QDialog):
    """Small app-owned WebSpeech bridge window.

    Keeping this inside the Qt app lets Mio close the page it opened and avoids
    leaving an external browser tab behind after shutdown.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ui_lang = normalize_ui_language(
            str(getattr(parent, "_ui_lang", "") or "").strip() or "en",
            default="en",
        )
        self.setWindowTitle(tr(self._ui_lang, "webspeech_window_title"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:  # pragma: no cover - depends on optional Qt module
            raise RuntimeError(tr(self._ui_lang, "webspeech_qt_unavailable")) from exc

        self._page_type = QWebEnginePage
        self._view = QWebEngineView(self)
        settings = self._view.settings()
        for attr_name in ("PlaybackRequiresUserGesture", "JavascriptCanAccessClipboard"):
            attr = getattr(QWebEngineSettings.WebAttribute, attr_name, None)
            if attr is not None:
                settings.setAttribute(attr, True)
        self._view.page().featurePermissionRequested.connect(
            self._on_feature_permission_requested
        )
        layout.addWidget(self._view)

    def update_language(self, ui_language: str) -> None:
        self._ui_lang = normalize_ui_language(ui_language, default="en")
        self.setWindowTitle(tr(self._ui_lang, "webspeech_window_title"))
        current_url = self._view.url()
        if current_url.isValid() and current_url.scheme() in {"http", "https"}:
            self._view.reload()

    def load_url(self, url: str) -> None:
        self._view.setUrl(QUrl(str(url or "")))
        if not self.isVisible():
            self.show()
        self.raise_()

    def close_bridge(self) -> None:
        self._release_page()
        self.close()

    def _release_page(self) -> None:
        try:
            self._view.stop()
            self._view.setUrl(QUrl("about:blank"))
        except Exception:
            logger.debug("Failed to blank WebSpeech bridge page", exc_info=True)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Closing through the title-bar must release WebEngine microphone and
        # page resources just like the explicit application shutdown path.
        self._release_page()
        super().closeEvent(event)

    def _on_feature_permission_requested(self, security_origin, feature) -> None:
        page = self._view.page()
        granted = self._page_type.PermissionPolicy.PermissionGrantedByUser
        denied = self._page_type.PermissionPolicy.PermissionDeniedByUser
        audio_features = {
            self._page_type.Feature.MediaAudioCapture,
            self._page_type.Feature.MediaAudioVideoCapture,
        }
        try:
            policy = granted if feature in audio_features else denied
            page.setFeaturePermission(security_origin, feature, policy)
        except Exception:
            logger.debug("Failed to set WebSpeech bridge permission", exc_info=True)
