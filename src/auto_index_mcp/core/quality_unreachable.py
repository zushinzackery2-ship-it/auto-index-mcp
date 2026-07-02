"""Per-file unreachable-code detection (Python AST + brace-language scan)."""

from __future__ import annotations

import ast
import re
from typing import Any

from .source_clean import clean_source_lines

TERMINAL_NODES = (ast.Return, ast.Raise, ast.Break, ast.Continue)
BRACE_TERMINAL_RE = re.compile(r"\b(return|throw|break|continue)\b")
BRACE_LANGUAGES = {"c", "cpp", "csharp", "go", "java", "javascript", "php", "rust", "typescript"}


def file_quality_findings(
    item: dict[str, Any],
    text: str,
    cleaned_lines: list[str] | None = None,
) -> list[dict[str, Any]]:
    return _unreachable_findings(item, text, cleaned_lines)


def _unreachable_findings(
    item: dict[str, Any],
    text: str,
    cleaned_lines: list[str] | None = None,
) -> list[dict[str, Any]]:
    if item["language"] == "python":
        return _python_unreachable(item, text)
    if item["language"] in BRACE_LANGUAGES:
        return _brace_unreachable(item, text, cleaned_lines)
    return []


def _python_unreachable(item: dict[str, Any], text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[dict[str, Any]] = []
    _visit_python_block(item, tree.body, findings)
    return findings


def _visit_python_block(item: dict[str, Any], body: list[ast.stmt], findings: list[dict[str, Any]]) -> None:
    terminal_line: int | None = None
    for stmt in body:
        if terminal_line is not None:
            findings.append(_unreachable_statement(item, "high", getattr(stmt, "lineno", terminal_line), terminal_line))
            continue
        _visit_python_children(item, stmt, findings)
        if isinstance(stmt, TERMINAL_NODES):
            terminal_line = getattr(stmt, "lineno", None)


def _visit_python_children(item: dict[str, Any], stmt: ast.stmt, findings: list[dict[str, Any]]) -> None:
    for field in ("body", "orelse", "finalbody"):
        child = getattr(stmt, field, None)
        if isinstance(child, list):
            _visit_python_block(item, child, findings)
    handlers = getattr(stmt, "handlers", None)
    if handlers:
        for handler in handlers:
            _visit_python_block(item, handler.body, findings)
    cases = getattr(stmt, "cases", None)
    if cases:
        for case in cases:
            _visit_python_block(item, case.body, findings)


def _brace_unreachable(
    item: dict[str, Any],
    text: str,
    cleaned_lines: list[str] | None = None,
) -> list[dict[str, Any]]:
    lines = text.splitlines()
    cleaned = cleaned_lines if cleaned_lines is not None else clean_source_lines(lines, item["language"])
    findings = []
    for symbol in item["symbols"]:
        if symbol["kind"] in {"function", "method", "procedure"}:
            findings.extend(_brace_unreachable_in_symbol(item, symbol, cleaned))
    return findings


def _brace_unreachable_in_symbol(item: dict[str, Any], symbol: dict[str, Any], cleaned: list[str]) -> list[dict[str, Any]]:
    findings = []
    depth = 0
    terminal_depth: int | None = None
    terminal_line: int | None = None
    start = max(1, symbol["line"])
    end = min(len(cleaned), symbol["end_line"])
    for line_no in range(start, end + 1):
        text = cleaned[line_no - 1].strip()
        leading_closes = len(text) - len(text.lstrip("}"))
        current_depth = max(0, depth - leading_closes)
        if terminal_depth is not None and current_depth == terminal_depth and _is_executable_after_terminal(text):
            findings.append(_unreachable_statement(item, "medium", line_no, terminal_line, symbol["name"]))
            terminal_depth = None
        if BRACE_TERMINAL_RE.search(text):
            terminal_depth = current_depth
            terminal_line = line_no
        depth = max(0, current_depth + text.count("{"))
    return findings


def _unreachable_statement(
    item: dict[str, Any],
    confidence: str,
    line: int,
    after_line: int | None,
    symbol: str | None = None,
) -> dict[str, Any]:
    finding = {
        "kind": "unreachable_statement",
        "confidence": confidence,
        "path": item["path"],
        "language": item["language"],
        "line": line,
        "after_line": after_line,
        "reason": "statement appears after a terminal control-flow statement in the same block",
    }
    if symbol:
        finding["symbol"] = symbol
    return finding


def _is_executable_after_terminal(text: str) -> bool:
    if not text or text in {"}", "};"}:
        return False
    return not text.startswith(("case ", "default:", "else", "catch", "finally"))
