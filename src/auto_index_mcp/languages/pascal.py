from __future__ import annotations

import re

from ..core.models import SymbolRecord

CLASS_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*class\b", re.IGNORECASE)
ROUTINE_RE = re.compile(r"^\s*(?:class\s+)?(procedure|function)\s+([A-Za-z_][\w.]*)(?:\s*\(|\s*;|\s*:)", re.IGNORECASE)


def extract_pascal_symbols(lines: list[str]) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    for index, line in enumerate(lines):
        class_match = CLASS_RE.match(line)
        if class_match:
            records.append(_record(class_match.group(1), "class", index, _find_class_end(lines, index), line))
            continue
        routine = ROUTINE_RE.match(line)
        if routine and _has_body(lines, index):
            full_name = routine.group(2)
            records.append(
                _record(
                    full_name.split(".")[-1],
                    "method" if "." in full_name else routine.group(1).lower(),
                    index,
                    _find_routine_end(lines, index),
                    line,
                )
            )
    return records


def _record(name: str, kind: str, index: int, end_line: int, line: str) -> SymbolRecord:
    return SymbolRecord(name=name, kind=kind, line=index + 1, end_line=end_line, signature=line.strip())


def _has_body(lines: list[str], start: int) -> bool:
    for index in range(start + 1, min(start + 20, len(lines))):
        text = lines[index].strip().lower()
        if not text:
            continue
        if text.startswith(("implementation", "procedure ", "function ", "class procedure ", "class function ", "type ")):
            return False
        if text.startswith("begin"):
            return True
    return False


def _find_class_end(lines: list[str], start: int) -> int:
    for index in range(start + 1, len(lines)):
        if lines[index].strip().lower().startswith("end"):
            return index + 1
    return min(start + 80, len(lines))


def _find_routine_end(lines: list[str], start: int) -> int:
    depth = 0
    opened = False
    for index in range(start + 1, len(lines)):
        text = lines[index].strip().lower()
        if not text:
            continue
        if text.startswith(("begin", "case ", "try", "repeat")):
            depth += 1
            opened = True
        if text.startswith(("end", "until ")):
            depth -= 1
            if opened and depth <= 0:
                return index + 1
    return min(start + 80, len(lines))
