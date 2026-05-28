from __future__ import annotations

import re

from ..core.models import SymbolRecord

SYMBOL_PATTERNS = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_][\w]*)")),
    ("struct", re.compile(r"^\s*(?:export\s+)?struct\s+([A-Za-z_][\w]*)")),
    ("enum", re.compile(r"^\s*(?:export\s+)?enum\s+([A-Za-z_][\w]*)")),
    ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_][\w]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?def\s+([A-Za-z_][\w]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:export\s+)?func\s+([A-Za-z_][\w]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][\w]*)\s*\(")),
    ("function", re.compile(r"^\s*(?:class\s+)?function\s+([A-Za-z_][\w]*)\s*\(", re.IGNORECASE)),
    ("procedure", re.compile(r"^\s*(?:class\s+)?procedure\s+([A-Za-z_][\w]*)\s*\(", re.IGNORECASE)),
    ("function", re.compile(r"^\s*(?:public|private|protected|static|virtual|inline|constexpr)\s+[\w:<>,\[\]?*&\s]+\s+([A-Za-z_][\w]*)\s*\([^;]*\)\s*(?:\{|$)")),
    ("variable", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=")),
    ("type", re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_][\w]*)\s*=")),
]


def extract_symbols(lines: list[str]) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    for index, line in enumerate(lines):
        matched = _match_symbol(line)
        if not matched:
            continue
        kind, name = matched
        end_line = _find_end_line(lines, index)
        records.append(
            SymbolRecord(
                name=name,
                kind=kind,
                line=index + 1,
                end_line=end_line,
                signature=line.strip(),
            )
        )
    return records


def _match_symbol(line: str) -> tuple[str, str] | None:
    stripped = line.lstrip()
    if stripped.startswith(("raise ", "return ", "if ", "for ", "while ", "switch ")):
        return None
    for kind, pattern in SYMBOL_PATTERNS:
        match = pattern.match(line)
        if match:
            return kind, match.group(1)
    return None


def _find_end_line(lines: list[str], start_index: int) -> int:
    start_line = lines[start_index]
    stripped = start_line.lstrip()
    if stripped.endswith(":") and not "{" in stripped:
        return _find_python_block_end(lines, start_index)
    return _find_brace_block_end(lines, start_index)


def _find_python_block_end(lines: list[str], start_index: int) -> int:
    base_indent = len(lines[start_index]) - len(lines[start_index].lstrip())
    end_index = start_index
    for index in range(start_index + 1, len(lines)):
        text = lines[index]
        stripped = text.strip()
        if not stripped:
            end_index = index
            continue
        indent = len(text) - len(text.lstrip())
        if indent <= base_indent:
            break
        end_index = index
    return end_index + 1


def _find_brace_block_end(lines: list[str], start_index: int) -> int:
    depth = 0
    opened = False
    for index in range(start_index, len(lines)):
        line = _strip_string_literals(lines[index])
        depth += line.count("{")
        if line.count("{"):
            opened = True
        depth -= line.count("}")
        if opened and depth <= 0:
            return index + 1
        if not opened and index > start_index and lines[index].strip():
            return index
    return min(start_index + 50, len(lines))


def _strip_string_literals(line: str) -> str:
    return re.sub(r"(['\"]).*?\1", "", line)
