from __future__ import annotations

import logging

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from src.utils.i18n import tr

logger = logging.getLogger(__name__)


class WebSpeechBridgeWindow(QDialog):
    """Small app-owned WebSpeech bridge window.

    Keeping this inside the Qt app lets Mio close the page it opened and avoids
    leaving an external browser tab behind after shutdown.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        ui_lang = str(getattr(parent, "_ui_lang", "") or "").strip() or "en"
        self.setWindowTitle(tr(ui_lang, "webspeech_window_title"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:  # pragma: no cover - depends on optional Qt module
            raise RuntimeError("PySide6 QtWebEngine is not available") from exc

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

    def load_url(self, url: str) -> None:
        self._view.setUrl(QUrl(str(url or "")))
        if not self.isVisible():
            self.show()
        self.raise_()

    def close_bridge(self) -> None:
        try:
            self._view.setUrl(QUrl("about:blank"))
        except Exception:
            logger.debug("Failed to blank WebSpeech bridge page", exc_info=True)
        self.close()

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
