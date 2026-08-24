"""Index registry: registration on enable, markers, verify/clean/list surfaces."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import anyio
import pytest

from mcp.server.fastmcp import FastMCP

from auto_index_mcp.cli import main as cli_main
from auto_index_mcp.core.config import registry_directory
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.lifecycle import register_lifecycle_tools
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

CODE_PY = "def compute(rows):\n    return sum(rows)\n"


@pytest.fixture
def project(tmp_path: Path, write_file) -> Path:
    root = tmp_path / "proj"
    write_file(root / "src" / "mod.py", CODE_PY)
    return root


def _enable_in_place(project: Path, source: str = "mcp-enable") -> AutoIndexService:
    """Enable with the default in-project index location (non-ephemeral)."""
    service = AutoIndexService()
    service.enable(str(project), rebuild=True, refresh_embedder=False, source=source)
    return service


# ---- registry primitives ------------------------------------------------------


def test_registry_directory_honours_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTO_INDEX_REGISTRY_DIR", str(tmp_path / "custom"))
    assert registry_directory() == tmp_path / "custom"


def test_register_creates_json_and_preserves_created_at(tmp_path: Path) -> None:
    registry = IndexRegistry()
    root = tmp_path / "proj"
    root.mkdir()
    assert registry.register(root, root / ".auto-index-mcp", source="cli-build") is True

    data = json.loads(registry.path().read_text(encoding="utf-8"))
    assert data["version"] == 1
    entry = data["entries"][registry_key(root)]
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
    data["entries"][registry_key(root)]["last_attached_at"] = stale
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


# ---- markers -------------------------------------------------------------------


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


# ---- service integration --------------------------------------------------------


def test_enable_registers_and_writes_markers(project: Path) -> None:
    service = _enable_in_place(project, source="cli-build")
    try:
        entry = service.registry.get(project)
        assert entry is not None
        assert entry["source"] == "cli-build"
        assert entry["ephemeral"] is False
        index_dir = project / ".auto-index-mcp"
        assert (index_dir / MARKER_FILE_NAME).exists()
        assert (index_dir / CACHEDIR_TAG_NAME).exists()
        assert service.status()["registered"] is True
    finally:
        service.disable()


def test_enable_with_index_root_override_registers_ephemeral(project: Path, tmp_path: Path) -> None:
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(project), rebuild=True, refresh_embedder=False)
    try:
        entry = service.registry.get(project)
        assert entry is not None
        assert entry["ephemeral"] is True
        assert Path(entry["index_dir"]) == (tmp_path / ".idx").resolve()
    finally:
        service.disable()


def test_reenable_fast_path_touches_instead_of_registering(project: Path) -> None:
    service = _enable_in_place(project)
    try:
        stale = time.time() - 7200
        data = service.registry._read()
        data["entries"][registry_key(project)]["last_attached_at"] = stale
        service.registry._write(data)
        service.enable(str(project), rebuild=False, refresh_embedder=False)
        assert service.registry.get(project)["last_attached_at"] > stale
    finally:
        service.disable()


def test_clear_delete_file_unregisters(project: Path) -> None:
    service = _enable_in_place(project)
    try:
        assert service.registry.is_registered(project) is True
        service.clear(delete_file=True)
        assert service.registry.is_registered(project) is False
    finally:
        service.disable()


def test_enable_survives_broken_registry_location(project: Path, monkeypatch) -> None:
    # Point the registry at a path that cannot be a directory: enable must
    # still succeed because registration is best-effort.
    blocker = project.parent / "not-a-dir"
    blocker.write_text("file, not dir", encoding="utf-8")
    monkeypatch.setenv("AUTO_INDEX_REGISTRY_DIR", str(blocker / "sub"))
    service = _enable_in_place(project)
    try:
        assert service.status()["enabled"] is True
        assert service.status()["registered"] is False
    finally:
        service.disable()


# ---- verify / scan ---------------------------------------------------------------


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
    assert by_key[registry_key(live)]["orphan"] is False
    assert by_key[registry_key(gone)]["orphan"] is True
    assert by_key[registry_key(gone)]["index_exists"] is False


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


# ---- deletion safety ---------------------------------------------------------------


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
    assert removed is True
    assert not index_dir.exists()


# ---- CLI ---------------------------------------------------------------------------


def test_cli_list_prints_entries(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["list"]) == 0
    output = capsys.readouterr().out
    assert str(project.resolve()) in output
    assert "last attached" in output


def test_cli_clean_dry_run_deletes_nothing(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", str(project), "--dry-run"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert "dry run" in capsys.readouterr().out


def test_cli_clean_target_removes_index_and_entry(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", str(project), "-y"]) == 0
    assert not (project / ".auto-index-mcp").exists()
    assert IndexRegistry().is_registered(project) is False
    assert "removed" in capsys.readouterr().out


def test_cli_clean_default_selects_orphans_and_ephemeral(
    project: Path,
    tmp_path: Path,
    capsys,
) -> None:
    # Fresh, healthy, non-ephemeral index: the no-argument default must keep it.
    service = _enable_in_place(project)
    service.disable()
    # Orphan entry (project deleted) and an ephemeral one.
    orphan_root = tmp_path / "orphan"
    orphan_root.mkdir()
    registry = IndexRegistry()
    registry.register(orphan_root, orphan_root / ".auto-index-mcp", source="scan")
    import shutil

    shutil.rmtree(orphan_root)
    override = tmp_path / ".idx"
    write_index_markers(override, tmp_path / "ephproj")
    (override / "index.db").write_bytes(b"")
    (tmp_path / "ephproj").mkdir()
    registry.register(tmp_path / "ephproj", override, source="mcp-enable", ephemeral=True)

    assert cli_main(["clean", "-y"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert registry.is_registered(project) is True
    assert registry.is_registered(orphan_root) is False
    assert registry.is_registered(tmp_path / "ephproj") is False
    assert not override.exists()
    capsys.readouterr()


def test_cli_clean_older_than_filters_by_last_attach(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", "--older-than", "30d", "-y"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()

    registry = IndexRegistry()
    data = registry._read()
    data["entries"][registry_key(project)]["last_attached_at"] = time.time() - 40 * 86400
    registry._write(data)
    assert cli_main(["clean", "--older-than", "30d", "-y"]) == 0
    assert not (project / ".auto-index-mcp").exists()
    capsys.readouterr()


def test_cli_clean_skips_directories_with_active_build_lock(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    lock = project / ".auto-index-mcp" / "index.build.lock"
    lock.write_text(f"{os.getpid()}:1:1\n0", encoding="ascii")
    assert cli_main(["clean", str(project), "-y"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert IndexRegistry().is_registered(project) is True
    assert "skipped" in capsys.readouterr().out


def test_cli_clean_scan_adopts_unregistered_index(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    registry = IndexRegistry()
    registry.unregister(project)
    assert cli_main(["clean", "--scan", str(project.parent)]) == 0
    assert registry.is_registered(project) is True
    assert "adopted" in capsys.readouterr().out


def test_cli_clean_rejects_bad_older_than(capsys) -> None:
    assert cli_main(["clean", "--older-than", "soon"]) == 2
    assert "invalid --older-than" in capsys.readouterr().err


# ---- MCP surface --------------------------------------------------------------------


def _call_manage(service: AutoIndexService, **kwargs):
    mcp = FastMCP("AutoIndexRegistryTest")
    register_lifecycle_tools(mcp, service)
    fn = mcp._tool_manager.get_tool("auto_index_manage").fn

    async def runner():
        return await fn(**kwargs)

    return anyio.run(runner)


def test_mcp_registry_action_is_read_only_listing(project: Path) -> None:
    service = _enable_in_place(project)
    try:
        result = _call_manage(service, action="registry")
        assert "entries" in result
        roots = [entry["root"] for entry in result["entries"]]
        assert str(project.resolve()) in roots
        assert result["registry_path"].endswith("registry.json")
        # Read-only: the index itself is untouched.
        assert (project / ".auto-index-mcp" / "index.db").exists()
    finally:
        service.disable()


def test_status_reports_registered_flag(project: Path) -> None:
    service = _enable_in_place(project)
    try:
        assert service.status()["registered"] is True
        service.registry.unregister(project)
        assert service.status()["registered"] is False
    finally:
        service.disable()
