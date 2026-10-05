"""Qualified unresolved edges retain enough evidence to express ambiguity."""


def initialize_references(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(symbol_references)")}
    migrate = bool(columns and "qualifier" not in columns)
    if migrate:
        conn.execute("ALTER TABLE symbol_references RENAME TO legacy_references")
        conn.execute("DROP INDEX IF EXISTS idx_references_target")
    conn.execute("""CREATE TABLE IF NOT EXISTS symbol_references (
        source_file TEXT NOT NULL, source_name TEXT NOT NULL, source_line INTEGER NOT NULL,
        target TEXT NOT NULL, qualifier TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT 'name-only',
        source_scope TEXT NOT NULL DEFAULT '', usage TEXT NOT NULL DEFAULT 'reference',
        PRIMARY KEY(source_file,source_name,source_line,target,qualifier,usage),
        FOREIGN KEY(source_file) REFERENCES files(path) ON DELETE CASCADE)""")
    if migrate:
        conn.execute("""INSERT OR IGNORE INTO symbol_references(source_file,source_name,source_line,target)
            SELECT source_file,source_name,source_line,target FROM legacy_references""")
        conn.execute("DROP TABLE legacy_references")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_references_target ON symbol_references(target,source_file)")
