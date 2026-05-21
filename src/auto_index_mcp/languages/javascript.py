from __future__ import annotations

import re

from ..core.models import SymbolRecord
from .generic import extract_symbols


TS_EXPORT_RE = re.compile(r"^\s*export\s+(?:default\s+)?")
ARROW_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][\w]*)\s*=>")
METHOD_RE = re.compile(r"^\s*(?:public|private|protected|static|async|override|readonly|\s)*([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{")


def extract_javascript_like_symbols(lines: list[str]) -> list[SymbolRecord]:
    records = extract_symbols(lines)
    names = {(record.name, record.line) for record in records}
    for index, line in enumerate(lines):
        arrow = ARROW_RE.match(line)
        if arrow and (arrow.group(1), index + 1) not in names:
            records.append(_record(arrow.group(1), "function", index, lines))
            continue
        method = METHOD_RE.match(TS_EXPORT_RE.sub("", line))
        if method and (method.group(1), index + 1) not in names:
            records.append(_record(method.group(1), "method", index, lines))
    return sorted(records, key=lambda item: (item.line, item.name))


def _record(name: str, kind: str, index: int, lines: list[str]) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        line=index + 1,
        end_line=_find_brace_end(lines, index),
        signature=lines[index].strip(),
    )


def _find_brace_end(lines: list[str], start_index: int) -> int:
    depth = 0
    opened = False
    for index in range(start_index, len(lines)):
        line = _strip_strings(lines[index])
        depth += line.count("{")
        opened = opened or "{" in line
        depth -= line.count("}")
        if opened and depth <= 0:
            return index + 1
    return min(start_index + 50, len(lines))


def _strip_strings(line: str) -> str:
    return re.sub(r"(['\"`]).*?\1", "", line)
