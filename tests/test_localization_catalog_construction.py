from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _literal(node: ast.AST, environment: dict[str, object]) -> object:
    if isinstance(node, ast.Name):
        return environment.get(node.id)
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError):
        return None


def _catalog_assignment(
    tree: ast.Module,
    variable_name: str,
) -> dict[str, object]:
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else (statement.target,)
        )
        if not any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in targets
        ):
            continue
        value = ast.literal_eval(statement.value)
        assert isinstance(value, dict)
        return value
    raise AssertionError(f"Missing catalog assignment: {variable_name}")


def _duplicate_catalog_writes(
    relative_path: str,
    variable_name: str,
) -> tuple[list[str], dict[str, object]]:
    """Interpret literal catalog construction and report silent overwrites."""

    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    environment: dict[str, object] = {}
    catalog: dict[str, object] = {}
    duplicates: list[str] = []

    def write_outer(additions: object, line_number: int) -> None:
        if not isinstance(additions, dict):
            return
        for key, value in additions.items():
            if key in catalog:
                duplicates.append(f"{relative_path}:{line_number} duplicate {key!r}")
            catalog[key] = dict(value) if isinstance(value, dict) else value

    def write_inner(key: object, additions: object, line_number: int) -> None:
        if not isinstance(key, str) or not isinstance(additions, dict):
            return
        current = catalog.setdefault(key, {})
        if not isinstance(current, dict):
            return
        for inner_key, value in additions.items():
            if inner_key in current:
                duplicates.append(
                    f"{relative_path}:{line_number} duplicate "
                    f"{key!r}.{inner_key!r}"
                )
            current[inner_key] = value

    def process(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else (statement.target,)
                )
                value = _literal(statement.value, environment)
                for target in targets:
                    if isinstance(target, ast.Name):
                        if isinstance(value, dict):
                            environment[target.id] = value
                        if target.id == variable_name and isinstance(value, dict):
                            catalog.clear()
                            catalog.update(
                                {
                                    key: dict(item) if isinstance(item, dict) else item
                                    for key, item in value.items()
                                }
                            )
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Subscript)
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == variable_name
                    ):
                        write_inner(
                            _literal(target.value.slice, environment),
                            {_literal(target.slice, environment): value},
                            statement.lineno,
                        )
                continue

            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                call = statement.value
                if not (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "update"
                    and call.args
                ):
                    continue
                receiver = call.func.value
                additions = _literal(call.args[0], environment)
                if isinstance(receiver, ast.Name) and receiver.id == variable_name:
                    write_outer(additions, statement.lineno)
                elif (
                    isinstance(receiver, ast.Subscript)
                    and isinstance(receiver.value, ast.Name)
                    and receiver.value.id == variable_name
                ):
                    write_inner(
                        _literal(receiver.slice, environment),
                        additions,
                        statement.lineno,
                    )
                elif (
                    isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Attribute)
                    and receiver.func.attr == "setdefault"
                    and isinstance(receiver.func.value, ast.Name)
                    and receiver.func.value.id == variable_name
                    and receiver.args
                ):
                    write_inner(
                        _literal(receiver.args[0], environment),
                        additions,
                        statement.lineno,
                    )
                continue

            if not isinstance(statement, ast.For):
                continue
            if not (
                isinstance(statement.target, (ast.Tuple, ast.List))
                and len(statement.target.elts) == 2
                and all(isinstance(item, ast.Name) for item in statement.target.elts)
                and isinstance(statement.iter, ast.Call)
                and isinstance(statement.iter.func, ast.Attribute)
                and statement.iter.func.attr == "items"
                and isinstance(statement.iter.func.value, ast.Name)
            ):
                process(statement.body)
                continue
            source = environment.get(statement.iter.func.value.id)
            if not isinstance(source, dict):
                process(statement.body)
                continue
            key_name, value_name = (
                item.id for item in statement.target.elts if isinstance(item, ast.Name)
            )
            for key, value in source.items():
                environment[key_name] = key
                environment[value_name] = value
                process(statement.body)

    process(tree.body)
    return duplicates, catalog


def test_translation_catalog_construction_does_not_overwrite_entries():
    settings_duplicates, _settings = _duplicate_catalog_writes(
        "src/ui_qt/settings_window.py",
        "QT_SETTINGS_COPY",
    )
    completion_duplicates, completions = _duplicate_catalog_writes(
        "src/utils/i18n.py",
        "UI_TEXT_COMPLETIONS",
    )

    i18n_tree = ast.parse(
        (ROOT / "src/utils/i18n.py").read_text(encoding="utf-8")
    )
    base_catalog = _catalog_assignment(i18n_tree, "UI_TEXTS")
    base_completion_overlap = [
        f"src/utils/i18n.py duplicate UI_TEXTS[{language!r}].{key!r}"
        for language, values in base_catalog.items()
        if isinstance(values, dict)
        for key in set(values) & set(completions.get(language, {}))
    ]

    assert settings_duplicates + completion_duplicates + base_completion_overlap == []
