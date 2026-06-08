import os
import time
from pathlib import Path

from auto_index_mcp.indexing.build_lock import BuildLock


def test_acquire_is_exclusive_until_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    first = BuildLock(lock_path)
    second = BuildLock(lock_path)

    assert first.acquire(1.0) is True
    assert second.acquire(0.1) is False

    first.release()
    assert second.acquire(0.5) is True
    second.release()


def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    lock_path.write_text("99999\n0", encoding="utf-8")
    old = time.time() - 600
    os.utime(lock_path, (old, old))

    reclaimed = BuildLock(lock_path, stale_seconds=1.0)
    assert reclaimed.acquire(0.5) is True
    reclaimed.release()


def test_held_lock_heartbeat_prevents_false_stale_reclaim(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    stale_seconds = 0.4
    first = BuildLock(lock_path, stale_seconds=stale_seconds, poll_seconds=0.02)
    second = BuildLock(lock_path, stale_seconds=stale_seconds, poll_seconds=0.02)

    assert first.acquire(0.5) is True
    start = time.time()
    initial_mtime = lock_path.stat().st_mtime_ns
    assert _wait_until(lambda: time.time() - start > stale_seconds and lock_path.stat().st_mtime_ns != initial_mtime)
    assert second.acquire(0.1) is False

    first.release()
    assert second.acquire(0.5) is True
    second.release()


def test_release_does_not_delete_replaced_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    first = BuildLock(lock_path)

    assert first.acquire(0.5) is True
    lock_path.unlink()
    lock_path.write_text("replacement\n0", encoding="ascii")

    first.release()

    assert lock_path.exists()
    assert lock_path.read_text(encoding="ascii").startswith("replacement")


def test_release_without_hold_is_noop(tmp_path: Path) -> None:
    lock = BuildLock(tmp_path / "index.build.lock")
    # Never acquired - releasing must not raise or delete an unrelated file.
    lock.release()
    assert not (tmp_path / "index.build.lock").exists()


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False
