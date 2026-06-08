from __future__ import annotations

from pathlib import Path
from typing import Any

from .view import WorkspaceView


class ContextLoader:
    def __init__(self, view: WorkspaceView, root: Path) -> None:
        self.view = view
        self.root = root
        self._lines_by_path: dict[str, list[str]] = {}

    def attach(self, matches: list[dict[str, Any]], context_lines: int) -> list[dict[str, Any]]:
        if context_lines <= 0:
            return matches
        with_context = []
        for match in matches:
            lines = self._lines(match["path"])
            updated = dict(match)
            updated["context"] = self._context(match, lines, context_lines)
            with_context.append(updated)
        return with_context

    def _lines(self, path: str) -> list[str]:
        if path not in self._lines_by_path:
            try:
                self._lines_by_path[path] = self.view.read_text(self.root, path).splitlines()
            except UnicodeDecodeError:
                self._lines_by_path[path] = []
        return self._lines_by_path[path]

    def _context(self, match: dict[str, Any], lines: list[str], context_lines: int) -> list[dict[str, Any]]:
        line_index = match["line"] - 1
        start = max(0, line_index - context_lines)
        end = min(len(lines), line_index + context_lines + 1)
        return [{"line": index + 1, "text": lines[index]} for index in range(start, end)]
