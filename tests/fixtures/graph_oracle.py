"""Independent full-graph oracle for incremental differential tests."""
from dataclasses import replace
from auto_index_mcp.domain.models import FileRecord


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
        local_locations = dict()
        for symbol_index, symbol in enumerate(record.symbols):
            local_locations.setdefault(symbol.name, []).append((record_index, symbol_index))
        for name in record.module_refs:
            _record_local_call(local_callers, local_locations, name, "<module>")
            _record_project_call(project_callers, symbol_locations, name, f"{record.path}::<module>", record_index)
        for symbol in record.symbols:
            project_caller = f"{record.path}::{symbol.name}"
            # Value references count as usage edges exactly like calls: a
            # function handed to sort(key=...) or stored in a table has a user.
            for call in dict.fromkeys(symbol.calls + symbol.refs):
                _record_local_call(local_callers, local_locations, call, symbol.name)
                _record_project_call(project_callers, symbol_locations, call, project_caller, record_index)
    return local_callers, project_callers


def _record_local_call(
    callers: dict[tuple[int, int], list[str]],
    locations_by_name: dict[str, list[tuple[int, int]]],
    call: str,
    caller_name: str,
) -> None:
    if call == caller_name:
        return
    for location in locations_by_name.get(call, []):
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
