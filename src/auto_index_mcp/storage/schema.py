from __future__ import annotations

import json

SCHEMA_VERSION = 5
NESTING_COLUMNS = dict(
    parent_name="TEXT NOT NULL DEFAULT ''", parent_kind="TEXT NOT NULL DEFAULT ''",
    depth="INTEGER NOT NULL DEFAULT 0", nesting_path="TEXT NOT NULL DEFAULT ''",
    children_count="INTEGER NOT NULL DEFAULT 0", max_child_depth="INTEGER NOT NULL DEFAULT 0",
    max_block_depth="INTEGER NOT NULL DEFAULT 0",
)


def initialize_schema(conn, set_metadata=None) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("""CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT NOT NULL,
        extension TEXT NOT NULL, language TEXT NOT NULL, size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL, sha1 TEXT NOT NULL, line_count INTEGER NOT NULL,
        imports TEXT NOT NULL, snippet TEXT NOT NULL)""")
    ensure_columns(conn, "files", dict(
        quality_findings="TEXT NOT NULL DEFAULT '[]'", active_source="INTEGER NOT NULL DEFAULT 1",
        symbol_count="INTEGER NOT NULL DEFAULT 0", module_refs="TEXT NOT NULL DEFAULT '[]'",
        analysis_kind="TEXT NOT NULL DEFAULT 'heuristic'",
    ))
    conn.execute("""CREATE TABLE IF NOT EXISTS symbols (
        id INTEGER PRIMARY KEY, file_path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
        line INTEGER NOT NULL, end_line INTEGER NOT NULL, signature TEXT NOT NULL,
        complexity INTEGER NOT NULL DEFAULT 1, calls TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY(file_path) REFERENCES files(path) ON DELETE CASCADE)""")
    ensure_columns(conn, "symbols", dict(NESTING_COLUMNS, refs="TEXT NOT NULL DEFAULT '[]'"))
    _migrate_symbol_payload(conn)
    conn.execute("DROP TABLE IF EXISTS symbol_nesting")
    conn.execute("DROP TABLE IF EXISTS symbol_embeddings")
    for name, fields in (("name", "name"), ("file", "file_path"), ("file_line", "file_path,line,id")):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_symbols_{name} ON symbols({fields})")
    conn.execute("""CREATE TABLE IF NOT EXISTS child_indexes (
        path TEXT PRIMARY KEY, root TEXT NOT NULL, db_path TEXT NOT NULL,
        file_count INTEGER NOT NULL, updated_at REAL, version INTEGER NOT NULL)""")
    conn.execute("DROP TABLE IF EXISTS file_fts")
    conn.execute("""CREATE TABLE IF NOT EXISTS symbol_references (
        source_file TEXT NOT NULL, source_name TEXT NOT NULL, source_line INTEGER NOT NULL,
        target TEXT NOT NULL, PRIMARY KEY(source_file,source_name,source_line,target),
        FOREIGN KEY(source_file) REFERENCES files(path) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_references_target ON symbol_references(target,source_file)")
    conn.execute("CREATE TABLE IF NOT EXISTS active_sources (path TEXT PRIMARY KEY)")
    conn.execute("""INSERT OR IGNORE INTO symbol_references
        SELECT s.file_path,s.name,s.line,j.value FROM symbols s, json_each(s.calls) j""")
    conn.execute("""INSERT OR IGNORE INTO symbol_references
        SELECT s.file_path,s.name,s.line,j.value FROM symbols s, json_each(s.refs) j""")


def ensure_columns(conn, table, additions) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_symbol_payload(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(files)")}
    if "symbols" in columns:
        assignments = ",".join(f"{name}=?" for name in ("refs", *NESTING_COLUMNS))
        for row in conn.execute("SELECT path,symbols FROM files"):
            symbols = json.loads(row["symbols"])
            for symbol in symbols:
                values = [json.dumps(symbol.get("refs", []))]
                values.extend(symbol.get(name, "" if "TEXT" in definition else 0)
                              for name, definition in NESTING_COLUMNS.items())
                conn.execute(f"UPDATE symbols SET {assignments} WHERE file_path=? AND name=? AND line=?",
                             values + [row["path"], symbol["name"], symbol["line"]])
        conn.execute("UPDATE files SET symbol_count=(SELECT COUNT(*) FROM symbols WHERE file_path=files.path)")
        conn.execute("ALTER TABLE files DROP COLUMN symbols")
    symbol_columns = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)")}
    if "called_by" in symbol_columns:
        conn.execute("ALTER TABLE symbols DROP COLUMN called_by")
