"""Kernel locks and the durable manifest recover from real process termination."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.runtime.control.journal import read_report


@pytest.mark.parametrize("stage", ["notify", "lease", "delete", "final"])
def test_abrupt_initiator_exit_is_suspended_and_resumable(tmp_path, write_file, make_service, stage):
    root = tmp_path / "project"
    write_file(root / "main.py", "def item():\n    return 1\n")
    service = make_service(root)
    child = root / "child"
    child.mkdir()
    write_index_markers(child / ".auto-index-mcp", child)
    worker = Path(__file__).with_name("crash_worker.py")
    result = subprocess.run([sys.executable, "-B", str(worker), str(root), stage],
                            env=dict(os.environ, AUTO_INDEX_SEMANTIC_MODE="off"),
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 73, result.stderr
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = service.status()
        if not status["enabled"] and status.get("suspension", dict()).get("phase") == "interrupted":
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"initiator exit was not observed: {status}")
    identity = status["suspension"]["operation_id"]
    recovered = service.purge()
    assert recovered["complete"] and recovered["operation_id"] == identity, recovered
    assert recovered["removed_indexes"] == 2
    assert not (service.index_root / "index.db").exists()
    assert read_report(service.project.control.store, root, identity, 0)["complete"]
