from __future__ import annotations

import ast

from ..core.models import SymbolRecord


def extract_python_symbols(text: str, lines: list[str]) -> list[SymbolRecord]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    records: list[SymbolRecord] = []
    _visit_body(tree.body, lines, records, in_class=False)
    return sorted(records, key=lambda item: (item.line, item.name))


def _visit_body(nodes: list[ast.stmt], lines: list[str], records: list[SymbolRecord], in_class: bool) -> None:
    for node in nodes:
        _visit_node(node, lines, records, in_class)


def _visit_node(node: ast.AST, lines: list[str], records: list[SymbolRecord], in_class: bool) -> None:
    """Visit definitions below control-flow nodes without changing scope."""
    if isinstance(node, ast.ClassDef):
        records.append(_record(node.name, "class", node, lines))
        _visit_body(node.body, lines, records, in_class=True)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        records.append(_record(node.name, "method" if in_class else "function", node, lines))
        _visit_body(node.body, lines, records, in_class=False)
        return
    for child in ast.iter_child_nodes(node):
        _visit_node(child, lines, records, in_class)


def _record(name: str, kind: str, node: ast.AST, lines: list[str]) -> SymbolRecord:
    line = getattr(node, "lineno", 1)
    end_line = getattr(node, "end_lineno", line)
    signature = lines[line - 1].strip() if 0 < line <= len(lines) else name
    return SymbolRecord(name=name, kind=kind, line=line, end_line=end_line, signature=signature)
