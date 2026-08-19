# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 ここ_Mio and Mio RealTime Translator contributors
#
# This file is part of Mio RealTime Translator.

"""API and model settings for translation providers."""

from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Callable, Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.translators.factory import test_translation_connection
from src.ui_qt.credential_prompt import show_missing_credential_prompt
from src.utils.credential_validation import (
    MissingCredential,
    first_missing_required_credential,
)
from src.utils.openai_compat import normalize_openai_custom_headers
from src.utils.provider_settings import (
    AI_PROVIDER_BACKENDS,
    PROVIDER_CHOICES,
    parse_optional_timeout,
    preserve_provider_id,
)
from src.utils.qwen_endpoints import (
    QWEN_TOKYO_COMPATIBLE_MODE_PATH,
    require_qwen_tokyo_workspace_base_url,
)
from src.utils.secure_http import validate_api_base_url
from src.utils.translation_error_formatter import format_translation_error
from src.utils.ui_config import (
    get_backend_known_base_urls,
    get_backend_model_options,
    get_backend_value,
    get_qwen_translation_base_url,
    normalize_qwen_translation_region,
    qwen_translation_region_for_ui_language,
)

from .localized_tab import LocalizedSettingsTab, normalize_settings_language

logger = logging.getLogger(__name__)


# Stable catalog keys retained for older tabbed-settings plugins.  The current
# UI uses explicit official/compatible names, but removing these literals would
# make the public localization surface appear undocumented.
_LEGACY_PROVIDER_I18N_KEYS = frozenset(
    {
        "anthropic_config",
        "grok_config",
        "openai_config",
        "provider_anthropic",
        "provider_grok",
        "provider_openai",
        "provider_openai_recommended",
        "provider_qwen",
        "request_timeout_invalid",
    }
)


# Keep the official providers distinct from compatible relays.  In particular,
# never infer a relay from a custom model name or silently rewrite it to an
# official backend.
_OFFICIAL_PROVIDER_BACKENDS = frozenset({"openai", "anthropic", "xai"})
_PROMPTABLE_PROVIDER_BACKENDS = frozenset(backend for _key, backend in PROVIDER_CHOICES)
_PROVIDER_GROUP_KEYS = {
    "openai": "openai_official_config",
    "openai_compatible": "openai_compatible_config",
    "anthropic": "anthropic_official_config",
    "anthropic_compatible": "anthropic_compatible_config",
    "xai": "xai_official_config",
    "grok_compatible": "grok_compatible_config",
}
_PROVIDER_HINT_KEYS = {
    "openai": "provider_hint_openai_official",
    "openai_compatible": "provider_hint_openai_compatible",
    "anthropic": "provider_hint_anthropic_official",
    "anthropic_compatible": "provider_hint_anthropic_compatible",
    "xai": "provider_hint_xai_official",
    "grok_compatible": "provider_hint_grok_compatible",
}
_TIMEOUT_FIELDS: tuple[tuple[str, str, float, float, bool], ...] = (
    ("timeout_s", "request_timeout", 3.0, 120.0, False),
    ("connect_timeout_s", "connect_timeout", 0.1, 120.0, True),
    ("pool_timeout_s", "pool_timeout", 0.1, 120.0, True),
    ("read_timeout_s", "read_timeout", 0.1, 300.0, True),
    ("write_timeout_s", "write_timeout", 0.1, 300.0, True),
    ("wall_timeout_s", "wall_timeout", 0.1, 300.0, True),
)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


class APIModelsTab(LocalizedSettingsTab):
    """Configure official and compatible translation API providers."""

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
        self._translation_snapshot: dict[str, object] = {}
        self._ui_language = normalize_settings_language(ui_language)
        self._on_open_api_settings = on_open_api_settings
        self._loading_config = False
        self._suppress_credential_prompt = False
        self._provider_groups: dict[str, QGroupBox] = {}
        self._provider_fields: dict[str, dict[str, object]] = {}
        self._provider_models: dict[str, str] = {}
        self._current_backend = "openai"

        self._init_ui()
        self.connection_test_finished.connect(self._finish_connection_test)
        self.load_config(config)

    def _init_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(24)

        title = QLabel(self._t("api_models_title"))
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        self._provider_groups = {}
        self._provider_fields = {}
        self._add_provider_section(layout)
        for backend in AI_PROVIDER_BACKENDS:
            self._add_ai_provider_section(layout, backend)
        self._add_deepseek_section(layout)
        self._add_gemini_section(layout)
        self._add_qwen_section(layout)
        self._install_legacy_widget_aliases()

        layout.addStretch()
        scroll.setWidget(container)

        root = self._root_layout()
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

    def _add_provider_section(self, layout: QVBoxLayout) -> None:
        group = QGroupBox(self._t("translation_provider"))
        group_layout = QVBoxLayout(group)

        provider_layout = QHBoxLayout()
        provider_layout.addWidget(QLabel(self._t("select_provider")))
        self._provider_combo = QComboBox()
        for label_key, backend in PROVIDER_CHOICES:
            self._provider_combo.addItem(self._t(label_key), backend)
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addWidget(self._provider_combo, 1)
        group_layout.addLayout(provider_layout)

        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel(self._t("model")))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.currentTextChanged.connect(self._on_model_text_changed)
        self._model_combo.activated.connect(self._on_model_selected)
        model_layout.addWidget(self._model_combo, 1)
        group_layout.addLayout(model_layout)

        model_hint = QLabel(self._t("model_id_preserved_hint"))
        model_hint.setWordWrap(True)
        group_layout.addWidget(model_hint)
        layout.addWidget(group)

    def _add_ai_provider_section(self, layout: QVBoxLayout, backend: str) -> None:
        group = QGroupBox(self._t(_PROVIDER_GROUP_KEYS[backend]))
        group_layout = QVBoxLayout(group)

        provider_hint = QLabel(self._t(_PROVIDER_HINT_KEYS[backend]))
        provider_hint.setWordWrap(True)
        group_layout.addWidget(provider_hint)

        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel(self._t("api_key")))
        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.setPlaceholderText(
            "sk-ant-..."
            if backend.startswith("anthropic")
            else "xai-..."
            if backend in {"xai", "grok_compatible"}
            else "sk-..."
        )
        key_input.textChanged.connect(self._on_field_changed)
        key_layout.addWidget(key_input, 1)
        show_button = QPushButton(self._t("show_api_key"))
        show_button.setMinimumWidth(72)
        show_button.clicked.connect(lambda: self._toggle_password(key_input))
        key_layout.addWidget(show_button)
        group_layout.addLayout(key_layout)

        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel(self._t("base_url")))
        url_input = QLineEdit()
        url_input.setPlaceholderText(get_backend_value(backend, "base_url"))
        url_input.setReadOnly(backend in _OFFICIAL_PROVIDER_BACKENDS)
        url_input.textChanged.connect(self._on_field_changed)
        url_layout.addWidget(url_input, 1)
        group_layout.addLayout(url_layout)
        url_hint = QLabel(
            self._t(
                "official_base_url_read_only_hint"
                if backend in _OFFICIAL_PROVIDER_BACKENDS
                else "relay_base_url_hint"
            )
        )
        url_hint.setWordWrap(True)
        group_layout.addWidget(url_hint)

        timeout_group = QGroupBox(self._t("timeout_settings"))
        timeout_layout = QGridLayout(timeout_group)
        timeout_inputs: dict[str, QLineEdit] = {}
        for row, (config_key, label_key, _minimum, _maximum, optional) in enumerate(
            _TIMEOUT_FIELDS
        ):
            timeout_layout.addWidget(QLabel(self._t(label_key)), row, 0)
            entry = QLineEdit()
            entry.setPlaceholderText(
                self._t("optional_timeout_placeholder")
                if optional
                else get_backend_value(backend, "timeout_s") or "15"
            )
            entry.textChanged.connect(self._on_field_changed)
            timeout_layout.addWidget(entry, row, 1)
            timeout_inputs[config_key] = entry
        timeout_hint = QLabel(self._t("timeout_settings_hint"))
        timeout_hint.setWordWrap(True)
        timeout_layout.addWidget(timeout_hint, len(_TIMEOUT_FIELDS), 0, 1, 2)
        group_layout.addWidget(timeout_group)

        headers_layout = QHBoxLayout()
        headers_layout.addWidget(QLabel(self._t("custom_request_headers")))
        headers_input = QLineEdit()
        headers_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        headers_input.setPlaceholderText(self._t("custom_request_headers_example"))
        headers_input.textChanged.connect(self._on_field_changed)
        headers_layout.addWidget(headers_input, 1)
        group_layout.addLayout(headers_layout)
        headers_hint = QLabel(self._t("custom_request_headers_hint"))
        headers_hint.setWordWrap(True)
        group_layout.addWidget(headers_hint)

        streaming_check = QCheckBox(self._t("streaming_responses"))
        streaming_check.toggled.connect(self._on_field_changed)
        group_layout.addWidget(streaming_check)
        streaming_hint = QLabel(self._t("streaming_responses_hint"))
        streaming_hint.setWordWrap(True)
        group_layout.addWidget(streaming_hint)

        test_button = QPushButton(self._t("test_connection"))
        test_button.clicked.connect(
            lambda _checked=False, provider=backend: self._test_provider(provider)
        )
        group_layout.addWidget(test_button)

        fields: dict[str, object] = {
            "api_key": key_input,
            "base_url": url_input,
            "custom_headers": headers_input,
            "streaming": streaming_check,
            "test_button": test_button,
        }
        fields.update(timeout_inputs)
        self._provider_groups[backend] = group
        self._provider_fields[backend] = fields
        layout.addWidget(group)

    def _add_deepseek_section(self, layout: QVBoxLayout) -> None:
        self._deepseek_group = QGroupBox(self._t("deepseek_config"))
        group_layout = QHBoxLayout(self._deepseek_group)
        group_layout.addWidget(QLabel(self._t("api_key")))
        self._deepseek_key_input = QLineEdit()
        self._deepseek_key_input.setPlaceholderText("sk-...")
        self._deepseek_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._deepseek_key_input.textChanged.connect(self._on_field_changed)
        group_layout.addWidget(self._deepseek_key_input, 1)
        layout.addWidget(self._deepseek_group)

    def _add_gemini_section(self, layout: QVBoxLayout) -> None:
        self._gemini_group = QGroupBox(self._t("gemini_config"))
        group_layout = QHBoxLayout(self._gemini_group)
        group_layout.addWidget(QLabel(self._t("api_key")))
        self._gemini_key_input = QLineEdit()
        self._gemini_key_input.setPlaceholderText("AI...")
        self._gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._gemini_key_input.textChanged.connect(self._on_field_changed)
        group_layout.addWidget(self._gemini_key_input, 1)
        layout.addWidget(self._gemini_group)

    def _add_qwen_section(self, layout: QVBoxLayout) -> None:
        self._qwen_group = QGroupBox(self._t("qwen_config"))
        group_layout = QVBoxLayout(self._qwen_group)

        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel(self._t("api_key")))
        self._qwen_key_input = QLineEdit()
        self._qwen_key_input.setPlaceholderText("sk-...")
        self._qwen_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._qwen_key_input.textChanged.connect(self._on_field_changed)
        key_layout.addWidget(self._qwen_key_input, 1)
        group_layout.addLayout(key_layout)

        region_layout = QHBoxLayout()
        region_layout.addWidget(QLabel(self._t("region")))
        self._qwen_region_combo = QComboBox()
        self._qwen_region_combo.addItem(self._t("region_china"), "china_mainland")
        self._qwen_region_combo.addItem(
            self._t("region_international"), "singapore"
        )
        self._qwen_region_combo.addItem(self._t("region_japan"), "japan")
        self._qwen_region_combo.currentIndexChanged.connect(
            self._on_qwen_region_changed
        )
        region_layout.addWidget(self._qwen_region_combo, 1)
        group_layout.addLayout(region_layout)

        base_url_layout = QHBoxLayout()
        base_url_layout.addWidget(QLabel(self._t("base_url")))
        self._qwen_base_url_input = QLineEdit()
        self._qwen_base_url_input.setPlaceholderText(
            "https://{WorkspaceId}.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1"
        )
        self._qwen_base_url_input.textChanged.connect(self._on_field_changed)
        base_url_layout.addWidget(self._qwen_base_url_input, 1)
        group_layout.addLayout(base_url_layout)
        layout.addWidget(self._qwen_group)

    def _install_legacy_widget_aliases(self) -> None:
        aliases = {
            "openai": "openai",
            "openai_compatible": "openai_compatible",
            "anthropic": "anthropic",
            "anthropic_compatible": "anthropic_compatible",
            "xai": "xai",
            "grok_compatible": "grok",
        }
        for backend, alias in aliases.items():
            fields = self._provider_fields[backend]
            setattr(self, f"_{alias}_group", self._provider_groups[backend])
            setattr(self, f"_{alias}_key_input", fields["api_key"])
            setattr(self, f"_{alias}_url_input", fields["base_url"])
            setattr(self, f"_{alias}_timeout_input", fields["timeout_s"])
            setattr(self, f"_{alias}_headers_input", fields["custom_headers"])
            setattr(self, f"_{alias}_streaming_check", fields["streaming"])
            setattr(self, f"_{alias}_test_btn", fields["test_button"])

    def _toggle_password(self, line_edit: QLineEdit) -> None:
        if line_edit.echoMode() == QLineEdit.EchoMode.Password:
            line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)

    def _on_field_changed(self, *_args: object) -> None:
        if not self._loading_config:
            self.config_changed.emit()

    def _on_qwen_region_changed(self, *_args: object) -> None:
        region = normalize_qwen_translation_region(
            self._qwen_region_combo.currentData()
        )
        regional_base_url = get_qwen_translation_base_url(region)
        if regional_base_url:
            self._qwen_base_url_input.setText(regional_base_url)
        elif (
            region == "japan"
            and self._qwen_base_url_input.text().strip()
            in get_backend_known_base_urls("qianwen")
        ):
            self._qwen_base_url_input.clear()
        self._qwen_base_url_input.setReadOnly(bool(regional_base_url))
        self._on_field_changed()

    def _on_model_text_changed(self, text: str) -> None:
        backend = preserve_provider_id(self._provider_combo.currentData())
        self._provider_models[backend] = str(text)
        self._on_field_changed()

    def _on_provider_changed(self, index: int) -> None:
        previous = self._current_backend
        if previous and not self._loading_config:
            self._provider_models[previous] = self._model_combo.currentText()

        backend = preserve_provider_id(self._provider_combo.itemData(index))
        self._current_backend = backend
        groups = {
            **self._provider_groups,
            "deepseek": self._deepseek_group,
            "gemini": self._gemini_group,
            "qianwen": self._qwen_group,
        }
        for provider_id, group in groups.items():
            group.setVisible(provider_id == backend)

        current_model = self._provider_models.get(backend, "")
        models = (
            list(get_backend_model_options(backend, current_model))
            if backend in _PROMPTABLE_PROVIDER_BACKENDS
            else ([current_model] if current_model else [])
        )
        self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            self._model_combo.addItems(models)
            if current_model:
                model_index = self._model_combo.findText(current_model)
                if model_index < 0:
                    self._model_combo.insertItem(0, current_model)
                    model_index = 0
                self._model_combo.setCurrentIndex(model_index)
        finally:
            self._model_combo.blockSignals(False)

        if not self._loading_config:
            self.config_changed.emit()
            if not self._suppress_credential_prompt:
                self._prompt_for_missing_credential()

    def _on_model_selected(self, _index: int) -> None:
        if not self._loading_config and not self._suppress_credential_prompt:
            self._prompt_for_missing_credential()

    def _ensure_provider_choice(self, provider: str) -> int:
        index = self._provider_combo.findData(provider)
        if index >= 0:
            return index
        self._provider_combo.addItem(
            self._t("current_provider_unavailable", provider=provider),
            provider,
        )
        return self._provider_combo.count() - 1

    def _key_input_for_provider(self, backend: str) -> QLineEdit | None:
        fields = self._provider_fields.get(backend)
        if fields is not None:
            return fields["api_key"]  # type: ignore[return-value]
        return {
            "deepseek": self._deepseek_key_input,
            "gemini": self._gemini_key_input,
            "qianwen": self._qwen_key_input,
        }.get(backend)

    def _prompt_for_missing_credential(self, provider_id: str | None = None) -> bool:
        backend = preserve_provider_id(
            provider_id if provider_id is not None else self._provider_combo.currentData()
        )
        # Unknown/plugin backends must remain untouched.  Passing one through the
        # global normalizer would incorrectly turn it into OpenAI.
        if backend not in _PROMPTABLE_PROVIDER_BACKENDS:
            return False
        key_input = self._key_input_for_provider(backend)
        provider_fields = self._provider_fields.get(backend, {})
        base_url_input = provider_fields.get("base_url")
        config = {
            "translation": {
                "backend": backend,
                backend: {
                    "api_key": key_input.text().strip() if key_input is not None else "",
                    "base_url": (
                        base_url_input.text().strip()
                        if isinstance(base_url_input, QLineEdit)
                        else ""
                    ),
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
            trigger="api_models_provider_selection",
            active_only=False,
        )
        return True

    def focus_credential(self, missing: MissingCredential) -> None:
        backend = preserve_provider_id(missing.provider_id)
        index = self._ensure_provider_choice(backend)
        if index != self._provider_combo.currentIndex():
            self._suppress_credential_prompt = True
            try:
                self._provider_combo.setCurrentIndex(index)
            finally:
                self._suppress_credential_prompt = False
        key_input = self._key_input_for_provider(backend)
        if key_input is not None:
            key_input.setFocus()

    def _test_openai(self) -> None:
        self._test_provider("openai")

    def _test_anthropic(self) -> None:
        self._test_provider("anthropic")

    def _test_grok(self) -> None:
        self._test_provider("grok_compatible")

    def _test_provider(self, backend: str) -> None:
        if self._prompt_for_missing_credential(backend):
            return
        try:
            config = self.get_config()
        except ValueError as exc:
            logger.warning(
                "Provider connection settings validation failed (%s)",
                type(exc).__name__,
            )
            QMessageBox.warning(
                self,
                self._t("connection_test_failed"),
                self._t(
                    "connection_test_invalid_settings",
                    error=self._t("unknown_error"),
                ),
            )
            return

        config["translation"]["backend"] = backend
        button = self._provider_fields[backend]["test_button"]
        assert isinstance(button, QPushButton)
        button.setEnabled(False)
        button.setText(self._t("connection_test_running"))

        def worker() -> None:
            try:
                result = test_translation_connection(config)
            except Exception as exc:  # provider errors are formatted on the UI thread
                self.connection_test_finished.emit(backend, False, exc)
            else:
                self.connection_test_finished.emit(backend, True, result)

        threading.Thread(
            target=worker,
            name=f"{backend}-connection-test",
            daemon=True,
        ).start()

    def _finish_connection_test(
        self,
        backend: str,
        success: bool,
        result: object,
    ) -> None:
        fields = self._provider_fields.get(backend, {})
        button = fields.get("test_button")
        if isinstance(button, QPushButton):
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

    def _timeout_value(
        self,
        backend: str,
        config_key: str,
        label_key: str,
        minimum: float,
        maximum: float,
        optional: bool,
    ) -> float | None:
        entry = self._provider_fields[backend][config_key]
        assert isinstance(entry, QLineEdit)
        value = entry.text()
        if not value.strip() and not optional:
            value = get_backend_value(backend, "timeout_s") or "15"
        try:
            return parse_optional_timeout(
                value,
                minimum=minimum,
                maximum=maximum,
                optional=optional,
            )
        except ValueError as exc:
            raise ValueError(
                self._t(
                    "timeout_value_invalid",
                    field=self._t(label_key),
                    minimum=minimum,
                    maximum=maximum,
                )
            ) from exc

    def _validated_base_url(self, backend: str) -> str:
        entry = self._provider_fields[backend]["base_url"]
        assert isinstance(entry, QLineEdit)
        candidate = entry.text().strip() or get_backend_value(backend, "base_url")
        try:
            validate_api_base_url(
                candidate,
                label="API",
                allow_private_http=backend
                in {"local_ai", "openai_compatible", "grok_compatible"},
            )
        except ValueError as exc:
            raise ValueError(
                self._t("invalid_base_url", provider=self._provider_label(backend))
            ) from exc
        # Validation is deliberately separate from storage so custom relay URLs,
        # including a meaningful trailing slash, survive exactly as entered.
        return candidate

    def _provider_label(self, backend: str) -> str:
        index = self._provider_combo.findData(backend)
        return self._provider_combo.itemText(index) if index >= 0 else backend

    def get_config(self) -> dict:
        translation = copy.deepcopy(self._translation_snapshot)
        backend = preserve_provider_id(self._provider_combo.currentData())
        translation["backend"] = backend

        active_model = self._model_combo.currentText()
        self._provider_models[backend] = active_model

        for provider in AI_PROVIDER_BACKENDS:
            existing = translation.get(provider)
            provider_cfg = copy.deepcopy(existing) if isinstance(existing, dict) else {}
            fields = self._provider_fields[provider]
            api_key = fields["api_key"]
            headers_input = fields["custom_headers"]
            streaming = fields["streaming"]
            assert isinstance(api_key, QLineEdit)
            assert isinstance(headers_input, QLineEdit)
            assert isinstance(streaming, QCheckBox)

            provider_cfg["api_key"] = api_key.text().strip()
            provider_cfg["base_url"] = self._validated_base_url(provider)
            model = self._provider_models.get(provider, "").strip()
            provider_cfg["model"] = model or get_backend_value(provider, "model")
            for config_key, label_key, minimum, maximum, optional in _TIMEOUT_FIELDS:
                parsed = self._timeout_value(
                    provider,
                    config_key,
                    label_key,
                    minimum,
                    maximum,
                    optional,
                )
                if parsed is None:
                    provider_cfg.pop(config_key, None)
                else:
                    provider_cfg[config_key] = parsed
            try:
                provider_cfg["custom_headers"] = normalize_openai_custom_headers(
                    headers_input.text()
                )
            except ValueError as exc:
                raise ValueError(self._t("invalid_custom_headers")) from exc
            provider_cfg["streaming"] = streaming.isChecked()
            translation[provider] = provider_cfg

        for provider, key_input in (
            ("deepseek", self._deepseek_key_input),
            ("gemini", self._gemini_key_input),
        ):
            existing = translation.get(provider)
            provider_cfg = copy.deepcopy(existing) if isinstance(existing, dict) else {}
            provider_cfg["api_key"] = key_input.text().strip()
            if self._provider_models.get(provider, "").strip():
                provider_cfg["model"] = self._provider_models[provider].strip()
            translation[provider] = provider_cfg

        qwen_existing = translation.get("qianwen", translation.get("qwen", {}))
        qwen_cfg = copy.deepcopy(qwen_existing) if isinstance(qwen_existing, dict) else {}
        qwen_cfg["api_key"] = self._qwen_key_input.text().strip()
        qwen_cfg["region"] = normalize_qwen_translation_region(
            self._qwen_region_combo.currentData()
        )
        qwen_cfg["base_url"] = (
            get_qwen_translation_base_url(qwen_cfg["region"])
            or self._qwen_base_url_input.text().strip().rstrip("/")
        )
        if qwen_cfg["region"] == "japan":
            qwen_cfg["base_url"] = require_qwen_tokyo_workspace_base_url(
                qwen_cfg["base_url"],
                endpoint_path=QWEN_TOKYO_COMPATIBLE_MODE_PATH,
                label="Qwen Tokyo workspace API",
            )
        if self._provider_models.get("qianwen", "").strip():
            qwen_cfg["model"] = self._provider_models["qianwen"].strip()
        translation["qianwen"] = qwen_cfg

        # A current provider supplied by a plugin or newer build is preserved as
        # an opaque mapping.  Its model is only updated when the player actually
        # edits the visible model field.
        if backend not in _PROMPTABLE_PROVIDER_BACKENDS:
            existing = translation.get(backend)
            backend_cfg = copy.deepcopy(existing) if isinstance(existing, dict) else {}
            if active_model.strip():
                backend_cfg["model"] = active_model.strip()
            translation[backend] = backend_cfg

        return {"translation": translation}

    def load_config(self, config: dict) -> None:
        self._loading_config = True
        try:
            self._load_config_values(config)
        finally:
            self._loading_config = False

    def _load_config_values(self, config: dict) -> None:
        trans_cfg = _mapping(config.get("translation"))
        self._translation_snapshot = copy.deepcopy(dict(trans_cfg))
        self._provider_models = {
            preserve_provider_id(provider): str(provider_cfg.get("model", "") or "")
            for provider, provider_cfg in trans_cfg.items()
            if isinstance(provider_cfg, Mapping)
        }

        for backend in AI_PROVIDER_BACKENDS:
            provider_cfg = _mapping(trans_cfg.get(backend))
            fields = self._provider_fields[backend]
            key_input = fields["api_key"]
            url_input = fields["base_url"]
            headers_input = fields["custom_headers"]
            streaming = fields["streaming"]
            assert isinstance(key_input, QLineEdit)
            assert isinstance(url_input, QLineEdit)
            assert isinstance(headers_input, QLineEdit)
            assert isinstance(streaming, QCheckBox)
            key_input.setText(str(provider_cfg.get("api_key", "") or ""))
            url_input.setText(
                str(
                    provider_cfg.get("base_url", get_backend_value(backend, "base_url"))
                    or get_backend_value(backend, "base_url")
                )
            )
            for config_key, _label_key, _minimum, _maximum, optional in _TIMEOUT_FIELDS:
                entry = fields[config_key]
                assert isinstance(entry, QLineEdit)
                raw_value = provider_cfg.get(config_key)
                if raw_value is None and not optional:
                    raw_value = get_backend_value(backend, "timeout_s") or 15
                entry.setText("" if raw_value is None else str(raw_value))
            custom_headers = provider_cfg.get("custom_headers", {})
            headers_input.setText(
                json.dumps(
                    custom_headers,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if isinstance(custom_headers, dict) and custom_headers
                else ""
            )
            raw_streaming = provider_cfg.get(
                "streaming",
                backend == "grok_compatible",
            )
            if isinstance(raw_streaming, str):
                streaming_value = raw_streaming.strip().casefold() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "enabled",
                }
            else:
                streaming_value = bool(raw_streaming)
            streaming.setChecked(streaming_value)

        self._deepseek_key_input.setText(
            str(_mapping(trans_cfg.get("deepseek")).get("api_key", "") or "")
        )
        self._gemini_key_input.setText(
            str(_mapping(trans_cfg.get("gemini")).get("api_key", "") or "")
        )
        qwen_cfg = _mapping(trans_cfg.get("qianwen", trans_cfg.get("qwen", {})))
        self._qwen_key_input.setText(str(qwen_cfg.get("api_key", "") or ""))
        region = normalize_qwen_translation_region(
            qwen_cfg.get("region")
            or qwen_translation_region_for_ui_language(self._ui_language)
        )
        region_index = self._qwen_region_combo.findData(region)
        self._qwen_region_combo.setCurrentIndex(max(0, region_index))
        configured_qwen_base_url = str(qwen_cfg.get("base_url", "") or "").strip()
        regional_qwen_base_url = get_qwen_translation_base_url(region)
        if (
            region == "japan"
            and configured_qwen_base_url in get_backend_known_base_urls("qianwen")
        ):
            configured_qwen_base_url = ""
        self._qwen_base_url_input.setText(
            regional_qwen_base_url or configured_qwen_base_url
        )
        self._qwen_base_url_input.setReadOnly(bool(regional_qwen_base_url))

        backend = preserve_provider_id(trans_cfg.get("backend", "openai"))
        provider_index = self._ensure_provider_choice(backend)
        self._current_backend = backend
        self._provider_combo.setCurrentIndex(provider_index)
        self._on_provider_changed(provider_index)

        backend_cfg = _mapping(trans_cfg.get(backend))
        selected_model = str(backend_cfg.get("model", "") or "")
        if selected_model:
            self._provider_models[backend] = selected_model
            model_index = self._model_combo.findText(selected_model)
            if model_index < 0:
                self._model_combo.insertItem(0, selected_model)
                model_index = 0
            self._model_combo.setCurrentIndex(model_index)
