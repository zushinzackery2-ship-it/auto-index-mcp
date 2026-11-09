"""Real CLI and MCP entrypoints preserve the opt-in child-mounting contract."""
import asyncio
from contextlib import AsyncExitStack
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from auto_index_mcp import cli
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.bootstrap import ensure_enabled


def _project(tmp_path, write_file):
    root = tmp_path / "project"
    child = root / "library"
    write_file(root / "main.py", "def main():\n    return 1\n")
    write_file(child / "child.py", "def child_value():\n    return 2\n")
    return root, child


async def _call(session, name, **arguments):
    result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=15)
    assert not result.isError, result
    assert not result.structuredContent.get("error"), result.structuredContent
    return result.structuredContent


async def _ready(session, mounted):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = await _call(session, "auto_index_status")
        if status["total_file_count"] == 2 and not status["index_build"]["running"]:
            assert status["mount_child_indexes"] is mounted
            assert status["child_index_count"] == int(mounted)
            assert status["file_count"] == (1 if mounted else 2)
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"mode-specific MCP index did not become ready: {status}")


@pytest.mark.parametrize("startup_mounted", [False, True])
def test_real_stdio_mode_schema_startup_and_explicit_switch(tmp_path, write_file, startup_mounted):
    root, child = _project(tmp_path, write_file)
    nested = AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        asyncio.run(_stdio_modes(root, startup_mounted))
    finally:
        nested.disable()


def test_two_stdio_processes_keep_opposite_modes_independent(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    nested = AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        asyncio.run(_concurrent_modes(root))
    finally:
        nested.disable()


async def _concurrent_modes(root):
    env = dict(os.environ, AUTO_INDEX_PROJECT_PATH=str(root), AUTO_INDEX_SEMANTIC_MODE="off")
    async with AsyncExitStack() as stack:
        sessions = []
        for mounted in (False, True):
            arguments = ["-B", "-m", "auto_index_mcp.server", "--no-watch"]
            if mounted:
                arguments.append("--mount-child-indexes")
            parameters = StdioServerParameters(command=sys.executable, args=arguments, env=env, cwd=root)
            read, write = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions.append(session)
        flat, mounted = await asyncio.gather(_ready(sessions[0], False), _ready(sessions[1], True))
        assert flat["index_path"] != mounted["index_path"]
        await _call(sessions[1], "auto_index_manage", action="rebuild")
        await _ready(sessions[1], True)
        await _ready(sessions[0], False)
        for session in sessions:
            body = await _call(session, "auto_index_symbol_body", symbol_name="child_value")
            assert body["path"] == "library/child.py"


async def _stdio_modes(root, startup_mounted):
    arguments = ["-B", "-m", "auto_index_mcp.server", "--no-watch"]
    if startup_mounted:
        arguments.append("--mount-child-indexes")
    env = dict(os.environ, AUTO_INDEX_PROJECT_PATH=str(root), AUTO_INDEX_SEMANTIC_MODE="off")
    parameters = StdioServerParameters(command=sys.executable, args=arguments, env=env, cwd=root)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            enable = next(tool for tool in tools if tool.name == "auto_index_enable")
            option = enable.inputSchema["properties"]["mount_child_indexes"]
            assert option["type"] == "boolean" and option["default"] is False
            assert len(tools) == 13
            await _ready(session, startup_mounted)
            await _call(session, "auto_index_enable", root_path=str(root), auto_watch=False, mount_child_indexes=True)
            mounted = await _ready(session, True)
            body = await _call(session, "auto_index_symbol_body", symbol_name="child_value")
            assert body["path"] == "library/child.py"
            await _call(session, "auto_index_enable", root_path=str(root), auto_watch=False)
            flat = await _ready(session, False)
            assert flat["index_path"] != mounted["index_path"]
            assert "mount_child_indexes=True" in Path(mounted["log_path"]).read_text(encoding="utf-8")
            assert "mount_child_indexes=False" in Path(flat["log_path"]).read_text(encoding="utf-8")


def test_cli_build_and_status_select_each_mode_without_changing_placement(tmp_path, write_file, capsys):
    root, child = _project(tmp_path, write_file)
    nested = AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        assert cli.main(["build", str(root), "--no-semantic", "--quiet"]) == 0
        assert cli.main(["build", str(root), "--no-semantic", "--quiet", "--mount-child-indexes"]) == 0
        capsys.readouterr()
        assert cli.main(["status", str(root)]) == 0
        assert "child mounts: False" in capsys.readouterr().out
        assert cli.main(["status", str(root), "--mount-child-indexes"]) == 0
        output = capsys.readouterr().out
        assert "child mounts: True" in output
        assert "1 (+1 in child indexes)" in output
        assert (root / ".auto-index-mcp" / "index.db").exists()
        assert (root / ".auto-index-mcp-mounted" / "index.db").exists()
    finally:
        nested.disable()


def test_cold_attach_uses_default_even_after_previous_explicit_mount(tmp_path, write_file, monkeypatch):
    root, child = _project(tmp_path, write_file)
    monkeypatch.delenv("AUTO_INDEX_PROJECT_PATH", raising=False)
    nested, parent = AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        parent.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        parent.disable()

        async def list_roots():
            return SimpleNamespace(roots=[SimpleNamespace(uri=root.as_uri())])

        context = SimpleNamespace(session=SimpleNamespace(list_roots=list_roots))
        assert asyncio.run(ensure_enabled(parent, context)) is None
        if parent.background is not None:
            assert parent.background.wait(10)
        assert parent.mount_child_indexes is False
        assert parent.status()["child_index_count"] == 0
        assert parent.status()["file_count"] == 2
    finally:
        parent.disable()
        nested.disable()


def test_purge_cleans_default_mounted_and_child_caches_together(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    nested, flat, mounted = AutoIndexService(), AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        flat.enable(str(root), refresh_embedder=False)
        mounted.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        mounted.all_files()
        paths = [service.store.db_path for service in (nested, flat, mounted)]
        result = flat.purge()
        assert result["complete"], result
        assert result["removed_indexes"] == 3
        assert result["stopped_instances"] == 3
        assert all(not path.exists() for path in paths)
    finally:
        mounted.disable()
        flat.disable()
        nested.disable()
