from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

from ..search.backend import search_text
from ..workspace.view import WorkspaceView


class _SearchService(Protocol):
    root_path: Path | None

    @property
    def view(self) -> WorkspaceView:
        ...

    def _require_ready(self) -> None:
        ...

    def _require_store(self) -> None:
        ...


class ServiceSearchMixin:
    def text_search(
        self,
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 80,
        file_pattern: str | None = None,
        context_lines: int = 0,
    ) -> dict[str, Any]:
        service = cast(_SearchService, self)
        service._require_ready()
        if service.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not pattern:
            raise ValueError("pattern is required")
        backend, matches = search_text(
            service.root_path,
            service.view.all_files(),
            pattern,
            case_sensitive,
            regex,
            limit,
            file_pattern,
        )
        if context_lines > 0:
            matches = self._with_contexts(matches, context_lines)
        return {"format": "auto_index_text_search_indexed", "backend": backend, "items": matches}

    def symbol_search(self, text: str = "", kind: str = "", limit: int = 80, cursor: str | None = None) -> dict[str, Any]:
        service = cast(_SearchService, self)
        service._require_store()
        offset = int(cursor or "0")
        rows = service.view.query_symbols(text, kind, limit, offset)
        next_cursor = str(offset + limit) if len(rows) == limit else None
        return {"format": "auto_index_symbol_search_indexed", "items": rows, "cursor": next_cursor}

    def symbol_body(self, path: str, symbol_name: str) -> dict[str, Any]:
        service = cast(_SearchService, self)
        service._require_ready()
        if service.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not path or not symbol_name:
            raise ValueError("path and symbol_name are required")
        lookup = service.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        matches = [symbol for symbol in lookup.item["symbols"] if symbol["name"] == symbol_name]
        if not matches:
            raise KeyError(f"symbol not found: {symbol_name}")
        if len(matches) > 1:
            return {"format": "auto_index_symbol_body_ambiguous", "candidates": matches}
        symbol = matches[0]
        lines = service.view.read_indexed_text(service.root_path, lookup.item).splitlines()
        start = max(1, symbol["line"])
        end = min(len(lines), symbol["end_line"])
        code = "\n".join(lines[start - 1:end])
        return {"format": "auto_index_symbol_body_full", "symbol": symbol, "path": path, "code": code}

    def _with_context(self, match: dict[str, Any], context_lines: int) -> dict[str, Any]:
        service = cast(_SearchService, self)
        if service.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        lines = service.view.read_text(service.root_path, match["path"]).splitlines()
        return self._attach_context(match, lines, context_lines)

    def _with_contexts(self, matches: list[dict[str, Any]], context_lines: int) -> list[dict[str, Any]]:
        service = cast(_SearchService, self)
        if service.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        line_cache: dict[str, list[str] | None] = {}
        enriched_matches = []
        for match in matches:
            path = match["path"]
            if path not in line_cache:
                try:
                    line_cache[path] = service.view.read_text(service.root_path, path).splitlines()
                except UnicodeDecodeError:
                    line_cache[path] = None
            lines = line_cache[path]
            if lines is None:
                enriched = dict(match)
                enriched["context"] = []
                enriched_matches.append(enriched)
            else:
                enriched_matches.append(self._attach_context(match, lines, context_lines))
        return enriched_matches

    def _attach_context(self, match: dict[str, Any], lines: list[str], context_lines: int) -> dict[str, Any]:
        enriched = dict(match)
        line_index = match["line"] - 1
        start = max(0, line_index - context_lines)
        end = min(len(lines), line_index + context_lines + 1)
        enriched["context"] = [{"line": index + 1, "text": lines[index]} for index in range(start, end)]
        return enriched
