from __future__ import annotations

import re
from dataclasses import replace

from ..core._utils import strip_comments, strip_string_literals
from ..core.models import SymbolRecord

BRACE_LANGUAGES = {
    "c",
    "cpp",
    "csharp",
    "go",
    "java",
    "javascript",
    "php",
    "rust",
    "typescript",
}
PYTHON_BLOCK_RE = re.compile(
    r"^(?:async\s+for|async\s+with|if|elif|else|for|while|try|except|finally|with|match|case)\b"
)


def annotate_symbol_nesting(lines: list[str], symbols: list[SymbolRecord], language: str) -> list[SymbolRecord]:
    records = list(symbols)
    if not records:
        return []

    ordered = sorted(range(len(records)), key=lambda index: (records[index].line, -records[index].end_line, records[index].name))
    parents: dict[int, int | None] = {}
    children: dict[int, list[int]] = {index: [] for index in range(len(records))}
    annotated: dict[int, SymbolRecord] = {}
    stack: list[int] = []

    for index in ordered:
        symbol = records[index]
        while stack and not _contains(records[stack[-1]], symbol):
            stack.pop()
        parent = stack[-1] if stack else None
        parents[index] = parent
        if parent is not None:
            children[parent].append(index)
        depth = len(stack)
        parent_symbol = annotated[parent] if parent is not None else None
        nesting_path = f"{parent_symbol.nesting_path}.{symbol.name}" if parent_symbol else symbol.name
        annotated[index] = replace(
            symbol,
            parent_name=parent_symbol.name if parent_symbol else "",
            parent_kind=parent_symbol.kind if parent_symbol else "",
            depth=depth,
            nesting_path=nesting_path,
            max_block_depth=_max_block_depth(lines, symbol, language),
        )
        stack.append(index)

    max_child_depth: dict[int, int] = {index: 0 for index in range(len(records))}
    for index, parent in parents.items():
        while parent is not None:
            max_child_depth[parent] = max(max_child_depth[parent], annotated[index].depth - annotated[parent].depth)
            parent = parents[parent]

    return [
        replace(
            annotated[index],
            children_count=len(children[index]),
            max_child_depth=max_child_depth[index],
        )
        for index in range(len(records))
    ]


def _contains(parent: SymbolRecord, child: SymbolRecord) -> bool:
    return (
        parent.line <= child.line
        and parent.end_line >= child.end_line
        and (parent.line, parent.end_line, parent.name) != (child.line, child.end_line, child.name)
    )


def _max_block_depth(lines: list[str], symbol: SymbolRecord, language: str) -> int:
    body = lines[symbol.line - 1:symbol.end_line]
    if language == "python":
        return _python_block_depth(lines, symbol)
    if language in BRACE_LANGUAGES or any("{" in line or "}" in line for line in body):
        return _brace_block_depth(body)
    return 0


def _python_block_depth(lines: list[str], symbol: SymbolRecord) -> int:
    if not lines or symbol.line < 1 or symbol.line > len(lines):
        return 0

    base_indent = _indent_width(lines[symbol.line - 1])
    active: list[int] = []
    max_depth = 0
    for line in lines[symbol.line:symbol.end_line]:
        stripped = strip_comments(line).strip()
        if not stripped:
            continue
        indent = _indent_width(line)
        if indent <= base_indent:
            active.clear()
            continue
        while active and indent <= active[-1]:
            active.pop()
        if stripped.endswith(":") and PYTHON_BLOCK_RE.match(stripped):
            active.append(indent)
            max_depth = max(max_depth, len(active))
    return max_depth


def _brace_block_depth(lines: list[str]) -> int:
    depth = 0
    max_depth = 0
    for line in lines:
        text = strip_comments(strip_string_literals(line))
        depth = max(0, depth - text.count("}"))
        opens = text.count("{")
        if opens:
            max_depth = max(max_depth, depth + opens)
        depth += opens
    return max(0, max_depth - 1)


def _indent_width(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))
