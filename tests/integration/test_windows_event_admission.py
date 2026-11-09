from __future__ import annotations

import os
from queue import Queue
from unittest.mock import Mock

import pytest
from watchdog.observers.api import ObservedWatch

from auto_index_mcp.indexing.events.observer import create_observer


@pytest.mark.skipif(os.name != "nt", reason="Native Windows emitter")
def test_ignored_snapshot_is_rejected_before_native_metadata_open(tmp_path, monkeypatch):
    from watchdog.observers.winapi import FILE_ACTION_MODIFIED, WinAPINativeEvent

    observer = create_observer(lambda path: "Saved" in path.parts)
    emitter = observer._emitter_class(Queue(), ObservedWatch(str(tmp_path), recursive=True))
    emitter._whandle = 1
    events = [WinAPINativeEvent(FILE_ACTION_MODIFIED, "Saved/state.json")]
    monkeypatch.setattr("watchdog.observers.read_directory_changes.read_events", lambda *args, **kwargs: events)
    probe = Mock(side_effect=AssertionError("Ignored live snapshot was opened for metadata"))
    monkeypatch.setattr("watchdog.observers.read_directory_changes.os.path.isdir", probe)
    emitter.queue_events(0)
    probe.assert_not_called()
