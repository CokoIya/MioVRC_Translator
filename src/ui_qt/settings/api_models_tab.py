# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""API & Models Settings Tab."""

from __future__ import annotations

import logging
import json
import threading
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QCheckBox,
    QGroupBox,
    QScrollArea,
    QMessageBox,
)

from src.translators.factory import test_translation_connection
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.secure_http import validate_api_base_url
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import (
    get_backend_model_options,
    normalize_backend,
    normalize_qwen_translation_region,
    qwen_translation_region_for_ui_language,
)
from src.ui_qt.credential_prompt import show_missing_credential_prompt
from src.utils.credential_validation import (
    MissingCredential,
    first_missing_required_credential,
)

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


class APIModelsTab(LocalizedSettingsTab):
    """API & Models configuration tab."""

    config_changed = Signal()
    connection_test_finished = Signal(str, bool, object)

    def __init__(
        self,
        config: dict,
        ui_language: str,
        parent: QWidget | None = None,
        *,
        on_open_api_settings: Callable[[MissingCredential], None] | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._ui_language = normalize_settings_language(ui_language)
        self._on_open_api_settings = on_open_api_settings
        self._loading_config = False
        self._suppress_credential_prompt = False

        self._init_ui()
        self.connection_test_finished.connect(self._finish_connection_test)

    def _init_ui(self) -> None:
        """Initialize the API & Models UI."""
        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel(self._t("api_models_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Translation Provider Section
        self._add_provider_section(layout)

        # OpenAI Section
        self._add_openai_section(layout)

        # Anthropic Section
        self._add_anthropic_section(layout)

        # DeepSeek Section
        self._add_deepseek_section(layout)

        # Gemini Section
        self._add_gemini_section(layout)

        # Qwen Section
        self._add_qwen_section(layout)

        # Grok-compatible relay section
        self._add_grok_section(layout)

        layout.addStretch()

        scroll.setWidget(container)

        main_layout = self._root_layout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _add_provider_section(self, layout: QVBoxLayout) -> None:
        """Add translation provider selection."""
        group = QGroupBox(self._t("translation_provider"))
        group_layout = QVBoxLayout(group)

        # Provider combo
        provider_layout = QHBoxLayout()
        provider_label = QLabel(self._t("select_provider"))
        provider_layout.addWidget(provider_label)

        self._provider_combo = QComboBox()
        for label_key, backend in (
            ("provider_openai", "openai"),
            ("provider_anthropic", "anthropic"),
            ("provider_deepseek", "deepseek"),
            ("provider_gemini", "gemini"),
            ("provider_qwen", "qianwen"),
            ("provider_grok", "grok_compatible"),
        ):
            self._provider_combo.addItem(self._t(label_key), backend)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addWidget(self._provider_combo, 1)

        group_layout.addLayout(provider_layout)

        # Model selection
        model_layout = QHBoxLayout()
        model_label = QLabel(self._t("model"))
        model_layout.addWidget(model_label)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.activated.connect(self._on_model_selected)
        model_layout.addWidget(self._model_combo, 1)

        group_layout.addLayout(model_layout)

        layout.addWidget(group)

    def _add_openai_section(self, layout: QVBoxLayout) -> None:
        """Add OpenAI configuration."""
        self._openai_group = QGroupBox(self._t("openai_config"))
        group_layout = QVBoxLayout(self._openai_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._openai_key_input = QLineEdit()
        self._openai_key_input.setPlaceholderText("sk-...")
        self._openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._openai_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._openai_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Base URL
        url_layout = QHBoxLayout()
        url_label = QLabel(self._t("base_url"))
        url_layout.addWidget(url_label)

        self._openai_url_input = QLineEdit()
        self._openai_url_input.setPlaceholderText("https://api.openai.com/v1")
        url_layout.addWidget(self._openai_url_input, 1)

        group_layout.addLayout(url_layout)

        # Test button
        test_btn = QPushButton("🧪 " + self._t("test_connection"))
        test_btn.clicked.connect(self._test_openai)
        group_layout.addWidget(test_btn)

        layout.addWidget(self._openai_group)

    def _add_anthropic_section(self, layout: QVBoxLayout) -> None:
        """Add Anthropic configuration."""
        self._anthropic_group = QGroupBox(self._t("anthropic_config"))
        group_layout = QVBoxLayout(self._anthropic_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._anthropic_key_input = QLineEdit()
        self._anthropic_key_input.setPlaceholderText("sk-ant-...")
        self._anthropic_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._anthropic_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._anthropic_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Test button
        test_btn = QPushButton("🧪 " + self._t("test_connection"))
        test_btn.clicked.connect(self._test_anthropic)
        group_layout.addWidget(test_btn)

        layout.addWidget(self._anthropic_group)

    def _add_deepseek_section(self, layout: QVBoxLayout) -> None:
        """Add DeepSeek configuration."""
        self._deepseek_group = QGroupBox(self._t("deepseek_config"))
        group_layout = QVBoxLayout(self._deepseek_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._deepseek_key_input = QLineEdit()
        self._deepseek_key_input.setPlaceholderText("sk-...")
        self._deepseek_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._deepseek_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._deepseek_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        layout.addWidget(self._deepseek_group)

    def _add_gemini_section(self, layout: QVBoxLayout) -> None:
        """Add Gemini configuration."""
        self._gemini_group = QGroupBox(self._t("gemini_config"))
        group_layout = QVBoxLayout(self._gemini_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._gemini_key_input = QLineEdit()
        self._gemini_key_input.setPlaceholderText("AI...")
        self._gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._gemini_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._gemini_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        layout.addWidget(self._gemini_group)

    def _add_qwen_section(self, layout: QVBoxLayout) -> None:
        """Add Qwen configuration."""
        self._qwen_group = QGroupBox(self._t("qwen_config"))
        group_layout = QVBoxLayout(self._qwen_group)

        # API Key
        key_layout = QHBoxLayout()
        key_label = QLabel(self._t("api_key"))
        key_layout.addWidget(key_label)

        self._qwen_key_input = QLineEdit()
        self._qwen_key_input.setPlaceholderText("sk-...")
        self._qwen_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._qwen_key_input, 1)

        show_btn = QPushButton("👁")
        show_btn.setFixedWidth(40)
        show_btn.clicked.connect(lambda: self._toggle_password(self._qwen_key_input))
        key_layout.addWidget(show_btn)

        group_layout.addLayout(key_layout)

        # Region
        region_layout = QHBoxLayout()
        region_label = QLabel(self._t("region"))
        region_layout.addWidget(region_label)

        self._qwen_region_combo = QComboBox()
        self._qwen_region_combo.addItem(self._t("region_china"), "china_mainland")
        self._qwen_region_combo.addItem(self._t("region_international"), "singapore")
        region_layout.addWidget(self._qwen_region_combo, 1)

        group_layout.addLayout(region_layout)

        layout.addWidget(self._qwen_group)

    def _add_grok_section(self, layout: QVBoxLayout) -> None:
        """Add Grok-compatible OpenAI relay configuration."""

        self._grok_group = QGroupBox(self._t("grok_config"))
        group_layout = QVBoxLayout(self._grok_group)

        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel(self._t("api_key")))
        self._grok_key_input = QLineEdit()
        self._grok_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._grok_key_input, 1)
        show_btn = QPushButton(self._t("show_api_key"))
        show_btn.setMinimumWidth(72)
        show_btn.clicked.connect(lambda: self._toggle_password(self._grok_key_input))
        key_layout.addWidget(show_btn)
        group_layout.addLayout(key_layout)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel(self._t("base_url")))
        self._grok_url_input = QLineEdit()
        self._grok_url_input.setPlaceholderText("https://api.x.ai/v1")
        url_layout.addWidget(self._grok_url_input, 1)
        group_layout.addLayout(url_layout)

        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel(self._t("request_timeout")))
        self._grok_timeout_input = QLineEdit()
        self._grok_timeout_input.setPlaceholderText("15")
        timeout_layout.addWidget(self._grok_timeout_input, 1)
        group_layout.addLayout(timeout_layout)

        headers_layout = QHBoxLayout()
        headers_layout.addWidget(QLabel(self._t("custom_request_headers")))
        self._grok_headers_input = QLineEdit()
        self._grok_headers_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._grok_headers_input.setPlaceholderText(
            self._t("custom_request_headers_example")
        )
        headers_layout.addWidget(self._grok_headers_input, 1)
        group_layout.addLayout(headers_layout)
        headers_hint = QLabel(self._t("custom_request_headers_hint"))
        headers_hint.setWordWrap(True)
        group_layout.addWidget(headers_hint)

        self._grok_streaming_check = QCheckBox(self._t("streaming_responses"))
        self._grok_streaming_check.setChecked(True)
        group_layout.addWidget(self._grok_streaming_check)
        streaming_hint = QLabel(self._t("streaming_responses_hint"))
        streaming_hint.setWordWrap(True)
        group_layout.addWidget(streaming_hint)

        self._grok_test_btn = QPushButton(self._t("test_connection"))
        self._grok_test_btn.clicked.connect(self._test_grok)
        group_layout.addWidget(self._grok_test_btn)
        layout.addWidget(self._grok_group)

    def _toggle_password(self, line_edit: QLineEdit) -> None:
        """Toggle password visibility."""
        if line_edit.echoMode() == QLineEdit.EchoMode.Password:
            line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_provider_changed(self, index: int) -> None:
        """Handle provider selection change."""
        # Show/hide relevant sections
        backend = str(self._provider_combo.itemData(index) or "")
        groups = {
            "openai": self._openai_group,
            "anthropic": self._anthropic_group,
            "deepseek": self._deepseek_group,
            "gemini": self._gemini_group,
            "qianwen": self._qwen_group,
            "grok_compatible": self._grok_group,
        }
        for provider_id, group in groups.items():
            group.setVisible(provider_id == backend)

        # Update model options
        models = list(get_backend_model_options(backend)) if backend else []

        self._model_combo.clear()
        self._model_combo.addItems(models)

        self.config_changed.emit()
        if not self._loading_config and not self._suppress_credential_prompt:
            self._prompt_for_missing_credential()

    def _on_model_selected(self, _index: int) -> None:
        """Validate credentials when the player selects a provider model."""
        if not self._loading_config and not self._suppress_credential_prompt:
            self._prompt_for_missing_credential()

    def _prompt_for_missing_credential(self, provider_id: str | None = None) -> bool:
        backend = normalize_backend(
            provider_id or self._provider_combo.currentData()
        )
        key_inputs = {
            "openai": self._openai_key_input,
            "anthropic": self._anthropic_key_input,
            "deepseek": self._deepseek_key_input,
            "gemini": self._gemini_key_input,
            "qianwen": self._qwen_key_input,
            "grok_compatible": self._grok_key_input,
        }
        key_input = key_inputs.get(backend)
        config = {
            "translation": {
                "backend": backend,
                backend: {
                    "api_key": key_input.text().strip() if key_input is not None else ""
                },
            }
        }
        missing = first_missing_required_credential(
            config,
            scopes=("translation",),
            ui_language=self._ui_language,
            active_only=False,
        )
        if missing is None:
            return False

        def open_api_settings() -> None:
            if callable(self._on_open_api_settings):
                self._on_open_api_settings(missing)
            else:
                self.focus_credential(missing)

        show_missing_credential_prompt(
            self,
            missing,
            ui_language=self._ui_language,
            open_settings=open_api_settings,
        )
        return True

    def focus_credential(self, missing: MissingCredential) -> None:
        """Show the provider section and focus its secret input."""
        backend = normalize_backend(missing.provider_id)
        index = self._provider_combo.findData(backend)
        if index >= 0 and index != self._provider_combo.currentIndex():
            self._suppress_credential_prompt = True
            try:
                self._provider_combo.setCurrentIndex(index)
            finally:
                self._suppress_credential_prompt = False
        key_inputs = {
            "openai": self._openai_key_input,
            "anthropic": self._anthropic_key_input,
            "deepseek": self._deepseek_key_input,
            "gemini": self._gemini_key_input,
            "qianwen": self._qwen_key_input,
            "grok_compatible": self._grok_key_input,
        }
        key_input = key_inputs.get(backend)
        if key_input is not None:
            key_input.setFocus()

    def _test_openai(self) -> None:
        """Test OpenAI connection."""
        if self._prompt_for_missing_credential("openai"):
            return
        logger.info("Testing OpenAI connection...")
        # Keep this button side-effect free until a real provider test exists.

    def _test_anthropic(self) -> None:
        """Test Anthropic connection."""
        if self._prompt_for_missing_credential("anthropic"):
            return
        logger.info("Testing Anthropic connection...")
        # Keep this button side-effect free until a real provider test exists.

    def _test_grok(self) -> None:
        """Test the configured Grok-compatible relay without exposing secrets."""

        if self._prompt_for_missing_credential("grok_compatible"):
            return
        try:
            timeout_s = float(self._grok_timeout_input.text().strip() or "15")
            if not 3.0 <= timeout_s <= 120.0:
                raise ValueError
        except (TypeError, ValueError):
            QMessageBox.warning(
                self,
                self._t("connection_test_failed"),
                self._t("request_timeout_invalid"),
            )
            return
        try:
            custom_headers = normalize_openai_custom_headers(
                self._grok_headers_input.text()
            )
        except ValueError:
            QMessageBox.warning(
                self,
                self._t("connection_test_failed"),
                self._t("invalid_custom_headers"),
            )
            return

        config = self.get_config()
        grok_cfg = config["translation"].setdefault("grok_compatible", {})
        grok_cfg["timeout_s"] = timeout_s
        grok_cfg["custom_headers"] = custom_headers
        grok_cfg["streaming"] = self._grok_streaming_check.isChecked()
        config["translation"]["backend"] = "grok_compatible"
        self._grok_test_btn.setEnabled(False)
        self._grok_test_btn.setText(self._t("connection_test_running"))

        def worker() -> None:
            try:
                result = test_translation_connection(config)
            except Exception as exc:
                self.connection_test_finished.emit("grok_compatible", False, exc)
            else:
                self.connection_test_finished.emit(
                    "grok_compatible",
                    True,
                    result,
                )

        threading.Thread(
            target=worker,
            name="grok-connection-test",
            daemon=True,
        ).start()

    def _finish_connection_test(
        self,
        backend: str,
        success: bool,
        result: object,
    ) -> None:
        button = getattr(self, "_grok_test_btn", None)
        if button is not None:
            button.setEnabled(True)
            button.setText(self._t("test_connection"))
        if success:
            QMessageBox.information(
                self,
                self._t("test_connection"),
                self._t("connection_test_success"),
            )
            return
        friendly = format_translation_error(
            result,
            backend=backend,
            ui_language=self._ui_language,
        )
        QMessageBox.warning(
            self,
            self._t("connection_test_failed"),
            friendly.detailed_message,
        )

    def get_config(self) -> dict:
        """Get current configuration from UI."""
        backend = normalize_backend(self._provider_combo.currentData())
        selected_model = self._model_combo.currentText().strip()
        selected_region = normalize_qwen_translation_region(
            self._qwen_region_combo.currentData()
        )
        try:
            grok_timeout = float(self._grok_timeout_input.text().strip() or "15")
            if not 3.0 <= grok_timeout <= 120.0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(self._t("request_timeout_invalid")) from exc
        try:
            grok_headers = normalize_openai_custom_headers(
                self._grok_headers_input.text()
            )
        except ValueError as exc:
            raise ValueError(self._t("invalid_custom_headers")) from exc
        try:
            grok_base_url = validate_api_base_url(
                self._grok_url_input.text().strip() or "https://api.x.ai/v1",
                label="Grok-compatible API",
            )
        except ValueError as exc:
            raise ValueError(
                format_translation_error(
                    exc,
                    backend="grok_compatible",
                    ui_language=self._ui_language,
                ).detailed_message
            ) from exc

        config = {
            "translation": {
                "backend": backend or "openai",
                "openai": {
                    "api_key": self._openai_key_input.text().strip(),
                    "base_url": self._openai_url_input.text().strip(),
                },
                "anthropic": {
                    "api_key": self._anthropic_key_input.text().strip(),
                },
                "deepseek": {
                    "api_key": self._deepseek_key_input.text().strip(),
                },
                "gemini": {
                    "api_key": self._gemini_key_input.text().strip(),
                },
                "qianwen": {
                    "api_key": self._qwen_key_input.text().strip(),
                    "region": selected_region,
                },
                "grok_compatible": {
                    "api_key": self._grok_key_input.text().strip(),
                    "base_url": grok_base_url,
                    "model": (
                        selected_model
                        if backend == "grok_compatible" and selected_model
                        else "grok-4.5"
                    ),
                    "timeout_s": grok_timeout,
                    "max_retries": 0,
                    "custom_headers": grok_headers,
                    "streaming": self._grok_streaming_check.isChecked(),
                },
            }
        }
        if selected_model:
            config["translation"].setdefault(backend, {})["model"] = selected_model

        return config

    def load_config(self, config: dict) -> None:
        """Load configuration into UI."""
        self._loading_config = True
        try:
            self._load_config_values(config)
        finally:
            self._loading_config = False

    def _load_config_values(self, config: dict) -> None:
        trans_cfg = config.get("translation", {})

        # Load API keys
        openai_cfg = trans_cfg.get("openai", {})
        self._openai_key_input.setText(openai_cfg.get("api_key", ""))
        self._openai_url_input.setText(openai_cfg.get("base_url", ""))

        anthropic_cfg = trans_cfg.get("anthropic", {})
        self._anthropic_key_input.setText(anthropic_cfg.get("api_key", ""))

        deepseek_cfg = trans_cfg.get("deepseek", {})
        self._deepseek_key_input.setText(deepseek_cfg.get("api_key", ""))

        gemini_cfg = trans_cfg.get("gemini", {})
        self._gemini_key_input.setText(gemini_cfg.get("api_key", ""))

        qwen_cfg = trans_cfg.get("qianwen", trans_cfg.get("qwen", {}))
        self._qwen_key_input.setText(qwen_cfg.get("api_key", ""))
        region = normalize_qwen_translation_region(
            qwen_cfg.get("region")
            or qwen_translation_region_for_ui_language(self._ui_language)
        )
        region_index = self._qwen_region_combo.findData(region)
        self._qwen_region_combo.setCurrentIndex(max(0, region_index))

        grok_cfg = trans_cfg.get("grok_compatible", {})
        if not isinstance(grok_cfg, dict):
            grok_cfg = {}
        self._grok_key_input.setText(str(grok_cfg.get("api_key", "") or ""))
        self._grok_url_input.setText(
            str(grok_cfg.get("base_url", "https://api.x.ai/v1") or "")
        )
        self._grok_timeout_input.setText(
            str(grok_cfg.get("timeout_s", 15.0) or 15.0)
        )
        grok_headers = grok_cfg.get("custom_headers", {})
        self._grok_headers_input.setText(
            json.dumps(grok_headers, ensure_ascii=False, separators=(",", ":"))
            if isinstance(grok_headers, dict) and grok_headers
            else ""
        )
        self._grok_streaming_check.setChecked(bool(grok_cfg.get("streaming", True)))

        # Set provider
        backend = trans_cfg.get("backend", "openai")
        provider_map_reverse = {
            "openai": 0,
            "anthropic": 1,
            "deepseek": 2,
            "gemini": 3,
            "qianwen": 4,
            "qwen": 4,
            "grok_compatible": 5,
        }
        self._provider_combo.setCurrentIndex(provider_map_reverse.get(normalize_backend(backend), 0))
        self._on_provider_changed(self._provider_combo.currentIndex())
        selected_backend = normalize_backend(backend)
        backend_cfg = trans_cfg.get(selected_backend, {})
        selected_model = str(backend_cfg.get("model", "") or "").strip()
        if selected_model:
            model_index = self._model_combo.findText(selected_model)
            if model_index < 0:
                self._model_combo.insertItem(0, selected_model)
                model_index = 0
            self._model_combo.setCurrentIndex(model_index)
