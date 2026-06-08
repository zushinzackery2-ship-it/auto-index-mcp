from pathlib import Path

from auto_index_mcp.indexing.snapshot import WatchSnapshot
from auto_index_mcp.indexing.watcher import FileEventWatcher


def test_watcher_requeues_paths_after_update_error(tmp_path: Path) -> None:
    previous = WatchSnapshot(files={}, child_indexes={})
    calls = {"n": 0}
    changed = tmp_path / "main.py"

    def take_snapshot() -> WatchSnapshot:
        return previous

    def update_snapshot(snapshot: WatchSnapshot, paths: set[Path]) -> WatchSnapshot:
        _ = snapshot, paths
        calls["n"] += 1
        raise RuntimeError("transient")

    watcher = FileEventWatcher(tmp_path, take_snapshot, update_snapshot, lambda old, new: {}, 0.1, previous)
    watcher._snapshot = previous
    with watcher._changes_lock:
        watcher._changed_paths.add(changed)

    watcher._apply_snapshot_change()

    assert watcher.last_error == "transient"
    assert watcher._changed.is_set()
    with watcher._changes_lock:
        assert watcher._needs_full_snapshot is True
        assert changed in watcher._changed_paths
    assert calls["n"] == 1
