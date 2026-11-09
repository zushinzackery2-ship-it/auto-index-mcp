from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_index_mcp.indexing.events.native_policy import NativeEventPolicy


def event(action, path):
    return SimpleNamespace(action=action, src_path=path,
        is_renamed_old=action == "old", is_renamed_new=action == "new")


def policy():
    return NativeEventPolicy(Path("project"), lambda path: "Saved" in path.parts,
        lambda path: event("removed", path), lambda path: event("created", path))


def test_ignored_changes_never_reach_metadata_classification():
    admission = policy()
    assert admission.admit([event("modified", "Saved/state.json")]) == []


def test_regular_changes_are_preserved():
    source = event("modified", "src/main.py")
    assert policy().admit([source]) == [source]


def test_ignored_rename_pair_is_dropped():
    assert policy().admit([event("old", "Saved/state.tmp"), event("new", "Saved/state.json")]) == []


def test_source_rename_pair_is_preserved():
    pair = [event("old", "src/before.py"), event("new", "src/after.py")]
    assert policy().admit(pair) == pair


def test_move_into_source_keeps_pair_and_visible_destination():
    pair = [event("old", "Saved/export.py"), event("new", "src/export.py")]
    assert policy().admit(pair) == pair


def test_move_out_of_source_records_removal_without_probing_ignored_destination():
    admitted = policy().admit([event("old", "src/export.py"), event("new", "Saved/export.py")])
    assert len(admitted) == 1
    assert admitted[0].action == "removed"
    assert admitted[0].src_path == "src/export.py"


def test_split_native_batches_preserve_rename_identity():
    admission = policy()
    source = event("old", "src/before.py")
    destination = event("new", "src/after.py")
    assert admission.admit([source]) == []
    assert admission.admit([destination]) == [source, destination]


def test_entering_destination_without_observed_source_records_creation():
    admitted = policy().admit([event("new", "src/export.py")])
    assert admitted[0].action == "created"


def test_malformed_repeated_rename_source_is_explicit_failure():
    with pytest.raises(RuntimeError, match="rename source"):
        policy().admit([event("old", "src/first.py"), event("old", "src/second.py")])
