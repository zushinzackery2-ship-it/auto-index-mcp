from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.pagination import PageRequest
from ..core.service import AutoIndexService
from ..workspace.context import ContextLoader


class CompatService:
    def __init__(self, service: AutoIndexService) -> None:
        self.service = service
        self.last_result: dict[str, Any] | None = None

    def set_project_path(self, path: str) -> str:
        root = Path(path).resolve()
        if self._can_return_active_status(root):
            result = self.service.status()
        else:
            db_existed = self.service._db_path(root).exists()
            result = self.service.enable(str(root), rebuild=False)
            if db_existed and self.service.can_reuse_index_for(root):
                # Reuse the existing index and return immediately. Catching up
                # files changed while offline is the watcher's job: its first
                # background tick diffs the live tree against the index, so doing
                # the same full os.walk synchronously here only blocks the request.
                result = self.service.status()
            else:
                # First-time build: a cross-process lock collapses concurrent
                # agents pointing at the same directory into a single rebuild;
                # losers reuse the index the winner just produced.
                result = self.service.rebuild(reuse_if_fresh=True)
        self.last_result = result
        total = result.get("total_file_count", result["file_count"])
        child_count = result.get("child_index_count", 0)
        child_suffix = f" across {child_count} child indexes" if child_count else ""
        return f"Project path set to: {result['root']}. Indexed {total} total files ({result['file_count']} local{child_suffix})."

    def _can_return_active_status(self, root: Path) -> bool:
        return (
            self.service.enabled
            and self.service.root_path == root
            and self.service.store is not None
        )

    def find_files(self, pattern: str) -> list[str]:
        self.service._require_store()
        matches = []
        for item in self.service.all_files():
            path = item["path"]
            if Path(path).match(pattern) or Path(path).name == pattern:
                matches.append(path)
        return matches

    def get_file_summary(self, file_path: str) -> dict[str, Any]:
        summary = self.service.file_summary(file_path)
        functions = []
        methods = []
        classes = []
        for symbol in summary["symbols"]:
            target = {
                "name": symbol["name"],
                "line": symbol["line"],
                "end_line": symbol["end_line"],
                "signature": symbol["signature"],
                "complexity": symbol.get("complexity", 1),
                "called_by": symbol.get("called_by", []),
                "calls": symbol.get("calls", []),
            }
            if symbol["kind"] == "class":
                classes.append(target)
            elif symbol["kind"] == "method":
                methods.append(target)
            elif symbol["kind"] == "function":
                functions.append(target)
        return {
            "file_path": summary["path"],
            "language": summary["language"],
            "line_count": summary["line_count"],
            "imports": summary["imports"],
            "functions": functions,
            "methods": methods,
            "classes": classes,
            "complexity": {"total": summary["total_complexity"], "max": summary["max_complexity"]},
        }

    def get_symbol_body(self, file_path: str, symbol_name: str) -> dict[str, Any]:
        result = self.service.symbol_body(file_path, symbol_name)
        symbol = result["symbol"]
        return {
            "status": "success",
            "truncated": False,
            "symbol_name": symbol_name,
            "type": symbol["kind"],
            "file_path": file_path,
            "line": symbol["line"],
            "end_line": symbol["end_line"],
            "code": result["code"],
            "signature": symbol["signature"],
            "docstring": None,
            "called_by": symbol.get("called_by", []),
        }

    def search_code_advanced(
        self,
        pattern: str,
        case_sensitive: bool = True,
        context_lines: int = 0,
        file_pattern: str | None = None,
        fuzzy: bool = False,
        regex: bool | None = None,
        start_index: int = 0,
        max_results: int | None = 10,
    ) -> dict[str, Any]:
        search_pattern = _fuzzy_pattern(pattern) if fuzzy and not regex else pattern
        try:
            page = PageRequest.from_values(start_index, max_results)
        except ValueError as exc:
            message = str(exc).replace("offset", "start_index").replace("limit", "max_results")
            raise ValueError(message) from exc
        response = self.service.text_search(
            pattern=search_pattern,
            case_sensitive=case_sensitive,
            regex=bool(regex or fuzzy),
            limit=page.probe_limit,
            file_pattern=file_pattern,
            context_lines=0,
        )
        page_result = page.slice(response["items"])
        matches = page_result.items
        if context_lines > 0:
            if self.service.root_path is None:
                raise RuntimeError("auto-index root is not configured")
            matches = ContextLoader(self.service.view, self.service.root_path).attach(matches, context_lines)
        return {
            "pattern": pattern,
            "matches": matches,
            "total_matches": page_result.scanned,
            "scanned_matches": page_result.scanned,
            "start_index": start_index,
            "max_results": max_results,
            "has_more": page_result.has_more,
            "backend": response["backend"],
        }

def _fuzzy_pattern(pattern: str) -> str:
    escaped = [char for char in pattern if char.strip()]
    return ".*".join(map(__import__("re").escape, escaped))
