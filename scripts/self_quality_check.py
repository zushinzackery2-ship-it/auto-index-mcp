"""Self quality check: rebuild this repo's index in-process and report quality.

Runs against the CURRENT source tree (not a previously installed/running MCP
process), so it validates fresh changes end to end: full rebuild, nesting
check, dangling check, and a semantic-path sanity probe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from auto_index_mcp.core.service import AutoIndexService

DEFAULT_EXCLUDES = ["reference/**", "code_references/**", "dist/**"]
DANGLING_EXCLUDES = [*DEFAULT_EXCLUDES, "scripts/**"]


def run_self_quality_check(project: Path | None = None) -> dict[str, Any]:
    project = project or Path(__file__).resolve().parents[1]
    service = AutoIndexService(index_root=project / ".smoke-index")
    enable = service.enable(str(project), rebuild=True)
    nesting = service.nesting_check(max_depth=4, exclude_paths=DEFAULT_EXCLUDES)
    dangling = service.dangling_check(exclude_paths=DANGLING_EXCLUDES)
    resolve = service.resolve_path("service.py")
    search = service.text_search("AutoIndexService", limit=5)

    report = {
        "project": str(project),
        "enable": {
            "status": enable.get("status"),
            "file_count": enable.get("file_count"),
            "error_count": enable.get("error_count"),
            "errors": enable.get("errors", [])[:5],
            "elapsed_seconds": enable.get("elapsed_seconds"),
        },
        "nesting_summary": nesting["summary"],
        "dangling_summary": dangling["summary"],
        "resolve_path_items": len(resolve["items"]),
        "text_search_items": len(search["items"]),
        "text_search_backend": search["backend"],
    }
    failures: list[str] = []
    if enable.get("status") != "indexed":
        failures.append(f"enable status={enable.get('status')!r}")
    if enable.get("error_count"):
        failures.append(f"enable error_count={enable.get('error_count')}")
    if report["resolve_path_items"] < 1:
        failures.append("resolve_path returned no items")
    if report["text_search_items"] < 1:
        failures.append("text_search returned no items")
    report["ok"] = not failures
    report["failures"] = failures
    return report


def main() -> int:
    report = run_self_quality_check()
    print(json.dumps(report, indent=2))
    if not report["ok"]:
        print("self quality check failed:", ", ".join(report["failures"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
