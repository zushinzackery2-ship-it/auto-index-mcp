from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.errors import ServiceError
from .source import MAX_CONTEXT_BYTES, MAX_SOURCE_BYTES, clip_utf8, read_source
from .view import WorkspaceView


class ContextLoader:
    def __init__(self, view: WorkspaceView, root: Path) -> None:
        self.view, self.root = view, root

    def attach(self, matches: list[dict[str, Any]], context_lines: int) -> list[dict[str, Any]]:
        if context_lines <= 0:
            return matches
        radius = min(context_lines, 20)
        output_budget, read_budget = MAX_CONTEXT_BYTES, MAX_SOURCE_BYTES
        result = [dict(match) for match in matches]
        by_path: dict[str, list[dict]] = {}
        for match in result:
            by_path.setdefault(match["path"], []).append(match)
        for path, group in by_path.items():
            lines, metadata = [], {}
            if output_budget and read_budget:
                item = self.view.get_file(path).item or dict(path=path)
                try:
                    lines, metadata = read_source(self.root, item, read_limit=read_budget)
                    read_budget -= metadata["read_bytes"]
                except ServiceError as exc:
                    metadata = dict(context_error=exc.code)
            for match in group:
                context, used, truncated = _context(lines, match["line"], radius, output_budget)
                match.update(context=context, context_truncated=truncated or context_lines > radius, **metadata)
                output_budget -= used
        return result


def _context(lines: list[str], line: int, radius: int, budget: int) -> tuple[list[dict], int, bool]:
    start, end = max(1, line - radius), min(len(lines), line + radius)
    context, used = [], 0
    truncated = not lines or line > len(lines)
    for number in range(start, end + 1):
        text = clip_utf8(lines[number - 1], budget - used)
        used += len(text.encode("utf-8"))
        context.append(dict(line=number, text=text))
        if text != lines[number - 1] or used >= budget:
            truncated = True
            break
    return context, used, truncated
