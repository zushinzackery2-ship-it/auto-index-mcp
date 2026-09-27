from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp status",
        description="Print a read-only summary of an existing index.",
    )
    parser.add_argument("path", nargs="?", default=".", help="project root (default: current directory)")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    index_dir = root / ".auto-index-mcp"
    db_path = index_dir / "index.db"
    if not db_path.exists():
        print(f"no index at {db_path}")
        print(f"build one with: auto-index-mcp build {root}")
        return 1

    from ..runtime.timefmt import iso_time
    from ..workspace.discovery import read_index_metadata

    meta = read_index_metadata(db_path)
    if not meta:
        print(f"index at {db_path} is unreadable or empty")
        return 1
    file_count = int(meta.get("file_count") or 0)
    total_count = int(meta.get("total_file_count") or file_count)
    files_note = f"{file_count}"
    if total_count > file_count:
        files_note += f" (+{total_count - file_count} in child indexes)"
    print(f"root:        {meta.get('root', root)}")
    print(f"index:       {db_path}")
    print(f"files:       {files_note}")
    print(f"updated:     {iso_time(meta.get('updated_at'))}")
    print(f"version:     {meta.get('version')}")

    vectors, models = _read_vector_summary(index_dir / "embeddings.db")
    if vectors is None:
        print("vectors:     none (run build without --no-semantic)")
    else:
        model_note = f" (model {', '.join(models)})" if models else ""
        print(f"vectors:     {vectors}{model_note}")
    return 0


def _read_vector_summary(db_path: Path) -> tuple[int | None, list[str]]:
    if not db_path.exists():
        return None, []
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbol_embeddings").fetchone()[0]
            models = [
                str(row[0]).split("#", 1)[0]
                for row in conn.execute(
                    "SELECT DISTINCT model_name FROM symbol_embeddings"
                ).fetchall()
            ]
        return int(count), sorted(set(models))
    except sqlite3.DatabaseError:
        return None, []
