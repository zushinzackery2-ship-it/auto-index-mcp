from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ._utils import strip_comments, strip_string_literals
from .models import FileRecord

CALLABLE_KINDS = {"class", "function", "method", "procedure", "struct", "enum", "interface"}
ENTRYPOINT_NAMES = {"main", "setup", "teardown"}
TERMINAL_NODES = (ast.Return, ast.Raise, ast.Break, ast.Continue)
BRACE_TERMINAL_RE = re.compile(r"\b(return|throw|break|continue)\b")
BRACE_LANGUAGES = {"c", "cpp", "csharp", "go", "java", "javascript", "php", "rust", "typescript"}
PROJECT_FINDING_KINDS = {"unused_symbol", "orphan_file"}


def dangling_report(
    files: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    include_low_confidence: bool = False,
    include_tests: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    checked_files = [item for item in files if include_tests or not _is_test_path(item["path"])]
    checked_paths = {item["path"] for item in checked_files}
    findings = [finding for finding in findings if finding["path"] in checked_paths]
    if not include_low_confidence:
        findings = [finding for finding in findings if finding["confidence"] != "low"]

    findings.sort(key=lambda finding: (_confidence_rank(finding["confidence"]), finding["path"], finding.get("line", 0)))
    limited = findings[:limit]
    confidence_counts = Counter(finding["confidence"] for finding in findings)
    return {
        "format": "auto_index_dangling_check_v1",
        "summary": {
            "files_checked": len(checked_files),
            "symbols_checked": sum(len(item["symbols"]) for item in checked_files),
            "findings": len(limited),
            "total_findings": len(findings),
            "confidence": dict(confidence_counts),
        },
        "findings": limited,
    }


def file_quality_findings(item: dict[str, Any], text: str) -> list[dict[str, Any]]:
    return _unreachable_findings(item, text)


def with_project_quality_findings(records: list[FileRecord]) -> list[FileRecord]:
    files = [_record_to_item(record) for record in records]
    project_findings = _unused_symbol_findings(files) + _orphan_file_findings(files)
    findings_by_path: dict[str, list[dict[str, Any]]] = {record.path: [] for record in records}
    for finding in project_findings:
        findings_by_path.setdefault(finding["path"], []).append(finding)

    updated = []
    for record in records:
        local_findings = [finding for finding in record.quality_findings if finding["kind"] not in PROJECT_FINDING_KINDS]
        updated.append(replace(record, quality_findings=local_findings + findings_by_path.get(record.path, [])))
    return updated


def _record_to_item(record: FileRecord) -> dict[str, Any]:
    return {
        "path": record.path,
        "language": record.language,
        "imports": record.imports,
        "symbols": [asdict(symbol) for symbol in record.symbols],
        "quality_findings": record.quality_findings,
    }


def _unused_symbol_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for item in files:
        for symbol in item["symbols"]:
            if not _is_dangling_candidate(item, symbol) or symbol.get("called_by"):
                continue
            confidence = "high" if symbol["name"].startswith("_") and not _is_dunder(symbol["name"]) else "medium"
            findings.append(
                {
                    "kind": "unused_symbol",
                    "confidence": confidence,
                    "path": item["path"],
                    "language": item["language"],
                    "symbol": symbol["name"],
                    "symbol_kind": symbol["kind"],
                    "line": symbol["line"],
                    "reason": "no indexed callers found",
                }
            )
    return findings


def _is_dangling_candidate(item: dict[str, Any], symbol: dict[str, Any]) -> bool:
    name = symbol["name"]
    if symbol["kind"] not in CALLABLE_KINDS:
        return False
    if _is_dunder(name) or name in ENTRYPOINT_NAMES:
        return False
    if name.startswith("test_") or name.startswith("Test"):
        return False
    if Path(item["path"]).name in {"__init__.py", "__main__.py"}:
        return False
    return not symbol.get("signature", "").startswith("export ")


def _unreachable_findings(item: dict[str, Any], text: str) -> list[dict[str, Any]]:
    if item["language"] == "python":
        return _python_unreachable(item, text)
    if item["language"] in BRACE_LANGUAGES:
        return _brace_unreachable(item, text)
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


def _brace_unreachable(item: dict[str, Any], text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    findings = []
    for symbol in item["symbols"]:
        if symbol["kind"] in {"function", "method", "procedure"}:
            findings.extend(_brace_unreachable_in_symbol(item, symbol, lines))
    return findings


def _brace_unreachable_in_symbol(item: dict[str, Any], symbol: dict[str, Any], lines: list[str]) -> list[dict[str, Any]]:
    findings = []
    depth = 0
    terminal_depth: int | None = None
    terminal_line: int | None = None
    start = max(1, symbol["line"])
    end = min(len(lines), symbol["end_line"])
    for line_no in range(start, end + 1):
        text = strip_comments(strip_string_literals(lines[line_no - 1])).strip()
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


def _orphan_file_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    incoming = _incoming_import_counts(files)
    findings = []
    for item in files:
        if incoming[item["path"]] or _is_entry_file(item["path"]):
            continue
        if any(symbol.get("called_by") for symbol in item["symbols"]):
            continue
        findings.append(
            {
                "kind": "orphan_file",
                "confidence": "low",
                "path": item["path"],
                "language": item["language"],
                "reason": "no cheap import edge or indexed caller points at this file",
            }
        )
    return findings


def _incoming_import_counts(files: list[dict[str, Any]]) -> dict[str, int]:
    incoming = {item["path"]: 0 for item in files}
    import_text_by_path = {item["path"]: "\n".join(item.get("imports", [])).replace("\\", "/") for item in files}
    keys = {item["path"]: _file_import_keys(item["path"]) for item in files}
    for source_path, import_text in import_text_by_path.items():
        for target_path, target_keys in keys.items():
            if source_path != target_path and any(key and key in import_text for key in target_keys):
                incoming[target_path] += 1
    return incoming


def _file_import_keys(path: str) -> set[str]:
    without_ext = str(Path(path).with_suffix("")).replace("\\", "/")
    return {without_ext, Path(path).stem}


def _is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name
    return "tests" in parts or name.startswith("test_") or name.endswith("_test.py")


def _is_entry_file(path: str) -> bool:
    name = Path(path).name
    return name in {"__init__.py", "__main__.py", "main.py", "index.js", "index.ts"}


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _confidence_rank(confidence: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(confidence, 3)
