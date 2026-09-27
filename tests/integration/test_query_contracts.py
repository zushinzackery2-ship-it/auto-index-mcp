from __future__ import annotations

import asyncio
import threading
import time

from mcp.server.fastmcp import FastMCP

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.navigation import register_navigation_tools
from auto_index_mcp.mcp_api.guard import run_service, run_tool


def test_symbol_file_candidates_respect_every_filter(tmp_path, make_service, write_file):
    write_file(tmp_path / "src" / "target.py", "def only_prod_symbol():\n    pass\n")
    service = make_service(tmp_path)
    result = service.find_files("only_prod_symbol", dir="tests", languages=["cpp"])
    assert result["items"] == []
    assert result["total_matches"] == 0


def test_tree_depth_is_relative_to_requested_directory(tmp_path, make_service, write_file):
    write_file(tmp_path / "src" / "project" / "a" / "b.py", "def work():\n    pass\n")
    service = make_service(tmp_path)
    result = service.tree_get("src/project", depth=1)
    assert result["folders"][0]["folder"] == "src/project/a"


def test_resource_read_does_not_block_the_event_loop(tmp_path, monkeypatch):
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(tmp_path))
    mcp = FastMCP("resource-test")
    register_navigation_tools(mcp, service)
    finished = threading.Event()

    def slow_content(path):
        time.sleep(0.2)
        finished.set()
        return "source"

    monkeypatch.setattr(service, "file_content", slow_content)

    async def exercise():
        read = asyncio.create_task(mcp.read_resource("files://code.py"))
        await asyncio.sleep(0.01)
        blocked = finished.is_set()
        await read
        assert not blocked

    try:
        asyncio.run(exercise())
    finally:
        service.disable()


def test_symbol_body_rejects_stale_line_ranges(tmp_path, make_service, write_file):
    source = tmp_path / "code.py"
    write_file(source, "def work():\n    return 1\n")
    service = make_service(tmp_path)
    write_file(source, "# moved\n\ndef work():\n    return 2\n")
    assert run_tool(service.symbol_body, "work")["error"] == "source-changed"


def test_body_and_resource_have_byte_budgets(tmp_path, make_service, write_file):
    write_file(tmp_path / "code.py", 'def work():\n    return "' + "a" * 100_000 + '"\n')
    service = make_service(tmp_path)
    body = service.symbol_body("work")
    assert body["truncated"]
    assert len(body["code"].encode("utf-8")) <= body["byte_limit"]
    assert run_tool(service.file_content, "code.py")["error"] == "source-too-large"


def test_search_failure_uses_error_contract(tmp_path, make_service, write_file):
    write_file(tmp_path / "code.py", "x = 1\n")
    result = make_service(tmp_path).text_search("[", regex=True)
    assert result["format"] == "auto_index_error"
    assert result["error"] == "search-invalid-pattern"


def test_runtime_failure_is_not_reported_as_disabled():
    def fail():
        raise RuntimeError("unexpected backend failure")
    assert run_tool(fail)["error"] == "internal-error"


def test_control_status_bypasses_busy_query(tmp_path):
    service = AutoIndexService()
    started = threading.Event()
    release = threading.Event()

    def blocked_query():
        started.set()
        assert release.wait(3)

    async def exercise():
        query = asyncio.create_task(run_service(service, blocked_query))
        while not started.is_set():
            await asyncio.sleep(0.001)
        try:
            result = await asyncio.wait_for(run_service(service, service.status, control_plane=True), 0.5)
            assert result["enabled"] is False
        finally:
            release.set()
            await query

    asyncio.run(exercise())


def test_symbol_candidates_are_not_capped_at_two_hundred(tmp_path, make_service, write_file):
    for index in range(205):
        write_file(tmp_path / f"module_{index:03d}.py", "def shared_symbol():\n    pass\n")
    service = make_service(tmp_path)
    first = service.find_files("shared_symbol", limit=200)
    second = service.find_files("shared_symbol", limit=200, cursor=first["cursor"])
    assert first["total_matches"] == 205
    assert len(second["items"]) == 5
    assert second["cursor"] is None
