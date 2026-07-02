"""Project-level dangling-code checks: unused symbols and orphan files."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .models import FileRecord
from .multi_match import MultiPatternMatcher

CALLABLE_KINDS = {"class", "function", "method", "procedure", "struct", "enum", "interface"}
ENTRYPOINT_NAMES = {"main", "setup", "teardown"}
PROJECT_FINDING_KINDS = {"unused_symbol", "orphan_file"}
# Markup/data files produce pseudo-symbols (markdown code fences, config keys)
# that have no call graph; never report them as dangling code.
NON_CODE_LANGUAGES = {"markdown", "text", "json", "yaml", "toml", "xml", "html", "css"}


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
    called_member_parents = _called_member_parent_names(files)
    for item in files:
        for symbol in item["symbols"]:
            if not _is_dangling_candidate(item, symbol, called_member_parents) or symbol.get("called_by"):
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


def _called_member_parent_names(files: list[dict[str, Any]]) -> set[str]:
    return {
        symbol["parent_name"]
        for item in files
        for symbol in item["symbols"]
        if symbol.get("parent_name") and _has_external_member_caller(item["path"], symbol)
    }


def _has_external_member_caller(path: str, symbol: dict[str, Any]) -> bool:
    parent = symbol.get("parent_name")
    structural_callers = {parent, f"{path}::{parent}"}
    return any(caller not in structural_callers for caller in symbol.get("called_by", []))


def _is_dangling_candidate(
    item: dict[str, Any],
    symbol: dict[str, Any],
    called_member_parents: set[str],
) -> bool:
    name = symbol["name"]
    if item["language"] in NON_CODE_LANGUAGES:
        return False
    if symbol["kind"] not in CALLABLE_KINDS:
        return False
    if _is_dunder(name) or name in ENTRYPOINT_NAMES:
        return False
    if name.startswith("test_") or name.startswith("Test"):
        return False
    if symbol["kind"] == "class" and (name.endswith("Mixin") or name in called_member_parents):
        return False
    if symbol["kind"] == "function" and name.startswith("register_") and name.endswith("_tools"):
        return False
    if _is_protocol_symbol(symbol):
        return False
    if Path(item["path"]).name in {"__init__.py", "__main__.py"}:
        return False
    return not symbol.get("signature", "").startswith("export ")


def _is_protocol_symbol(symbol: dict[str, Any]) -> bool:
    return symbol["kind"] == "class" and "(Protocol" in symbol.get("signature", "")


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
    # One Aho-Corasick pass per import text replaces the former
    # every-source x every-target substring scan (O(files^2) on large trees)
    # with identical match semantics.
    incoming = {item["path"]: 0 for item in files}
    key_targets: dict[str, list[str]] = {}
    for item in files:
        for key in _file_import_keys(item["path"]):
            if key:
                key_targets.setdefault(key, []).append(item["path"])
    matcher = MultiPatternMatcher(key_targets)
    for item in files:
        import_text = "\n".join(item.get("imports", [])).replace("\\", "/")
        if not import_text:
            continue
        hit_targets: set[str] = set()
        for key in matcher.matched_patterns(import_text):
            hit_targets.update(key_targets[key])
        hit_targets.discard(item["path"])
        for target in hit_targets:
            incoming[target] += 1
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
