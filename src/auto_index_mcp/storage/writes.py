from __future__ import annotations

import json

from .graph import insert_edges

FILE_FIELDS = (
    "path", "name", "parent", "extension", "language", "size", "mtime_ns", "sha1",
    "line_count", "imports", "quality_findings", "active_source", "snippet", "symbol_count",
    "module_refs", "analysis_kind",
)
SYMBOL_FIELDS = (
    "name", "kind", "line", "end_line", "signature", "complexity", "calls", "refs",
    "parent_name", "parent_kind", "depth", "nesting_path", "children_count", "max_child_depth", "max_block_depth",
)


def insert_many(conn, records) -> None:
    for start in range(0, len(records), 64):
        _insert_batch(conn, records[start:start + 64])


def _insert_batch(conn, records) -> None:
    files, symbols = [], []
    for record in records:
        values = dict(vars(record), symbol_count=len(record.symbols))
        for key in ("imports", "quality_findings", "module_refs"):
            values[key] = json.dumps(values[key])
        files.append(tuple(values[name] for name in FILE_FIELDS))
        for symbol in record.symbols:
            data = vars(symbol)
            symbols.append((record.path,) + tuple(
                json.dumps(data[name]) if name in ("calls", "refs") else data[name] for name in SYMBOL_FIELDS
            ))
    conn.executemany(f"INSERT INTO files({','.join(FILE_FIELDS)}) VALUES ({','.join('?' for _ in FILE_FIELDS)})", files)
    conn.executemany(
        f"INSERT INTO symbols(file_path,{','.join(SYMBOL_FIELDS)}) VALUES ({','.join('?' for _ in range(len(SYMBOL_FIELDS) + 1))})",
        symbols,
    )
    insert_edges(conn, records)


def insert_child_indexes(conn, children) -> None:
    conn.executemany("INSERT INTO child_indexes VALUES (?,?,?,?,?,?)", [
        tuple(child[name] for name in ("path", "root", "db_path", "file_count", "updated_at", "version"))
        for child in children
    ])


def delete_file_rows(conn, path: str) -> None:
    conn.execute("DELETE FROM files WHERE path=?", (path,))
