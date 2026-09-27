"""Persisted unresolved edges, resolved by indexed name and file on demand."""
from __future__ import annotations

from collections import defaultdict


def insert_edges(conn, records) -> None:
    for record in records:
        rows = [(record.path, "<module>", 0, target) for target in record.module_refs]
        for symbol in record.symbols:
            rows.extend((record.path, symbol.name, symbol.line, name)
                        for name in dict.fromkeys(symbol.calls + symbol.refs))
        conn.executemany("INSERT OR IGNORE INTO symbol_references VALUES (?,?,?,?)", rows)


def attach_callers(conn, symbols: list[dict]) -> list[dict]:
    names = list(dict.fromkeys(symbol["name"] for symbol in symbols))
    locations = dict()
    edges = defaultdict(list)
    for start in range(0, len(names), 400):
        batch = names[start:start + 400]
        marks = ",".join("?" for _ in batch)
        for row in conn.execute(f"SELECT name,COUNT(*) AS n FROM symbols WHERE name IN ({marks}) GROUP BY name", batch):
            locations[row["name"]] = row["n"]
        for row in conn.execute(
            f"SELECT * FROM symbol_references WHERE target IN ({marks}) "
            "ORDER BY source_file COLLATE NOCASE, source_line, source_name", batch,
        ):
            edges[row["target"]].append((row["source_file"], row["source_name"]))
    for symbol in symbols:
        local, external = [], []
        name, path = symbol["name"], symbol["file_path"]
        for source_file, source_name in edges[name]:
            if source_file == path:
                if source_name != name:
                    local.append(source_name)
            elif locations.get(name) == 1:
                external.append(f"{source_file}::{source_name}")
        symbol["called_by"] = list(dict.fromkeys(local + external))
    return symbols
