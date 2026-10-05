"""A bounded publication journal drives restart-safe semantic deltas."""
from .generation import metadata, revision, set_value

RETAIN_GENERATIONS = 512


def record_changes(conn, generation: int, paths, full: bool) -> None:
    if full:
        conn.execute("DELETE FROM source_changes")
        set_value(conn, "delta_floor", generation)
    else:
        conn.executemany("INSERT OR IGNORE INTO source_changes VALUES (?,?)",
                         [(generation, path) for path in dict.fromkeys(paths)])
        floor = max(0, generation - RETAIN_GENERATIONS)
        conn.execute("DELETE FROM source_changes WHERE generation<=?", (floor,))
        current = metadata(conn)
        set_value(conn, "delta_floor", max(floor, int(current.get("delta_floor", 0))))


def changes_since(store, previous: str | None) -> dict:
    with store.read_connect() as conn:
        current = metadata(conn)
        target = revision(current)
        reason = _full_reason(current, previous)
        if reason:
            return dict(mode="full", revision=target, paths=[], reason=reason)
        generation = int(previous.rsplit(":", 1)[1])
        paths = [row[0] for row in conn.execute("SELECT DISTINCT path FROM source_changes WHERE generation>? ORDER BY path",
                                               (generation,))]
        return dict(mode="incremental", revision=target, paths=paths, reason="source-delta")


def _full_reason(current, previous):
    if not previous:
        return "namespace-not-complete"
    try:
        epoch, generation = previous.rsplit(":", 1)
        generation = int(generation)
    except (ValueError, AttributeError):
        return "unknown-source-revision"
    if epoch != current.get("index_epoch"):
        return "source-epoch-changed"
    if generation < int(current.get("delta_floor", 0)):
        return "full-publication-or-journal-expired"
    return None
