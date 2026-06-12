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
    symbol_locations: dict[str, list[tuple[int, int]]] = {}
    for record_index, record in enumerate(records):
        for symbol_index, symbol in enumerate(record.symbols):
            symbol_locations.setdefault(symbol.name, []).append((record_index, symbol_index))

    local_callers: dict[tuple[int, int], list[str]] = {}
    project_callers: dict[tuple[int, int], list[str]] = {}
    for record_index, record in enumerate(records):
        local_names = {symbol.name for symbol in record.symbols}
        for symbol in record.symbols:
            project_caller = f"{record.path}::{symbol.name}"
            for call in symbol.calls:
                if call in local_names and call != symbol.name:
                    for location in symbol_locations[call]:
                        if location[0] == record_index:
                            local_callers.setdefault(location, []).append(symbol.name)
                locations = symbol_locations.get(call, [])
                if len(locations) == 1:
                    project_callers.setdefault(locations[0], []).append(project_caller)

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
