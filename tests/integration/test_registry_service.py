"""Index registry: registration on enable, markers, verify/clean/list surfaces."""

from __future__ import annotations

import time
from pathlib import Path

import anyio

from mcp.server.fastmcp import FastMCP

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.lifecycle import register_lifecycle_tools
from auto_index_mcp.registry import (
    CACHEDIR_TAG_NAME,
    MARKER_FILE_NAME,
    registry_key,
)

from tests.fixtures.registry import enable_in_place as _enable_in_place
from tests.fixtures.registry import project as project


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
        data["entries"][registry_key(project / ".auto-index-mcp")]["last_attached_at"] = stale
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
