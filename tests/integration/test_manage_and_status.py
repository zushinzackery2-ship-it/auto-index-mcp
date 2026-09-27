"""Tool-surface contracts: compact status, manage dispatch, 13-tool surface."""

from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from mcp.server.fastmcp import FastMCP

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.lifecycle import register_lifecycle_tools
from auto_index_mcp.mcp_api.navigation import register_navigation_tools
from auto_index_mcp.mcp_api.quality import register_quality_tools
from auto_index_mcp.mcp_api.search import register_search_tools
from auto_index_mcp.mcp_api.semantic import register_semantic_tools

CODE_PY = "def compute(rows):\n    return sum(rows)\n"

EXPECTED_TOOLS = {
    "auto_index_enable",
    "auto_index_status",
    "auto_index_manage",
    "auto_index_overview",
    "auto_index_tree_get",
    "auto_index_files",
    "auto_index_file",
    "auto_index_text_search",
    "auto_index_symbol_search",
    "auto_index_symbol_body",
    "auto_index_symbol_refs",
    "auto_index_quality_check",
    "auto_index_semantic_search",
}


def _make_mcp(service: AutoIndexService) -> FastMCP:
    mcp = FastMCP("AutoIndexTest")
    register_lifecycle_tools(mcp, service)
    register_navigation_tools(mcp, service)
    register_search_tools(mcp, service)
    register_quality_tools(mcp, service)
    register_semantic_tools(mcp, service)
    return mcp


def _tool_fn(mcp: FastMCP, name: str):
    return mcp._tool_manager.get_tool(name).fn


def _call(mcp: FastMCP, name: str, /, **kwargs):
    fn = _tool_fn(mcp, name)

    async def runner():
        return await fn(**kwargs)

    return anyio.run(runner)


@pytest.fixture
def service(tmp_path: Path, write_file, make_service):
    project = tmp_path / "proj"
    write_file(project / "src" / "mod.py", CODE_PY)
    return make_service(project)


def test_tool_surface_is_exactly_thirteen(service) -> None:
    mcp = _make_mcp(service)

    async def names():
        return {tool.name for tool in await mcp.list_tools()}

    assert anyio.run(names) == EXPECTED_TOOLS


def test_status_is_flat_and_iso_timestamped(service) -> None:
    status = service.status()
    assert status["enabled"] is True
    assert status["file_count"] == 1
    assert "T" in status["updated_at"]
    assert set(status["index_build"]) == {"state", "phase", "running", "elapsed_seconds"}
    assert "state" in status["embedding"]
    # The old duplicated blobs must stay gone.
    assert "background_index" not in status
    assert "embedding_background" not in status
    assert "build_timers" not in status
    assert "last_result" not in str(status.get("watcher", {}))


def test_status_without_root_carries_enable_hint() -> None:
    status = AutoIndexService().status()
    assert status["enabled"] is False
    assert "auto_index_enable" in status["hint"]


def test_manage_ignore_roundtrip(service) -> None:
    mcp = _make_mcp(service)
    result = _call(mcp, "auto_index_manage", action="ignore_add", patterns=["*.log"])
    assert "*.log" in result["runtime_patterns"]
    result = _call(mcp, "auto_index_manage", action="ignore_status")
    assert "*.log" in result["runtime_patterns"]
    result = _call(mcp, "auto_index_manage", action="ignore_clear")
    assert result["runtime_patterns"] == []


def test_manage_diff_reports_drift(service, tmp_path: Path, write_file) -> None:
    mcp = _make_mcp(service)
    write_file(tmp_path / "proj" / "src" / "new_file.py", "def fresh():\n    return 1\n")
    result = _call(mcp, "auto_index_manage", action="diff")
    assert "src/new_file.py" in result["added"]


def test_manage_watch_start_stop(service) -> None:
    mcp = _make_mcp(service)
    started = _call(mcp, "auto_index_manage", action="watch_start")
    assert started["running"] is True
    stopped = _call(mcp, "auto_index_manage", action="watch_stop")
    assert stopped["running"] is False


def test_manage_disable_keeps_index(service) -> None:
    mcp = _make_mcp(service)
    result = _call(mcp, "auto_index_manage", action="disable")
    assert result["enabled"] is False
    assert result["file_count"] == 1


def test_enable_tool_reports_compact_result(service, tmp_path: Path) -> None:
    mcp = _make_mcp(service)
    result = _call(mcp, "auto_index_enable", root_path=str(tmp_path / "proj"))
    assert result["enabled"] is True
    assert result["file_count"] == 1
    assert "background_index" not in result
