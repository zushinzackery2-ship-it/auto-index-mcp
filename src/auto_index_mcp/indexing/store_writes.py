from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from ..core.models import FileRecord


def insert_many(conn: sqlite3.Connection, records: list[FileRecord]) -> None:
    if not records:
        return
    file_rows = []
    symbol_rows = []
    nesting_rows = []
    fts_rows = []
    for record in records:
        file_rows.append(
            (
                record.path,
                record.name,
                record.parent,
                record.extension,
                record.language,
                record.size,
                record.mtime_ns,
                record.sha1,
                record.line_count,
                json.dumps(record.imports),
                json.dumps([asdict(symbol) for symbol in record.symbols]),
                json.dumps(record.quality_findings),
                1 if record.active_source else 0,
                record.snippet,
            )
        )
        fts_rows.append(
            (
                record.path,
                record.name,
                record.parent,
                record.language,
                " ".join(symbol.name for symbol in record.symbols),
                " ".join(record.imports),
                record.snippet,
            )
        )
        for symbol in record.symbols:
            symbol_rows.append(
                (
                    record.path,
                    symbol.name,
                    symbol.kind,
                    symbol.line,
                    symbol.end_line,
                    symbol.signature,
                    symbol.complexity,
                    json.dumps(symbol.calls),
                    json.dumps(symbol.called_by),
                )
            )
            nesting_rows.append(
                (
                    record.path,
                    symbol.name,
                    symbol.kind,
                    symbol.line,
                    symbol.end_line,
                    symbol.parent_name,
                    symbol.parent_kind,
                    symbol.depth,
                    symbol.nesting_path or symbol.name,
                    symbol.children_count,
                    symbol.max_child_depth,
                    symbol.max_block_depth,
                )
            )
    conn.executemany(
        "INSERT INTO files(path, name, parent, extension, language, size, mtime_ns, sha1, line_count, imports, symbols, quality_findings, active_source, snippet) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        file_rows,
    )
    conn.executemany(
        "INSERT INTO symbols(file_path, name, kind, line, end_line, signature, complexity, calls, called_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        symbol_rows,
    )
    conn.executemany(
        "INSERT INTO symbol_nesting("
        "file_path, symbol_name, symbol_kind, line, end_line, parent_name, parent_kind, "
        "depth, nesting_path, children_count, max_child_depth, max_block_depth"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        nesting_rows,
    )
    conn.executemany(
        "INSERT INTO file_fts VALUES (?, ?, ?, ?, ?, ?, ?)",
        fts_rows,
    )


def insert_child_indexes(conn: sqlite3.Connection, children: list[dict[str, Any]]) -> None:
    if not children:
        return
    conn.executemany(
        "INSERT INTO child_indexes VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                child["path"],
                child["root"],
                child["db_path"],
                child["file_count"],
                child["updated_at"],
                child["version"],
            )
            for child in children
        ],
    )


def delete_file_rows(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM files WHERE path=?", (path,))
    conn.execute("DELETE FROM symbols WHERE file_path=?", (path,))
    conn.execute("DELETE FROM symbol_nesting WHERE file_path=?", (path,))
    conn.execute("DELETE FROM file_fts WHERE path=?", (path,))
