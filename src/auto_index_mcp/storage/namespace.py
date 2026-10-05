"""Model namespaces have observable sizes and explicit retention limits."""
import json
import os

NAMESPACE_PREFIXES = ("complete:", "checkpoint:", "pending:", "generation:", "model-used:")


def namespaces(conn) -> list[dict]:
    used = dict((row[0].removeprefix("model-used:"), float(json.loads(row[1])))
                for row in conn.execute("SELECT key,value FROM metadata WHERE key LIKE 'model-used:%'"))
    rows = [dict(model=row[0], vector_count=row[1], vector_bytes=row[2], last_used=used.pop(row[0], 0.0))
            for row in conn.execute("SELECT model_name,COUNT(*),SUM(LENGTH(vector)) FROM symbol_embeddings GROUP BY model_name")]
    rows.extend(dict(model=key, vector_count=0, vector_bytes=0, last_used=value) for key, value in used.items())
    return sorted(rows, key=lambda row: (row["last_used"], row["model"]), reverse=True)


def retention_limits() -> tuple[int, int]:
    count = int(os.environ.get("AUTO_INDEX_MAX_MODEL_NAMESPACES", "3"))
    size = int(os.environ.get("AUTO_INDEX_MAX_VECTOR_BYTES", str(256 * 1024 * 1024)))
    if count < 1 or size < 1:
        raise ValueError("semantic namespace retention limits must be positive")
    return count, size


def retain_namespaces(conn, current=None, max_models=None, max_bytes=None) -> dict:
    rows = namespaces(conn)
    if current:
        rows.sort(key=lambda row: row["model"] != current)
    kept = []
    removed = []
    size = 0
    for row in rows:
        mandatory = row["model"] == current
        fits = ((max_models is None or len(kept) < max_models)
                and (max_bytes is None or size + row["vector_bytes"] <= max_bytes))
        if mandatory or fits:
            kept.append(row["model"])
            size += row["vector_bytes"]
        else:
            _remove_namespace(conn, row["model"])
            removed.append(row["model"])
    return dict(kept=kept, removed=removed, vector_bytes=size,
                budget_exceeded=max_bytes is not None and size > max_bytes)


def _remove_namespace(conn, model) -> None:
    conn.execute("DELETE FROM symbol_embeddings WHERE model_name=?", (model,))
    for prefix in NAMESPACE_PREFIXES:
        conn.execute("DELETE FROM metadata WHERE key=?", (prefix + model,))


def clear_namespaces(conn) -> None:
    conn.execute("DELETE FROM symbol_embeddings")
    for prefix in NAMESPACE_PREFIXES:
        conn.execute("DELETE FROM metadata WHERE key LIKE ?", (prefix + "%",))
