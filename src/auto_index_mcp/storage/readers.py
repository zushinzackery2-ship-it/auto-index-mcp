from __future__ import annotations

from collections import defaultdict

from .rows import file_row_to_dict, symbol_row_to_dict
from .graph import attach_callers


def read_files(conn, rows) -> list[dict]:
    files = [file_row_to_dict(row) for row in rows]
    by_path = defaultdict(list)
    paths = [item["path"] for item in files]
    symbols = []
    for start in range(0, len(paths), 400):
        batch = paths[start:start + 400]
        marks = ",".join("?" for _ in batch)
        symbols.extend(symbol_row_to_dict(row) for row in conn.execute(
            f"SELECT * FROM symbols WHERE file_path IN ({marks}) ORDER BY file_path,line,id", batch,
        ))
    for symbol in attach_callers(conn, symbols):
        path = symbol.pop("file_path")
        symbol.pop("id", None)
        by_path[path].append(symbol)
    for item in files:
        item["symbols"] = by_path[item["path"]]
    return files
