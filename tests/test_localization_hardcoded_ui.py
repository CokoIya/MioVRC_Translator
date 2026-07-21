from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_ROOTS = (
    ROOT / "src" / "ui_qt",
    ROOT / "src" / "asr" / "webspeech_asr.py",
)

DISPLAY_CALLS = {
    "QAction",
    "QCheckBox",
    "QCommandLinkButton",
    "QGroupBox",
    "QLabel",
    "QListWidgetItem",
    "QMenu",
    "QPushButton",
    "QRadioButton",
    "QTableWidgetItem",
    "QTreeWidgetItem",
    "addButton",
    "addItem",
    "addItems",
    "addTab",
    "insertTab",
    "setAccessibleDescription",
    "setAccessibleName",
    "setCancelButtonText",
    "setHeaderLabel",
    "setHeaderLabels",
    "setHorizontalHeaderLabels",
    "setItemText",
    "setLabelText",
    "setNameFilter",
    "setNameFilters",
    "setFormat",
    "setDetailedText",
    "setInformativeText",
    "setPlaceholderText",
    "setStatusTip",
    "setText",
    "setToolTip",
    "setTitle",
    "setVerticalHeaderLabels",
    "setWhatsThis",
    "setWindowTitle",
    "showMessage",
    "_build_switch_row",
    "_row_layout",
    "_section_title",
    "_set_bottom",
    "_set_status",
}

ALL_TEXT_ARGUMENT_CALLS = {
    "QAction",
    "QCheckBox",
    "QCommandLinkButton",
    "QGroupBox",
    "QLabel",
    "QListWidgetItem",
    "QMenu",
    "QPushButton",
    "QRadioButton",
    "QTableWidgetItem",
    "QTreeWidgetItem",
    "addButton",
    "addTab",
    "insertTab",
    "setHeaderLabels",
    "setHorizontalHeaderLabels",
    "setItemText",
    "setVerticalHeaderLabels",
    "showMessage",
}

TEXT_ARGUMENT_INDEXES = {
    "_build_switch_row": (1,),
    "_row_layout": (1,),
    "_section_title": (1,),
}

# Non-language payloads and compact symbolic controls are intentionally literal.
ALLOWED_LITERAL_TEXT = {
    "%p%",
    "%v s",
    "0%",
    "100%",
    "0.5x",
    "1.0x",
    "2.0x",
    "—",
    "→",
    "👁",
    "🙈",
    "sk-...",
    "sk-ant-...",
    "xai-...",
    "AI...",
    "{translation}\n{original}",
    "v",
}


def _iter_python_files():
    for root in UI_ROOTS:
        if root.is_file():
            yield root
        else:
            yield from root.rglob("*.py")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_strings(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for item in node.elts:
            yield from _literal_strings(item)
    elif isinstance(node, ast.JoinedStr):
        literal = "".join(
            item.value for item in node.values
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
        if literal:
            yield literal
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        yield from _literal_strings(node.left)
        yield from _literal_strings(node.right)
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _literal_strings(value)
    elif isinstance(node, ast.IfExp):
        yield from _literal_strings(node.body)
        yield from _literal_strings(node.orelse)


def _is_non_language_payload(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text in ALLOWED_LITERAL_TEXT:
        return True
    if text.startswith(("http://", "https://")):
        return True
    if not any(character.isalpha() for character in text):
        return True
    if re.fullmatch(r"\d+ Hz", text):
        return True
    if re.fullmatch(r"[\d\s.,:+\-/%xX{}_[\]()]+", text):
        return True
    return False


def test_user_facing_qt_text_is_not_hard_coded():
    failures: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            is_message_box = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "QMessageBox"
            )
            is_file_dialog = (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"QFileDialog", "QInputDialog"}
            )
            if call_name not in DISPLAY_CALLS and not is_message_box and not is_file_dialog:
                continue
            if call_name in TEXT_ARGUMENT_INDEXES:
                arguments = [
                    node.args[index]
                    for index in TEXT_ARGUMENT_INDEXES[call_name]
                    if index < len(node.args)
                ]
            else:
                arguments = (
                    node.args
                    if is_message_box
                    or is_file_dialog
                    or call_name in ALL_TEXT_ARGUMENT_CALLS
                    else node.args[:1]
                )
            for argument in arguments:
                for literal in _literal_strings(argument):
                    if _is_non_language_payload(literal):
                        continue
                    relative = path.relative_to(ROOT)
                    failures.append(
                        f"{relative}:{node.lineno} {call_name}: {literal!r}"
                    )
    assert failures == []


def test_source_dict_literals_do_not_repeat_keys():
    failures: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            seen: dict[object, int] = {}
            for key_node in node.keys:
                if not isinstance(key_node, ast.Constant):
                    continue
                value = key_node.value
                try:
                    already_seen = value in seen
                except TypeError:
                    continue
                if already_seen:
                    relative = path.relative_to(ROOT)
                    failures.append(
                        f"{relative}:{key_node.lineno} duplicate {value!r}; "
                        f"first declared at line {seen[value]}"
                    )
                else:
                    seen[value] = key_node.lineno
    assert failures == []


def test_message_boxes_do_not_expose_a_raw_exception_as_the_entire_message():
    failures: list[str] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "QMessageBox"
            ):
                continue
            for argument in node.args[1:]:
                if (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id == "str"
                ):
                    relative = path.relative_to(ROOT)
                    failures.append(f"{relative}:{node.lineno}")
    assert failures == []
