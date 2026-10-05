from __future__ import annotations

import re

from ..domain.models import SymbolRecord
from .source_clean import clean_source_lines
from .generic import extract_symbols
from .braces import BracePairs


TS_EXPORT_RE = re.compile(r"^\s*export\s+(?:default\s+)?")
ARROW_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][\w]*)\s*(?::\s*[^=]+)?=>")
METHOD_RE = re.compile(r"^\s*(?:public|private|protected|static|async|override|readonly|\s)*([A-Za-z_][\w]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{")


def extract_javascript_like_symbols(
    lines: list[str],
    cleaned_lines: list[str] | None = None,
) -> list[SymbolRecord]:
    cleaned = cleaned_lines if cleaned_lines is not None else clean_source_lines(lines, "javascript")
    records = {(record.name, record.line): record for record in extract_symbols(lines, cleaned)}
    pairs = BracePairs(cleaned)
    for index, line in enumerate(cleaned):
        arrow = ARROW_RE.match(line)
        if arrow:
            key = (arrow.group(1), index + 1)
            records[key] = _record(arrow.group(1), "function", index, lines, pairs)
            continue
        method = METHOD_RE.match(TS_EXPORT_RE.sub("", line))
        if method and (method.group(1), index + 1) not in records:
            records[(method.group(1), index + 1)] = _record(method.group(1), "method", index, lines, pairs)
    return sorted(records.values(), key=lambda item: (item.line, item.name))


def _record(name: str, kind: str, index: int, lines: list[str], pairs: BracePairs) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        line=index + 1,
        end_line=pairs.end_line(index),
        signature=lines[index].strip(),
    )
