"""Qualified unresolved edges are projected with explicit evidence strength."""
from __future__ import annotations

from ..domain.reference_graph import project_references


def insert_edges(conn, records) -> None:
    for record in records:
        rows = _edge_rows(record.path, "<module>", 0, "", record.module_refs, record.module_reference_evidence)
        for symbol in record.symbols:
            rows.extend(_edge_rows(record.path, symbol.name, symbol.line, symbol.nesting_path,
                                   symbol.calls + symbol.refs, symbol.reference_evidence))
        conn.executemany("INSERT OR IGNORE INTO symbol_references VALUES (?,?,?,?,?,?,?,?)", rows)


def _edge_rows(path, name, line, scope, targets, evidence):
    qualified_targets = {edge["target"] for edge in evidence}
    edges = list(evidence) + [dict(target=target, qualifier="", evidence="name-only", usage="reference")
                             for target in dict.fromkeys(targets) if target not in qualified_targets]
    return [(path, name, line, edge["target"], edge["qualifier"], edge["evidence"], scope, edge["usage"])
            for edge in edges]


def attach_callers(conn, symbols: list[dict]) -> list[dict]:
    names = list(dict.fromkeys(symbol["name"] for symbol in symbols))
    definitions, edges = reference_rows(conn, names)
    return project_references(symbols, definitions, edges)


def reference_rows(conn, names):
    definitions, edges = [], []
    for start in range(0, len(names), 400):
        batch = names[start:start + 400]
        marks = ",".join("?" for _ in batch)
        definitions.extend(dict(row) for row in conn.execute(
            f"SELECT file_path,name,line,nesting_path,kind FROM symbols WHERE name IN ({marks})", batch))
        edges.extend(dict(row) for row in conn.execute(
            f"SELECT * FROM symbol_references WHERE target IN ({marks})", batch))
    return definitions, edges
