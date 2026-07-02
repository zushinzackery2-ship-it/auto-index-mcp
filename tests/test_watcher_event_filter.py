from __future__ import annotations

from pathlib import Path

from auto_index_mcp.indexing.watcher import make_event_filter


ROOT = Path("D:/proj") if Path("D:/").exists() else Path("/proj")
OWN_INDEX = ROOT / ".auto-index-mcp"


def _filter():
    return make_event_filter(ROOT, OWN_INDEX)


def test_own_index_directory_events_are_ignored():
    should_ignore = _filter()
    assert should_ignore(OWN_INDEX / "index.db")
    assert should_ignore(OWN_INDEX / "index.db-wal")
    assert should_ignore(OWN_INDEX / "embeddings.db")
    assert should_ignore(OWN_INDEX / "index.build.lock")


def test_always_excluded_directories_are_ignored():
    should_ignore = _filter()
    assert should_ignore(ROOT / ".git" / "HEAD")
    assert should_ignore(ROOT / "__pycache__" / "m.pyc")
    assert should_ignore(ROOT / "node_modules" / "pkg" / "index.js")
    assert should_ignore(ROOT / ".venv" / "Scripts" / "python.exe")


def test_child_index_events_are_kept():
    # A subproject's .auto-index-mcp fingerprints child WAL commits; those
    # events must keep flowing so parent child-links stay fresh.
    should_ignore = _filter()
    assert not should_ignore(ROOT / "subproj" / ".auto-index-mcp" / "index.db")
    assert not should_ignore(ROOT / "subproj" / ".auto-index-mcp" / "index.db-wal")


def test_regular_source_and_root_events_are_kept():
    should_ignore = _filter()
    assert not should_ignore(ROOT / "src" / "main.py")
    assert not should_ignore(ROOT)


def test_paths_outside_root_are_not_filtered():
    should_ignore = _filter()
    outside = ROOT.parent / "elsewhere" / ".git" / "HEAD"
    assert not should_ignore(outside)


def test_excluded_name_in_root_prefix_does_not_leak():
    # A project living under D:/build/proj must not have every event filtered
    # just because "build" appears in the absolute prefix.
    root = ROOT.parent / "build" / "proj"
    should_ignore = make_event_filter(root, root / ".auto-index-mcp")
    assert not should_ignore(root / "src" / "app.py")
    assert should_ignore(root / "build" / "out.obj")
