"""Strict source-tree quality gate, isolated from all user indexes."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.registry import IndexRegistry

REPOSITORY = Path(__file__).resolve().parents[1]
MAX_FILE_LINES = 300

# Exact reviewed public interfaces and framework-dispatched methods only.
REVIEWED_UNUSED = (
    ("application/ignore.py", "runtime_ignore_patterns", "Public service compatibility accessor."),
    ("application/ignore.py", "privileged_ignore_patterns", "Public service compatibility accessor."),
    ("application/ignore.py", "add_auto_ignore_patterns", "Public service policy helper."),
    ("core/service.py", "runtime_ignore_patterns", "Public compatibility facade; internal code uses ignore_config."),
    ("core/service.py", "privileged_ignore_patterns", "Public compatibility facade; internal code uses ignore_config."),
    ("core/service.py", "add_auto_ignore_patterns", "Public compatibility facade for automatic exclusions."),
    ("embedding/indexer.py", "embed_files", "Partial embedding API tested by vector-store and recovery contracts."),
    ("embedding/indexer.py", "delete_files", "Model-space deletion API tested by namespace lifecycle contracts."),
    ("indexing/watcher.py", "on_any_event", "watchdog dispatches this FileSystemEventHandler override by name."),
    ("languages/python_refs.py", "visit_Call", "ast.NodeVisitor dispatches Call nodes by method name."),
    ("languages/python_refs.py", "visit_Name", "ast.NodeVisitor dispatches Name nodes by method name."),
    ("languages/python_refs.py", "visit_Attribute", "ast.NodeVisitor dispatches Attribute nodes by method name."),
    ("languages/python_refs.py", "visit_ImportFrom", "ast.NodeVisitor dispatches ImportFrom nodes by method name."),
    ("languages/python_refs.py", "visit_Assign", "ast.NodeVisitor dispatches Assign nodes by method name."),
    ("languages/python_refs.py", "visit_ClassDef", "ast.NodeVisitor dispatches ClassDef nodes by method name."),
    ("registry/cleanup.py", "build_lock_active", "Exported registry diagnostic covered by live-holder tests."),
    ("registry/cleanup.py", "remove_index_dir", "Exported cleanup contract covered by known-file tests."),
    ("runtime/diagnostics.py", "filter", "logging.Handler invokes registered Filter objects by protocol."),
    ("storage/index.py", "update_metadata", "Metadata API used by compatibility and recovery tests."),
    ("storage/index.py", "symbols_for_files", "Partial symbol projection API used by embedding recovery."),
    ("storage/index.py", "symbol_count", "Cardinality API checked by atomic-publication tests."),
    ("storage/vectors.py", "purge_other_models", "Explicit garbage collection; never implicit on model switching."),
)


def evaluate_quality(nesting, dangling, files, baseline=None) -> dict[str, Any]:
    baseline = baseline or dict()
    failures, accepted, unexpected = [], [], []
    summary = nesting["summary"]
    if summary["total_findings"]:
        failures.append(f"deep nesting: {summary['total_findings']}")
    if summary["nesting_coverage"] != 1.0 or not summary["reliable"]:
        failures.append("nesting analysis coverage is incomplete")
    for finding in dangling["findings"]:
        key = (finding["path"], finding.get("symbol"))
        reason = baseline.get(key) if finding["kind"] == "unused_symbol" else None
        if reason and finding["confidence"] == "medium":
            accepted.append(dict(finding, review_reason=reason))
        else:
            unexpected.append(finding)
    if unexpected:
        failures.append(f"unreviewed dangling findings: {len(unexpected)}")
    if dangling["summary"]["total_findings"] != len(dangling["findings"]):
        failures.append("dangling findings were truncated")
    unresolved = [item["path"] for item in files
                  if item["language"] == "python" and item.get("analysis_kind") != "python-ast"]
    if unresolved:
        failures.append(f"Python AST analysis unavailable: {', '.join(unresolved)}")
    return dict(failures=failures, accepted_findings=accepted, unexpected_findings=unexpected,
                nesting_findings=nesting["findings"], parser_failures=unresolved)


def _line_limit_findings(project: Path, files) -> list[dict]:
    findings = [dict(path=item["path"], lines=item["line_count"]) for item in files
                if item["language"] == "python" and item["line_count"] > MAX_FILE_LINES]
    tests = project / "tests"
    if (project / "src").is_dir() and tests.is_dir():
        for path in tests.rglob("*.py"):
            with path.open(encoding="utf-8-sig") as stream:
                count = sum(1 for _ in stream)
            if count > MAX_FILE_LINES:
                findings.append(dict(path=path.relative_to(project).as_posix(), lines=count))
    return findings


def _navigation_probe(service, files) -> dict:
    candidate = next((item for item in files if item["symbols"]), None)
    if candidate is None:
        return dict(find_files_items=0, text_search_items=0, text_search_backend=None)
    resolve = service.find_files(query=candidate["path"])
    search = service.text_search(candidate["symbols"][0]["name"], limit=5)
    return dict(find_files_items=len(resolve["items"]), text_search_items=len(search.get("items", [])),
                text_search_backend=search.get("backend"), text_search_error=search.get("error"))


def _check_service(service, project: Path, source: Path) -> dict:
    enable = service.enable(str(source), rebuild=True, refresh_embedder=False)
    files = service.all_files()
    nesting = service.nesting_check(max_depth=4, limit=1000)
    dangling = service.dangling_check(limit=1000)
    baseline = dict()
    if project == REPOSITORY:
        baseline = dict(((f"auto_index_mcp/{path}", symbol), reason) for path, symbol, reason in REVIEWED_UNUSED)
    report = evaluate_quality(nesting, dangling, files, baseline)
    report.update(project=str(project), source=str(source), nesting_summary=nesting["summary"],
                  dangling_summary=dangling["summary"], line_limit_findings=_line_limit_findings(project, files))
    report["enable"] = dict((key, enable.get(key)) for key in
                            ("status", "file_count", "error_count", "errors", "elapsed_seconds"))
    report.update(_navigation_probe(service, files))
    failures = report["failures"]
    if enable.get("status") != "indexed" or enable.get("error_count"):
        failures.append("source indexing failed")
    if enable.get("auto_ignored_paths"):
        failures.append("source files exceeded the indexing byte limit")
    if report["line_limit_findings"]:
        failures.append(f"files over {MAX_FILE_LINES} lines: {len(report['line_limit_findings'])}")
    if not files:
        failures.append("no source files were indexed")
    if any(item["symbols"] for item in files):
        if not report["find_files_items"] or not report["text_search_items"]:
            failures.append("navigation probe failed")
    report["ok"] = not failures
    return report


def run_self_quality_check(project: Path | None = None) -> dict[str, Any]:
    project = (project or REPOSITORY).resolve()
    source = project / "src" if (project / "src").is_dir() else project
    with TemporaryDirectory(prefix="auto-index-quality-") as temporary:
        service = AutoIndexService(index_root=Path(temporary) / "index")
        service.registry = IndexRegistry(Path(temporary) / "registry.json")
        service.semantic_enabled = False
        try:
            return _check_service(service, project, source)
        finally:
            service.disable()


def main() -> int:
    report = run_self_quality_check()
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        print("self quality check failed:", ", ".join(report["failures"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
