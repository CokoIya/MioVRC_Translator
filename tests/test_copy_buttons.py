from src.ui_qt.main_window import MainWindow


def _window():
    window = MainWindow.__new__(MainWindow)
    copied: list[str] = []
    window._copied = copied
    return window


class _Clipboard:
    def __init__(self, copied: list[str]) -> None:
        self._copied = copied

    def setText(self, text: str) -> None:  # noqa: N802
        self._copied[:] = [text]


def test_copy_source_uses_original_text(monkeypatch):
    window = _window()
    window._src_text = "original text"
    monkeypatch.setattr(
        "src.ui_qt.main_window.QApplication.clipboard",
        lambda: _Clipboard(window._copied),
    )

    window._copy_source()

    assert window._copied == ["original text"]


def test_copy_result_uses_rendered_translation(monkeypatch):
    window = _window()
    window._tgt_rendered_text = "translated text"
    window._last_tgt_text = ""
    monkeypatch.setattr(
        "src.ui_qt.main_window.QApplication.clipboard",
        lambda: _Clipboard(window._copied),
    )

    window._copy_result()

    assert window._copied == ["translated text"]
