"""Leased compaction reuses the same ownership contract as cleanup."""
from pathlib import Path

from ..cache.inventory import is_link
from ..runtime.maintenance import MaintenanceLease
from ..storage.compaction import compact_database
from ..storage.embeddings import EmbeddingStore
from ..storage.namespace import namespaces, retain_namespaces
from .markers import is_safe_to_delete


def compact_entry(entry, keep_model=None, max_models=None, max_bytes=None) -> dict:
    directory = Path(entry["index_dir"])
    with MaintenanceLease(directory, "compact"):
        if not is_safe_to_delete(directory, entry["root"]):
            raise ValueError(f"cache ownership could not be verified: {directory}")
        _check_database_paths(directory)
        retained = dict(kept=[], removed=[])
        vectors = directory / "embeddings.db"
        if vectors.is_file():
            store = EmbeddingStore(vectors)
            with store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                models = namespaces(conn)
                if keep_model is not None and not any(row["model"] == keep_model for row in models):
                    raise ValueError(f"model namespace does not exist: {keep_model}")
                retained = retain_namespaces(conn, keep_model, max_models, max_bytes)
        elif keep_model is not None:
            raise ValueError(f"model namespace does not exist: {keep_model}")
        databases = dict((name, compact_database(directory / name)) for name in ("index.db", "embeddings.db"))
        return dict(databases=databases, retention=retained,
                    reclaimed=sum(row["reclaimed"] for row in databases.values()))


def _check_database_paths(directory) -> None:
    for name in ("index.db", "embeddings.db"):
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = directory / (name + suffix)
            if path.exists() and is_link(path):
                raise OSError(f"linked database cannot be compacted: {path}")
