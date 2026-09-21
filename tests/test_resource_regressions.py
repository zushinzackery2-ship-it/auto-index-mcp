"""Request responsiveness and shared-work regressions at production seams."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.embedding_store import EmbeddingStore
from auto_index_mcp.indexing.build_lock import BuildLock
from auto_index_mcp.mcp_api.lifecycle import register_lifecycle_tools


def test_empty_abandoned_lock_is_recoverable(tmp_path: Path) -> None:
    path = tmp_path / "index.build.lock"
    path.touch()
    old = time.time() - 600
    os.utime(path, (old, old))
    lock = BuildLock(path, stale_seconds=1)
    try:
        assert lock.try_acquire(), "empty lock left by interrupted creation blocks every builder"
    finally:
        lock.release()


def test_live_owner_is_never_evicted_by_clock_age(tmp_path: Path) -> None:
    holder = BuildLock(tmp_path / "index.build.lock", stale_seconds=600)
    contender = BuildLock(holder.path, stale_seconds=1)
    assert holder.try_acquire()
    try:
        old = time.time() - 1000
        os.utime(holder.path, (old, old))
        assert not contender.try_acquire(), "a paused live builder must retain exclusivity"
    finally:
        contender.release()
        holder.release()


def test_initializing_existing_vectors_does_not_wait_for_writer(tmp_path: Path) -> None:
    store = EmbeddingStore(tmp_path / "embeddings.db")
    store.initialize()
    started = threading.Event()
    release = threading.Event()

    def writer() -> None:
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            started.set()
            release.wait(1.5)

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(3)
    try:
        before = time.monotonic()
        EmbeddingStore(store.db_path).initialize()
        elapsed = time.monotonic() - before
        assert elapsed < 0.5, f"new agent enable waited {elapsed:.3f}s for vector writer"
    finally:
        release.set()
        thread.join(3)


def test_enable_tool_keeps_event_loop_responsive(tmp_path: Path, monkeypatch) -> None:
    service = AutoIndexService()
    mcp = FastMCP("latency-test")
    register_lifecycle_tools(mcp, service)
    finished = threading.Event()

    def slow_enable(*args, **kwargs):
        time.sleep(0.3)
        finished.set()
        return dict(enabled=True)

    monkeypatch.setattr(service, "enable_reusing_index", slow_enable)

    async def exercise():
        task = asyncio.create_task(mcp._tool_manager.get_tool("auto_index_enable").fn(
            root_path=str(tmp_path), auto_watch=False,
        ))
        await asyncio.sleep(0.03)
        was_blocked = finished.is_set()
        await task
        assert not was_blocked, "enable ran blocking work on the MCP event loop"

    asyncio.run(exercise())


def test_watchers_share_one_observer_and_take_over(tmp_path: Path, write_file) -> None:
    write_file(tmp_path / "code.py", "def initial():\n    return 1\n")
    first, second = AutoIndexService(), AutoIndexService()
    for service in (first, second):
        service.semantic_enabled = False
    try:
        first.enable(str(tmp_path))
        second.enable(str(tmp_path), rebuild=False)
        first.start_watcher(wait_ready=True)
        second.start_watcher(wait_ready=True)
        observers = [service.watcher for service in (first, second) if service.watcher is not None]
        assert sum(watcher._observer is not None for watcher in observers) == 1
        first.stop_watcher()
        write_file(tmp_path / "code.py", "def after_takeover():\n    return 2\n")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if second.store.query_symbols("after_takeover", "", 10, 0):
                break
            time.sleep(0.05)
        assert second.store.query_symbols("after_takeover", "", 10, 0)
    finally:
        first.disable()
        second.disable()
