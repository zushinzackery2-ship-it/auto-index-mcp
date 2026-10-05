"""Bounded symbol streams carry the source revision from their SQL snapshot."""
from .rows import symbol_row_to_dict

SOURCE_COLUMNS = "f.sha1 AS source_sha1,f.size AS source_size,f.mtime_ns AS source_mtime_ns"


class SymbolReader:
    def __init__(self, store) -> None:
        self.store = store

    def iterate(self):
        with self.store.read_connect() as conn:
            cursor = conn.execute("SELECT s.*, " + SOURCE_COLUMNS +
                                  " FROM symbols s JOIN files f ON f.path=s.file_path ORDER BY s.file_path,s.line,s.id")
            while rows := cursor.fetchmany(256):
                for row in rows:
                    item = symbol_row_to_dict(row)
                    item.pop("id", None)
                    yield item

    def for_files(self, paths: list[str]) -> list[dict]:
        rows = []
        with self.store.read_connect() as conn:
            for start in range(0, len(paths), 400):
                batch = paths[start:start + 400]
                marks = ",".join("?" for _ in batch)
                rows.extend(symbol_row_to_dict(row) for row in conn.execute(
                    "SELECT s.*, " + SOURCE_COLUMNS +
                    f" FROM symbols s JOIN files f ON f.path=s.file_path WHERE s.file_path IN ({marks}) ORDER BY s.file_path,s.line,s.id",
                    batch,
                ))
        return rows
