"""The src import graph must stay acyclic, deferred imports included.

A function-level import can hide a cycle from the interpreter, but it still
inverts the layering and can fail depending on which module loads first
(ui_config once lost its catalog that way, silently).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _module_name(path: Path) -> str:
    parts = ("src", *path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking_block(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)


def _imported_names(path: Path, tree: ast.AST) -> set[str]:
    package = _module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    skipped = {
        id(child)
        for node in ast.walk(tree)
        if _is_type_checking_block(node)
        for child in ast.walk(node)
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _import_graph() -> dict[str, set[str]]:
    paths = [path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts]
    modules = {_module_name(path): path for path in paths}
    graph: dict[str, set[str]] = {}
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        graph[module] = {
            name
            for name in _imported_names(path, tree)
            if name in modules and name != module
        }
    return graph


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(module: str) -> None:
        state[module] = 1
        stack.append(module)
        for dependency in sorted(graph.get(module, ())):
            if state.get(dependency) == 1:
                cycles.append([*stack[stack.index(dependency):], dependency])
            elif dependency not in state:
                visit(dependency)
        stack.pop()
        state[module] = 2

    for module in sorted(graph):
        if module not in state:
            visit(module)
    return cycles


def test_src_import_graph_has_no_cycles():
    cycles = _find_cycles(_import_graph())

    assert not cycles, "import cycles:\n" + "\n".join(" -> ".join(c) for c in cycles)
