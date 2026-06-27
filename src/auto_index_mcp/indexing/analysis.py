from __future__ import annotations

import re
from dataclasses import replace

from ..core.models import FileRecord, SymbolRecord
from ..core._utils import strip_comments
from .nesting import annotate_symbol_nesting

CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
CONTROL_NAMES = {"if", "for", "while", "switch", "return", "raise", "catch", "with"}
COMPLEXITY_RE = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|and|or|\?|&&|\|\|)\b")


def enrich_symbols(lines: list[str], symbols: list[SymbolRecord], language: str = "") -> list[SymbolRecord]:
    enriched = []
    for symbol in symbols:
        body = lines[symbol.line - 1:symbol.end_line]
        enriched.append(
            SymbolRecord(
                name=symbol.name,
                kind=symbol.kind,
                line=symbol.line,
                end_line=symbol.end_line,
                signature=symbol.signature,
                complexity=_complexity(body),
                calls=_calls(body, symbol.name),
                called_by=[],
            )
        )
    return annotate_symbol_nesting(lines, enriched, language)


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
            for call in symbol.calls:
                _record_local_call(local_callers, symbol_locations, call, symbol.name, local_names, record_index)
                _record_project_call(project_callers, symbol_locations, call, project_caller)
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
) -> None:
    locations = locations_by_name.get(call, [])
    if len(locations) == 1:
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


def _complexity(lines: list[str]) -> int:
    score = 1
    for line in lines:
        score += len(COMPLEXITY_RE.findall(strip_comments(line)))
    return score


def _calls(lines: list[str], own_name: str) -> list[str]:
    calls: list[str] = []
    for line in lines:
        for name in CALL_RE.findall(strip_comments(line)):
            if name == own_name or name in CONTROL_NAMES:
                continue
            if name not in calls:
                calls.append(name)
    return calls
