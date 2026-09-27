"""Index registry: registration on enable, markers, verify/clean/list surfaces."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from auto_index_mcp.domain.config import registry_directory
from auto_index_mcp.registry import (
    CACHEDIR_TAG_NAME,
    IndexRegistry,
    MARKER_FILE_NAME,
    build_lock_active,
    is_safe_to_delete,
    registry_key,
    remove_index_dir,
    write_index_markers,
)

from tests.fixtures.registry import enable_in_place as _enable_in_place
from tests.fixtures.registry import project as project


def test_registry_directory_honours_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTO_INDEX_REGISTRY_DIR", str(tmp_path / "custom"))
    assert registry_directory() == tmp_path / "custom"


def test_register_creates_json_and_preserves_created_at(tmp_path: Path) -> None:
    registry = IndexRegistry()
    root = tmp_path / "proj"
    root.mkdir()
    assert registry.register(root, root / ".auto-index-mcp", source="cli-build") is True

    data = json.loads(registry.path().read_text(encoding="utf-8"))
    assert data["version"] == 2
    entry = data["entries"][registry_key(root / ".auto-index-mcp")]
    assert entry["source"] == "cli-build"
    assert entry["ephemeral"] is False
    created_at = entry["created_at"]

    assert registry.register(root, root / ".auto-index-mcp", source="mcp-enable") is True
    updated = registry.get(root)
    assert updated is not None
    assert updated["created_at"] == created_at
    assert updated["source"] == "mcp-enable"


def test_touch_is_throttled_to_an_hour(tmp_path: Path) -> None:
    registry = IndexRegistry()
    root = tmp_path / "proj"
    root.mkdir()
    registry.register(root, root / ".auto-index-mcp", source="cli-build")

    entry = registry.get(root)
    assert entry is not None
    recent = entry["last_attached_at"]
    assert registry.touch(root) is True
    assert registry.get(root)["last_attached_at"] == recent  # within the hour: no write

    stale = time.time() - 7200
    data = registry._read()
    data["entries"][registry_key(root / ".auto-index-mcp")]["last_attached_at"] = stale
    registry._write(data)
    assert registry.touch(root) is True
    assert registry.get(root)["last_attached_at"] > stale + 3600


def test_touch_unknown_root_is_a_noop(tmp_path: Path) -> None:
    assert IndexRegistry().touch(tmp_path / "nope") is False


def test_unregister_removes_entry(tmp_path: Path) -> None:
    registry = IndexRegistry()
    root = tmp_path / "proj"
    root.mkdir()
    registry.register(root, root / ".auto-index-mcp", source="cli-build")
    assert registry.unregister(root) is True
    assert registry.is_registered(root) is False
    assert registry.unregister(root) is False


def test_corrupt_registry_file_degrades_to_empty(tmp_path: Path) -> None:
    registry = IndexRegistry()
    registry.path().parent.mkdir(parents=True, exist_ok=True)
    registry.path().write_text("{not json", encoding="utf-8")
    assert registry.entries() == {}
    root = tmp_path / "proj"
    root.mkdir()
    assert registry.register(root, root / ".auto-index-mcp", source="scan") is True


def test_write_index_markers_creates_marker_and_cachedir_tag(tmp_path: Path) -> None:
    index_dir = tmp_path / "proj" / ".auto-index-mcp"
    write_index_markers(index_dir, tmp_path / "proj")

    marker = json.loads((index_dir / MARKER_FILE_NAME).read_text(encoding="utf-8"))
    assert marker["tool"] == "auto-index-mcp"
    assert Path(marker["root"]) == (tmp_path / "proj").resolve()

    tag = (index_dir / CACHEDIR_TAG_NAME).read_bytes()
    assert tag[:43] == b"Signature: 8a477f597d28d172789f06886806cdb5"


def test_write_index_markers_is_idempotent(tmp_path: Path) -> None:
    index_dir = tmp_path / ".auto-index-mcp"
    write_index_markers(index_dir, tmp_path)
    original = (index_dir / MARKER_FILE_NAME).read_text(encoding="utf-8")
    write_index_markers(index_dir, tmp_path / "elsewhere")
    assert (index_dir / MARKER_FILE_NAME).read_text(encoding="utf-8") == original


def test_verify_flags_orphans(tmp_path: Path) -> None:
    registry = IndexRegistry()
    live = tmp_path / "live"
    live.mkdir()
    write_index_markers(live / ".auto-index-mcp", live)
    (live / ".auto-index-mcp" / "index.db").write_bytes(b"")
    registry.register(live, live / ".auto-index-mcp", source="scan")

    gone = tmp_path / "gone"
    gone.mkdir()
    registry.register(gone, gone / ".auto-index-mcp", source="scan")
    import shutil

    shutil.rmtree(gone)

    by_key = {entry["key"]: entry for entry in registry.verify()}
    assert by_key[registry_key(live / ".auto-index-mcp")]["orphan"] is False
    assert by_key[registry_key(gone / ".auto-index-mcp")]["orphan"] is True
    assert by_key[registry_key(gone / ".auto-index-mcp")]["index_exists"] is False


def test_scan_and_adopt_registers_existing_indexes(project: Path) -> None:
    service = _enable_in_place(project)
    service.disable()
    registry = IndexRegistry()
    registry.unregister(project)

    adopted = registry.scan_and_adopt(project.parent)
    assert [Path(entry["root"]) for entry in adopted] == [project.resolve()]
    assert registry.get(project)["source"] == "scan"
    # Second scan adopts nothing new.
    assert registry.scan_and_adopt(project.parent) == []


def test_is_safe_to_delete_requires_name_and_attribution(project: Path, tmp_path: Path) -> None:
    service = _enable_in_place(project)
    service.disable()
    index_dir = project / ".auto-index-mcp"
    assert is_safe_to_delete(index_dir, project) is True
    assert is_safe_to_delete(index_dir, tmp_path / "other") is False

    # Without the marker the index.db metadata still attributes the dir.
    (index_dir / MARKER_FILE_NAME).unlink()
    assert is_safe_to_delete(index_dir, project) is True

    # A random directory named .auto-index-mcp with no marker and no db: refuse.
    stranger = tmp_path / "stranger" / ".auto-index-mcp"
    stranger.mkdir(parents=True)
    assert is_safe_to_delete(stranger, tmp_path / "stranger") is False

    # Ephemeral override dirs (arbitrary name) need the explicit marker.
    override = tmp_path / ".idx"
    override.mkdir()
    assert is_safe_to_delete(override, project) is False
    write_index_markers(override, project)
    assert is_safe_to_delete(override, project) is True


def test_build_lock_active_detects_live_holder(tmp_path: Path) -> None:
    index_dir = tmp_path / ".auto-index-mcp"
    index_dir.mkdir()
    assert build_lock_active(index_dir) is False
    lock = index_dir / "index.build.lock"
    lock.write_text(f"{os.getpid()}:1:1\n0", encoding="ascii")
    assert build_lock_active(index_dir) is True
    lock.write_text("999999999:1:1\n0", encoding="ascii")  # provably dead pid
    assert build_lock_active(index_dir) is False


def test_remove_index_dir_only_touches_known_files(tmp_path: Path) -> None:
    index_dir = tmp_path / ".auto-index-mcp"
    index_dir.mkdir()
    (index_dir / "index.db").write_bytes(b"x")
    (index_dir / "keepsake.txt").write_text("mine", encoding="utf-8")
    removed, notes = remove_index_dir(index_dir)
    assert removed is False
    assert not (index_dir / "index.db").exists()
    assert (index_dir / "keepsake.txt").exists()
    assert any("keepsake.txt" in note for note in notes)

    (index_dir / "keepsake.txt").unlink()
    removed, _ = remove_index_dir(index_dir)
    assert removed is False
    assert index_dir.is_dir()
    assert all(path.name.endswith(".lock") for path in index_dir.iterdir())
