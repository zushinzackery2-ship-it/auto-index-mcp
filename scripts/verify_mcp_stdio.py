"""Real stdio contract check, including updates, resources and process cleanup."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from auto_index_mcp.runtime.leases import BuildLock
from auto_index_mcp.runtime.locking.native import process_alive
from auto_index_mcp.runtime.maintenance import LEASE_NAMES

REQUIRED_TOOLS = frozenset((
    "auto_index_enable", "auto_index_manage", "auto_index_status", "auto_index_text_search",
    "auto_index_symbol_search", "auto_index_semantic_search", "auto_index_files", "auto_index_overview",
    "auto_index_tree_get", "auto_index_symbol_body", "auto_index_symbol_refs", "auto_index_file",
    "auto_index_quality_check",
))
FORBIDDEN_TOOLS = frozenset((
    "set_project_path", "find_files", "get_file_summary", "get_symbol_body", "search_code_advanced",
    "auto_index_get", "auto_index_file_summary", "auto_index_watcher_status",
    "auto_index_nesting_check", "auto_index_dangling_check",
))


async def call(session, name, **arguments):
    result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=15)
    assert not result.isError, result
    assert isinstance(result.structuredContent, dict), result
    return result.structuredContent


async def wait_ready(session):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = await call(session, "auto_index_status")
        watcher = status.get("watcher", dict())
        if status["file_count"] == 3 and watcher.get("ready") and not status["index_build"]["running"]:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"index did not become ready: {status}")


async def check_tools(session, project):
    result = await call(session, "auto_index_enable", root_path=str(project))
    assert not result.get("error"), result
    queries = (
        ("auto_index_files", dict(query="greet", languages=["python"])),
        ("auto_index_overview", dict()),
        ("auto_index_tree_get", dict(dir="pkg", depth=1)),
        ("auto_index_file", dict(path="sample.py", detail="full")),
        ("auto_index_symbol_body", dict(symbol_name="greet", path="sample.py")),
        ("auto_index_symbol_refs", dict(symbol_name="greet", path="sample.py")),
        ("auto_index_quality_check", dict(kind="all")),
        ("auto_index_manage", dict(action="diff")),
    )
    for name, arguments in queries:
        result = await call(session, name, **arguments)
        assert not result.get("error"), (name, result)
    symbols = await call(session, "auto_index_symbol_search", text="greet")
    assert symbols["items"][0]["name"] == "greet", symbols
    matches = await call(session, "auto_index_text_search", pattern="hello", context_lines=1)
    assert matches["items"] and not matches.get("error"), matches
    empty = await call(session, "auto_index_text_search", pattern="absent-stdio-token")
    assert not empty["items"] and not empty.get("error"), empty
    invalid = await call(session, "auto_index_text_search", pattern="(", regex=True)
    assert invalid["format"] == "auto_index_error" and invalid["error"], invalid
    assert invalid["message"], invalid
    missing = await call(session, "auto_index_file", path="missing.py")
    assert missing.get("error") == "file-not-found", missing
    semantic = await call(session, "auto_index_semantic_search", query="greeting")
    assert semantic.get("status") == "disabled" or semantic.get("state") == "disabled", semantic


async def check_resources(session):
    resource_task = asyncio.create_task(session.read_resource("files://sample.py"))
    started = time.perf_counter()
    await asyncio.wait_for(session.send_ping(), timeout=2)
    ping_ms = (time.perf_counter() - started) * 1000
    resource = await resource_task
    assert "def greet" in resource.contents[0].text
    try:
        await session.read_resource("files://large.txt")
    except McpError as exc:
        assert "source-too-large" in str(exc), exc
    else:
        raise AssertionError("oversized resource was not rejected")
    return round(ping_ms, 3)


async def check_watcher(session, project):
    (project / "sample.py").write_text(
        "def greet_new(name):\n    return 'hello ' + name\ndef main():\n    return greet_new('world')\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    while time.monotonic() - started < 10:
        result = await call(session, "auto_index_symbol_search", text="greet_new")
        if result.get("items"):
            assert result["items"][0]["name"] == "greet_new", result
            return round(time.monotonic() - started, 3)
        await asyncio.sleep(0.05)
    raise AssertionError("watcher did not publish the source update")


async def check_cleanup(index_root, pid):
    deadline = time.monotonic() + 5
    while process_alive(pid) is not False and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert process_alive(pid) is False, f"server process {pid} survived stdio shutdown"
    for name in LEASE_NAMES:
        assert BuildLock(index_root / name).state_info() is None, f"lease still held: {name}"


async def verify_stdio(python: str = sys.executable, installed: bool = False) -> dict:
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="auto-index-stdio-") as temporary:
        project = Path(temporary) / "project"
        (project / "pkg").mkdir(parents=True)
        (project / "sample.py").write_text(
            "def greet(name):\n    return 'hello ' + name\ndef main():\n    return greet('world')\n",
            encoding="utf-8",
        )
        (project / "pkg" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "large.txt").write_text("x" * (65536 + 1), encoding="utf-8")
        env = dict(os.environ, AUTO_INDEX_REGISTRY_DIR=str(Path(temporary) / "registry"),
                   AUTO_INDEX_SEMANTIC_MODE="off", AUTO_INDEX_PROJECT_PATH=str(project))
        if installed:
            env.pop("PYTHONPATH", None)
        else:
            env["PYTHONPATH"] = str(repository / "src")
        params = StdioServerParameters(command=python, args=["-B", "-m", "auto_index_mcp.server"],
                                       cwd=project, env=env)
        with (Path(temporary) / "server.stderr").open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = frozenset(tool.name for tool in (await session.list_tools()).tools)
                    assert not REQUIRED_TOOLS - names, f"missing tools: {REQUIRED_TOOLS - names}"
                    assert not FORBIDDEN_TOOLS & names, f"legacy tools: {FORBIDDEN_TOOLS & names}"
                    await wait_ready(session)
                    owner = BuildLock(project / ".auto-index-mcp" / "watcher.lock").state_info()
                    assert owner is not None, "watcher owner lease is missing"
                    await check_tools(session, project)
                    ping_ms = await check_resources(session)
                    update_seconds = await check_watcher(session, project)
            await check_cleanup(project / ".auto-index-mcp", owner["holder_pid"])
    return dict(ok=True, tools=len(names), ping_ms=ping_ms, watcher_update_seconds=update_seconds,
                resources=True, structured_errors=True, process_cleanup=True, lease_cleanup=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(verify_stdio(args.python, args.installed))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
