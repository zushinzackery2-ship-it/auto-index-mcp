"""OS-process ownership and abrupt-exit regressions."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.runtime.leases import BuildLock
from auto_index_mcp.mcp_api.lifecycle import start_or_defer_auto_watch


def test_process_lock_is_released_on_termination(tmp_path):
    path = tmp_path / "index.build.lock"
    code = "\n".join([
        "import sys, time, os",
        "from pathlib import Path",
        "from auto_index_mcp.runtime.leases import BuildLock",
        "lock = BuildLock(Path(sys.argv[1]))",
        "assert lock.try_acquire()",
        "print(os.getpid(), flush=True)",
        "time.sleep(30)",
    ])
    proc = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
    contender = BuildLock(path)
    try:
        worker_pid = int(proc.stdout.readline().strip())
        assert not contender.try_acquire()
        os.kill(worker_pid, signal.SIGTERM)
        proc.wait(5)
        assert contender.acquire(1.0)
    finally:
        contender.release()
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        proc.stdout.close()


def test_follower_enable_finishes_without_repeated_enable(tmp_path, write_file):
    write_file(tmp_path / "a.py", "def alpha():\n    return 1\n")
    holder = BuildLock(tmp_path / ".auto-index-mcp" / "index.build.lock")
    assert holder.try_acquire()
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        result = service.enable_reusing_index(str(tmp_path))
        start_or_defer_auto_watch(service, result)
        assert service.background.is_running()
        holder.release()
        assert service.background.wait(5)
        assert service.store.symbol_count() == 1
        deadline = time.monotonic() + 3
        while service.watcher is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert service.watcher is not None
    finally:
        holder.release()
        service.disable()


def test_default_attach_does_not_create_semantic_worker(tmp_path, write_file, monkeypatch):
    monkeypatch.delenv("AUTO_INDEX_SEMANTIC_MODE", raising=False)
    write_file(tmp_path / "a.py", "def alpha():\n    return 1\n")
    service = AutoIndexService()
    try:
        result = service.enable_reusing_index(str(tmp_path), wait_seconds=5)
        assert result["status"] == "indexed"
        assert service.embedding_background is None
        assert service.embedding_indexer is None
        assert service.status()["embedding"]["state"] == "on-demand"
    finally:
        service.disable()


def test_many_symbols_are_all_searchable(tmp_path, write_file):
    write_file(tmp_path / "many.py", "\n".join(f"def function_{i}():\n    return {i}\n" for i in range(250)))
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))
        assert service.store.symbol_count() == 250
        assert service.symbol_search("function_249")["items"][0]["name"] == "function_249"
    finally:
        service.disable()


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirector lifecycle")
def test_windows_launcher_exit_releases_worker_resources(tmp_path):
    path = tmp_path / "index.build.lock"
    code = "\n".join([
        "import sys, time, os",
        "from pathlib import Path",
        "from auto_index_mcp.runtime.parent_lifetime import watch_parent",
        "from auto_index_mcp.runtime.leases import BuildLock",
        "watch_parent()",
        "lock = BuildLock(Path(sys.argv[1]))",
        "assert lock.try_acquire()",
        "print(os.getpid(), flush=True)",
        "time.sleep(30)",
    ])
    proc = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE, text=True)
    contender = BuildLock(path)
    worker_pid = None
    try:
        worker_pid = int(proc.stdout.readline().strip())
        assert not contender.try_acquire()
        proc.kill()
        proc.wait(5)
        assert contender.acquire(3.0)
    finally:
        contender.release()
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        if worker_pid is not None:
            try:
                os.kill(worker_pid, signal.SIGTERM)
            except OSError:
                pass
        proc.stdout.close()
