from __future__ import annotations

import re

from ..core._utils import strip_comments, strip_string_literals
from ..core.models import SymbolRecord

TYPE_RE = re.compile(r"^\s*(?:class|struct|enum(?:\s+class)?)\s+([A-Za-z_][\w]*)")
CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "return", "sizeof"}
LEADING_KEYWORDS = {"namespace", "using", "typedef", "static_assert"}


def extract_c_family_symbols(lines: list[str]) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    index = 0
    while index < len(lines):
        type_match = TYPE_RE.match(_clean(lines[index]))
        if type_match:
            records.append(_record(type_match.group(1), _type_kind(lines[index]), index, lines))
            index += 1
            continue
        candidate = _function_candidate(lines, index)
        if candidate:
            name, kind, end_line = candidate
            records.append(SymbolRecord(name=name, kind=kind, line=index + 1, end_line=end_line, signature=_signature(lines, index)))
            index = max(index + 1, end_line)
            continue
        index += 1
    return records


def _function_candidate(lines: list[str], start: int) -> tuple[str, str, int] | None:
    first = _clean(lines[start])
    if not first or first.startswith("#") or _starts_with_keyword(first):
        return None
    header_lines = []
    paren_depth = 0
    saw_paren = False
    for index in range(start, min(start + 16, len(lines))):
        text = _clean(lines[index])
        if not text:
            continue
        header_lines.append(text)
        paren_depth += text.count("(") - text.count(")")
        saw_paren = saw_paren or "(" in text
        joined = " ".join(header_lines)
        if "{" in text and saw_paren and paren_depth <= 0:
            info = _function_info(joined.split("{", 1)[0])
            if not info:
                return None
            name, is_method = info
            return name, "method" if is_method else "function", _find_brace_end(lines, index)
        if ";" in text and paren_depth <= 0:
            return None
    return None


def _function_info(header: str) -> tuple[str, bool] | None:
    prefix = header.split("(", 1)[0]
    # Only an assignment '=' BEFORE the parameter list disqualifies a function
    # (e.g. `int x = foo();`). A '=' inside the parens is a default argument and
    # must not suppress the symbol (e.g. `void Configure(int retries = 3)`).
    if "=" in prefix and "operator" not in prefix:
        return None
    prefix = prefix.strip()
    if not prefix:
        return None
    raw_token = prefix.split()[-1]
    is_method = "::" in raw_token
    raw_name = raw_token.split("::")[-1]
    if raw_name in CONTROL_NAMES or not re.match(r"^~?[A-Za-z_][\w]*$", raw_name):
        return None
    return raw_name.lstrip("~"), is_method


def _record(name: str, kind: str, index: int, lines: list[str]) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        line=index + 1,
        end_line=_find_brace_end(lines, index),
        signature=_signature(lines, index),
    )


def _type_kind(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("struct "):
        return "struct"
    if stripped.startswith("enum "):
        return "enum"
    return "class"


def _signature(lines: list[str], start: int) -> str:
    parts = []
    for line in lines[start:min(start + 6, len(lines))]:
        text = _clean(line)
        if text:
            parts.append(text)
        if "{" in text:
            break
    return " ".join(parts).strip()


def _find_brace_end(lines: list[str], start: int) -> int:
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        text = _clean(lines[index])
        depth = max(0, depth - text.count("}"))
        opens = text.count("{")
        opened = opened or opens > 0
        depth += opens
        if opened and depth <= 0:
            return index + 1
    return len(lines)


def _starts_with_keyword(text: str) -> bool:
    first = text.split(None, 1)[0].rstrip(":")
    return first in CONTROL_NAMES or first in LEADING_KEYWORDS


def _clean(line: str) -> str:
    return strip_comments(strip_string_literals(line)).strip()
