from __future__ import annotations

import re
from dataclasses import replace

from ..core.models import FileRecord, SymbolRecord
from ..core.source_clean import clean_source_lines
from .nesting import annotate_symbol_nesting

CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
CONTROL_NAMES = {"if", "for", "while", "switch", "return", "raise", "catch", "with"}
COMPLEXITY_RE = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|and|or|\?|&&|\|\|)\b")

# Value references: a bare identifier used as data rather than invoked -
# callback arguments (sort(key=fn)), kwarg values, assignment RHS, decorators,
# collection elements, return values. The trailing lookahead rejects names that
# are immediately called (CALL_RE territory), kwarg names (key=) and walrus /
# dict-key positions (name:). Line-local by design: cleaned lines carry no
# cross-line state, so a bare positional name on its own continuation line is
# not seen - acceptable for a heuristic whose job is suppressing false
# "unused symbol" verdicts, not building a complete reference graph.
REF_RE = re.compile(r"(?:[=(,\[{:]|@|\breturn\b|\byield\b)\s*&?\s*([A-Za-z_]\w*)\b(?!\s*[(=:])")
# Definition lines are skipped for reference extraction so parameter names in
# single-line signatures are never mistaken for value references.
DEF_LINE_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|procedure|constructor|interface|struct|enum)\b"
)
REF_STOP_NAMES = CONTROL_NAMES | {
    "False", "None", "True", "and", "as", "assert", "async", "await", "bool",
    "break", "case", "char", "class", "cls", "const", "continue", "def",
    "default", "del", "delete", "do", "double", "elif", "else", "except",
    "false", "finally", "float", "from", "function", "global", "import", "in",
    "int", "is", "lambda", "let", "long", "new", "nonlocal", "not", "null",
    "nullptr", "or", "pass", "self", "short", "signed", "sizeof", "static",
    "struct", "super", "this", "true", "try", "typeof", "undefined",
    "unsigned", "var", "void", "yield",
}
MAX_REFS_PER_SYMBOL = 64


def enrich_symbols(
    lines: list[str],
    symbols: list[SymbolRecord],
    language: str = "",
    cleaned_lines: list[str] | None = None,
) -> list[SymbolRecord]:
    # Comment/string regions are blanked once per file so complexity, call and
    # nesting analysis only ever see executable code.
    cleaned = cleaned_lines if cleaned_lines is not None else clean_source_lines(lines, language)
    enriched = []
    for symbol in symbols:
        body = cleaned[symbol.line - 1:symbol.end_line]
        calls = _calls(body, symbol.name)
        enriched.append(
            SymbolRecord(
                name=symbol.name,
                kind=symbol.kind,
                line=symbol.line,
                end_line=symbol.end_line,
                signature=symbol.signature,
                complexity=_complexity(body),
                calls=calls,
                called_by=[],
                refs=_value_refs(body, symbol.name, calls),
            )
        )
    return annotate_symbol_nesting(cleaned, enriched, language)


def resolve_project_callers(records: list[FileRecord]) -> list[FileRecord]:
    # Authoritative recompute: called_by is derived entirely from the (stable) calls
    # data of the current record set, never seeded from previously stored called_by.
    # This keeps the reverse-call graph self-healing - references to files that were
    # deleted or renamed simply stop being recomputed instead of lingering forever.
    symbol_locations = _symbol_locations(records)
    local_callers, project_callers = _caller_maps(records, symbol_locations)
    return _apply_callers(records, local_callers, project_callers)


def _symbol_locations(records: list[FileRecord]) -> dict[str, list[tuple[int, int]]]:
    symbol_locations: dict[str, list[tuple[int, int]]] = {}
    for record_index, record in enumerate(records):
        for symbol_index, symbol in enumerate(record.symbols):
            symbol_locations.setdefault(symbol.name, []).append((record_index, symbol_index))
    return symbol_locations


def _caller_maps(
    records: list[FileRecord],
    symbol_locations: dict[str, list[tuple[int, int]]],
) -> tuple[dict[tuple[int, int], list[str]], dict[tuple[int, int], list[str]]]:
    local_callers: dict[tuple[int, int], list[str]] = {}
    project_callers: dict[tuple[int, int], list[str]] = {}
    for record_index, record in enumerate(records):
        local_names = {symbol.name for symbol in record.symbols}
        for symbol in record.symbols:
            project_caller = f"{record.path}::{symbol.name}"
            # Value references count as usage edges exactly like calls: a
            # function handed to sort(key=...) or stored in a table has a user.
            for call in dict.fromkeys(symbol.calls + symbol.refs):
                _record_local_call(local_callers, symbol_locations, call, symbol.name, local_names, record_index)
                _record_project_call(project_callers, symbol_locations, call, project_caller, record_index)
    return local_callers, project_callers


def _record_local_call(
    callers: dict[tuple[int, int], list[str]],
    locations_by_name: dict[str, list[tuple[int, int]]],
    call: str,
    caller_name: str,
    local_names: set[str],
    record_index: int,
) -> None:
    if call not in local_names or call == caller_name:
        return
    for location in locations_by_name[call]:
        if location[0] == record_index:
            callers.setdefault(location, []).append(caller_name)


def _record_project_call(
    callers: dict[tuple[int, int], list[str]],
    locations_by_name: dict[str, list[tuple[int, int]]],
    call: str,
    project_caller: str,
    caller_record_index: int,
) -> None:
    locations = locations_by_name.get(call, [])
    # Same-file callers are already covered by the bare-name local edge; adding
    # the path-qualified form as well only duplicates every entry.
    if len(locations) == 1 and locations[0][0] != caller_record_index:
        callers.setdefault(locations[0], []).append(project_caller)


def _apply_callers(
    records: list[FileRecord],
    local_callers: dict[tuple[int, int], list[str]],
    project_callers: dict[tuple[int, int], list[str]],
) -> list[FileRecord]:
    updated_records = []
    for record_index, record in enumerate(records):
        symbols = []
        for symbol_index, symbol in enumerate(record.symbols):
            location = (record_index, symbol_index)
            resolved = list(dict.fromkeys(local_callers.get(location, []) + project_callers.get(location, [])))
            symbols.append(replace(symbol, called_by=resolved))
        updated_records.append(replace(record, symbols=symbols))
    return updated_records


def _complexity(cleaned_body: list[str]) -> int:
    score = 1
    for line in cleaned_body:
        score += len(COMPLEXITY_RE.findall(line))
    return score


def _calls(cleaned_body: list[str], own_name: str) -> list[str]:
    calls: list[str] = []
    for line in cleaned_body:
        for name in CALL_RE.findall(line):
            if name == own_name or name in CONTROL_NAMES:
                continue
            if name not in calls:
                calls.append(name)
    return calls


def _value_refs(cleaned_body: list[str], own_name: str, calls: list[str]) -> list[str]:
    called = set(calls)
    refs: list[str] = []
    for line in cleaned_body:
        if DEF_LINE_RE.match(line):
            continue
        for name in REF_RE.findall(line):
            if name == own_name or name in REF_STOP_NAMES or name in called:
                continue
            if name not in refs:
                refs.append(name)
                if len(refs) >= MAX_REFS_PER_SYMBOL:
                    return refs
    return refs
