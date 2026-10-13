from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter

# Long option lists (a relay can expose hundreds of models) scroll inside a
# popup of this many rows instead of growing to the height of the screen.
COMBO_MAX_VISIBLE_ITEMS = 12


class NoWheelComboBox(QComboBox):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setMaxVisibleItems(COMBO_MAX_VISIBLE_ITEMS)

    def wheelEvent(self, event) -> None:  # noqa: N802
        view = self.view()
        if view is not None and view.isVisible():
            super().wheelEvent(event)
            return
        event.ignore()

    def enable_contains_completer(self) -> None:
        """Let an editable combo filter its items by any substring as you type."""

        if not self.isEditable():
            return
        completer = QCompleter(self.model(), self)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setMaxVisibleItems(COMBO_MAX_VISIBLE_ITEMS)
        self.setCompleter(completer)
