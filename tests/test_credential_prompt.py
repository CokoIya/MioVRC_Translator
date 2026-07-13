from __future__ import annotations

from src.ui_qt import credential_prompt
from src.utils.credential_validation import MissingCredential
from src.utils.i18n import tr


class _FakeMessageBox:
    class Icon:
        Warning = object()

    class ButtonRole:
        ActionRole = object()
        RejectRole = object()

    last_instance = None

    def __init__(self, parent):
        self.parent = parent
        self.title = ""
        self.text = ""
        self.buttons = []
        self._clicked = None
        type(self).last_instance = self

    def setIcon(self, icon):
        self.icon = icon

    def setWindowTitle(self, title):
        self.title = title

    def setText(self, text):
        self.text = text

    def addButton(self, text, role):
        button = object()
        self.buttons.append((text, role, button))
        return button

    def exec(self):
        self._clicked = self.buttons[0][2]

    def clickedButton(self):
        return self._clicked


def _missing() -> MissingCredential:
    return MissingCredential(
        scope="translation",
        provider_id="openai",
        provider_label="GPT",
        credential_id="translation.openai.api_key",
        credential_label="GPT API Key",
        focus_target="backend_api_key",
    )


def test_missing_credential_prompt_opens_settings_without_secret(monkeypatch):
    monkeypatch.setattr(credential_prompt, "QMessageBox", _FakeMessageBox)
    opened = []

    assert credential_prompt.show_missing_credential_prompt(
        None,
        _missing(),
        ui_language="en",
        open_settings=lambda: opened.append(True),
    )

    dialog = _FakeMessageBox.last_instance
    assert opened == [True]
    assert dialog.title == tr("en", "api_credential_missing_title")
    assert "GPT" in dialog.text
    assert "API Key" in dialog.text
    assert "sk-private-secret" not in dialog.text
    assert dialog.buttons[0][0] == tr("en", "api_credential_open_settings")


def test_missing_credential_prompt_copy_exists_for_every_supported_language(
    monkeypatch,
):
    monkeypatch.setattr(credential_prompt, "QMessageBox", _FakeMessageBox)

    for language in ("zh-CN", "en", "ja", "ru", "ko"):
        credential_prompt.show_missing_credential_prompt(
            None,
            _missing(),
            ui_language=language,
            open_settings=lambda: None,
        )
        dialog = _FakeMessageBox.last_instance
        assert dialog.title
        assert "GPT" in dialog.text
        assert dialog.buttons[0][0] == tr(language, "api_credential_open_settings")
