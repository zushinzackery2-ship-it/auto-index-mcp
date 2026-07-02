from __future__ import annotations

import re

from ..core.models import SymbolRecord
from ..core.source_clean import clean_source_lines

TYPE_RE = re.compile(r"^\s*(?:class|struct|enum(?:\s+class)?)\s+([A-Za-z_][\w]*)")
CONTROL_NAMES = {"if", "for", "while", "switch", "catch", "return", "sizeof"}
LEADING_KEYWORDS = {"namespace", "using", "typedef", "static_assert"}


def extract_c_family_symbols(
    lines: list[str],
    cleaned_lines: list[str] | None = None,
) -> list[SymbolRecord]:
    # Comment blocks and string literals are blanked up front, so brace counting
    # and signature detection below only ever see executable code. This is what
    # keeps macro/comment-dense sources (ImGui-style) from producing phantom
    # symbol starts or runaway end lines.
    cleaned = cleaned_lines if cleaned_lines is not None else clean_source_lines(lines, "cpp")
    records: list[SymbolRecord] = []
    index = 0
    while index < len(lines):
        type_match = TYPE_RE.match(cleaned[index])
        if type_match:
            records.append(_record(type_match.group(1), _type_kind(cleaned[index]), index, cleaned))
            index += 1
            continue
        candidate = _function_candidate(cleaned, index)
        if candidate:
            name, kind, end_line = candidate
            records.append(
                SymbolRecord(
                    name=name,
                    kind=kind,
                    line=index + 1,
                    end_line=end_line,
                    signature=_signature(cleaned, index),
                )
            )
            index = max(index + 1, end_line)
            continue
        index += 1
    return records


def _function_candidate(cleaned: list[str], start: int) -> tuple[str, str, int] | None:
    first = cleaned[start].strip()
    if not first or first.startswith("#") or _starts_with_keyword(first):
        return None
    header_lines = []
    paren_depth = 0
    saw_paren = False
    for index in range(start, min(start + 16, len(cleaned))):
        text = cleaned[index].strip()
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
            return name, "method" if is_method else "function", _find_brace_end(cleaned, index)
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


def _record(name: str, kind: str, index: int, cleaned: list[str]) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        kind=kind,
        line=index + 1,
        end_line=_find_brace_end(cleaned, index),
        signature=_signature(cleaned, index),
    )


def _type_kind(cleaned_line: str) -> str:
    stripped = cleaned_line.lstrip()
    if stripped.startswith("struct "):
        return "struct"
    if stripped.startswith("enum "):
        return "enum"
    return "class"


def _signature(cleaned: list[str], start: int) -> str:
    parts = []
    for line in cleaned[start:min(start + 6, len(cleaned))]:
        text = " ".join(line.split())
        if text:
            parts.append(text)
        if "{" in text:
            break
    return " ".join(parts).strip()


def _find_brace_end(cleaned: list[str], start: int) -> int:
    depth = 0
    opened = False
    for index in range(start, len(cleaned)):
        text = cleaned[index]
        depth = max(0, depth - text.count("}"))
        opens = text.count("{")
        opened = opened or opens > 0
        depth += opens
        if opened and depth <= 0:
            return index + 1
    # No balanced closing brace anywhere in the file: the start was most likely
    # mis-detected (macros / comment blocks throw off the brace count). Fall back
    # to a minimal span so one bad symbol cannot swallow the rest of a large
    # file's calls and nesting (e.g. an 11k-line file collapsing into one symbol).
    return min(start + 1, len(cleaned))


def _starts_with_keyword(text: str) -> bool:
    first = text.split(None, 1)[0].rstrip(":")
    return first in CONTROL_NAMES or first in LEADING_KEYWORDS
