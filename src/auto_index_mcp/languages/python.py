from __future__ import annotations

import ast
from dataclasses import replace

from ..domain.models import SymbolRecord
from .python_refs import scope_references


def parse_python(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, RecursionError):
        return None


def extract_python_symbols(text: str, lines: list[str], tree: ast.Module | None = None) -> list[SymbolRecord]:
    tree = tree if tree is not None else parse_python(text)
    if tree is None:
        return []
    records: list[SymbolRecord] = []
    module = scope_references(tree, {})
    _visit_body(tree.body, lines, records, in_class=False, aliases=module.aliases,
                qualifiers=module.qualifiers, scope="")
    return sorted(records, key=lambda item: (item.line, item.name))


def _visit_body(nodes, lines, records, in_class, aliases, qualifiers, scope) -> None:
    for node in nodes:
        _visit_node(node, lines, records, in_class, aliases, qualifiers, scope)


def _visit_node(node, lines, records, in_class, aliases, qualifiers, scope) -> None:
    """Visit definitions below control-flow nodes without changing scope."""
    if isinstance(node, ast.ClassDef):
        refs = scope_references(node, aliases, qualifiers=qualifiers)
        child_scope = f"{scope}.{node.name}" if scope else node.name
        records.append(_enriched_record(node, "class", lines, refs, child_scope))
        _visit_body(node.body, lines, records, in_class=True, aliases=refs.aliases,
                    qualifiers=refs.qualifiers, scope=child_scope)
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        refs = scope_references(node, aliases, owner=scope if in_class else "", qualifiers=qualifiers)
        child_scope = f"{scope}.{node.name}" if scope else node.name
        records.append(_enriched_record(node, "method" if in_class else "function", lines, refs, child_scope))
        _visit_body(node.body, lines, records, in_class=False, aliases=refs.aliases,
                    qualifiers=refs.qualifiers, scope=child_scope)
        return
    for child in ast.iter_child_nodes(node):
        _visit_node(child, lines, records, in_class, aliases, qualifiers, scope)


def _enriched_record(node, kind, lines, refs, scope) -> SymbolRecord:
    calls = [name for name in refs.calls if name != node.name]
    return replace(
        _record(node.name, kind, node, lines), complexity=refs.complexity, calls=calls,
        refs=[name for name in refs.refs if name != node.name and name not in refs.calls],
        reference_evidence=list(refs.evidence.values()), nesting_path=scope,
    )


def _record(name: str, kind: str, node: ast.AST, lines: list[str]) -> SymbolRecord:
    line = getattr(node, "lineno", 1)
    end_line = getattr(node, "end_lineno", line)
    signature = lines[line - 1].strip() if 0 < line <= len(lines) else name
    return SymbolRecord(name=name, kind=kind, line=line, end_line=end_line, signature=signature)
