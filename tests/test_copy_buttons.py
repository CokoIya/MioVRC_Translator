from src.ui.main_window import MainWindow


class _Textbox:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, *_args) -> str:
        return self._text


def _window():
    window = object.__new__(MainWindow)
    copied: list[str] = []
    window.clipboard_clear = lambda: copied.clear()
    window.clipboard_append = lambda text: copied.append(text)
    window._copied = copied
    return window


def test_copy_source_uses_original_text():
    window = _window()
    window._src_text = "original text"

    window._copy_source()

    assert window._copied == ["original text"]


def test_copy_result_uses_translated_textbox():
    window = _window()
    window._tgt_output = _Textbox("translated text\n")

    window._copy_result()

    assert window._copied == ["translated text"]
