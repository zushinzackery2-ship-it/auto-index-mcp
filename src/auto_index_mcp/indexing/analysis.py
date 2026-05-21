from __future__ import annotations

import re
from dataclasses import replace

from ..core.models import FileRecord, SymbolRecord

CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
CONTROL_NAMES = {"if", "for", "while", "switch", "return", "raise", "catch", "with"}
COMPLEXITY_RE = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|and|or|\?|&&|\|\|)\b")


def enrich_symbols(lines: list[str], symbols: list[SymbolRecord]) -> list[SymbolRecord]:
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
    return _resolve_local_callers(enriched)


def resolve_project_callers(records: list[FileRecord]) -> list[FileRecord]:
    symbol_locations: dict[str, list[tuple[int, int]]] = {}
    for record_index, record in enumerate(records):
        for symbol_index, symbol in enumerate(record.symbols):
            symbol_locations.setdefault(symbol.name, []).append((record_index, symbol_index))

    callers: dict[tuple[int, int], list[str]] = {}
    for record_index, record in enumerate(records):
        for symbol in record.symbols:
            caller_name = f"{record.path}::{symbol.name}"
            for call in symbol.calls:
                locations = symbol_locations.get(call, [])
                if len(locations) != 1:
                    continue
                callers.setdefault(locations[0], []).append(caller_name)

    updated_records = []
    for record_index, record in enumerate(records):
        symbols = []
        for symbol_index, symbol in enumerate(record.symbols):
            merged = list(dict.fromkeys(symbol.called_by + callers.get((record_index, symbol_index), [])))
            symbols.append(replace(symbol, called_by=merged))
        updated_records.append(replace(record, symbols=symbols))
    return updated_records


def _complexity(lines: list[str]) -> int:
    score = 1
    for line in lines:
        score += len(COMPLEXITY_RE.findall(_strip_comments(line)))
    return score


def _calls(lines: list[str], own_name: str) -> list[str]:
    calls: list[str] = []
    for line in lines:
        for name in CALL_RE.findall(_strip_comments(line)):
            if name == own_name or name in CONTROL_NAMES:
                continue
            if name not in calls:
                calls.append(name)
    return calls


def _resolve_local_callers(symbols: list[SymbolRecord]) -> list[SymbolRecord]:
    names = {symbol.name for symbol in symbols}
    callers: dict[str, list[str]] = {name: [] for name in names}
    for symbol in symbols:
        for call in symbol.calls:
            if call in callers and symbol.name not in callers[call]:
                callers[call].append(symbol.name)
    return [
        SymbolRecord(
            name=symbol.name,
            kind=symbol.kind,
            line=symbol.line,
            end_line=symbol.end_line,
            signature=symbol.signature,
            complexity=symbol.complexity,
            calls=symbol.calls,
            called_by=callers[symbol.name],
        )
        for symbol in symbols
    ]


def _strip_comments(line: str) -> str:
    return line.split("#", 1)[0].split("//", 1)[0]
