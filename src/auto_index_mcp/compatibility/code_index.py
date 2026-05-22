from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..core.service import AutoIndexService


class CompatService:
    def __init__(self, service: AutoIndexService) -> None:
        self.service = service

    def set_project_path(self, path: str) -> str:
        result = self.service.enable(path, rebuild=True)
        return f"Project path set to: {result['root']}. Indexed {result['file_count']} files."

    def refresh_index(self) -> str:
        result = self.service.rebuild()
        return f"Shallow index re-built with {result['file_count']} files."

    def build_deep_index(self, max_workers: int | None = None, timeout: int | None = None) -> str:
        _ = max_workers, timeout
        result = self.service.rebuild()
        return f"Project re-indexed. Found {result['file_count']} files."

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
        limit = (max_results or 10) + start_index
        response = self.service.text_search(
            pattern=search_pattern,
            case_sensitive=case_sensitive,
            regex=bool(regex or fuzzy),
            limit=limit,
            file_pattern=file_pattern,
            context_lines=context_lines,
        )
        all_matches = response["items"]
        page = all_matches[start_index:start_index + (max_results or 10)]
        return {
            "pattern": pattern,
            "matches": page,
            "total_matches": len(all_matches),
            "start_index": start_index,
            "max_results": max_results,
            "has_more": len(all_matches) > start_index + len(page),
            "backend": response["backend"],
        }

    def get_settings_info(self) -> dict[str, Any]:
        return self.service.status()

    def get_file_watcher_status(self) -> dict[str, Any]:
        return self.service.watcher_status()

    def configure_file_watcher(
        self,
        enabled: bool | None = None,
        debounce_seconds: float | None = None,
        additional_exclude_patterns: list | None = None,
        observer_type: str | None = None,
    ) -> str:
        _ = additional_exclude_patterns, observer_type
        if enabled is False:
            self.service.stop_watcher()
            return "File watcher disabled."
        if enabled is True:
            self.service.start_watcher(debounce_seconds or DEFAULT_WATCH_DEBOUNCE_SECONDS)
            return "File watcher enabled."
        return "File watcher configuration unchanged."

    def clear_settings(self) -> str:
        self.service.clear(delete_file=True)
        return "Settings and cached index cleared."


def _fuzzy_pattern(pattern: str) -> str:
    escaped = [char for char in pattern if char.strip()]
    return ".*".join(map(__import__("re").escape, escaped))
