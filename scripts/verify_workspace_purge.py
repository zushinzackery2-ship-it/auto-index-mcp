"""Five real MCP processes: four cooperate, one remains independent."""
import argparse
import asyncio
from contextlib import AsyncExitStack, ExitStack
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from auto_index_mcp.cache.locking import lock_directory
from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.runtime.control.store import ControlStore
from auto_index_mcp.runtime.leases import BuildLock
from auto_index_mcp.runtime.locking.native import process_alive
from auto_index_mcp.runtime.maintenance import LEASE_NAMES

from verify_mcp_stdio import call


async def connect(stack, logs, project, env, watch=False):
    arguments = ["-B", "-m", "auto_index_mcp.server"]
    if not watch:
        arguments.append("--no-watch")
    params = StdioServerParameters(command=sys.executable, args=arguments, cwd=project,
                                   env=dict(env, AUTO_INDEX_PROJECT_PATH=str(project)))
    errlog = logs.enter_context((project.parent / f"stderr-{time.time_ns()}.log").open("w", encoding="utf-8"))
    read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


async def ready(session):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = await call(session, "auto_index_status")
        if status["file_count"] and not status["index_build"]["running"]:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"index never became ready: {status}")


async def verify(installed=False):
    repository = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix="auto-index-purge-processes-") as temporary:
        base = Path(temporary)
        outer, root = base / "outer", base / "outer" / "project"
        child, unrelated = root / "child", base / "unrelated"
        for directory in (outer, root, child, unrelated):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "code.py").write_text("def item():\n    return 1\n", encoding="utf-8")
        env = dict(os.environ, AUTO_INDEX_CONTROL_DIR=str(base / "control"),
                   AUTO_INDEX_REGISTRY_DIR=str(base / "registry"), AUTO_INDEX_CACHE_DIR=str(base / "cache"),
                   AUTO_INDEX_CACHE_MODE="user", AUTO_INDEX_SEMANTIC_MODE="off")
        if installed:
            env.pop("PYTHONPATH", None)
        else:
            env["PYTHONPATH"] = str(repository / "src")
        with ExitStack() as logs:
            async with AsyncExitStack() as stack:
                leaf = await connect(stack, logs, child, env)
                leaf_status = await ready(leaf)
                first = await connect(stack, logs, root, env, watch=True)
                first_status = await ready(first)
                second = await connect(stack, logs, root, env, watch=True)
                await ready(second)
                parent = await connect(stack, logs, outer, env)
                parent_status = await ready(parent)
                assert (await call(parent, "auto_index_files"))["items"]
                other = await connect(stack, logs, unrelated, env, watch=True)
                other_status = await ready(other)
                deep = child / "nested"
                deep.mkdir()
                write_index_markers(deep / ".auto-index-mcp", deep)
                caches = [Path(row["index_path"]).parent for row in
                          (leaf_status, first_status, parent_status, other_status)]
                coordination = set(lock_directory(cache) for cache in caches)
                control = ControlStore(base / "control")
                pids = [member["pid"] for member in control.members()]
                preview = await call(first, "auto_index_manage", action="purge", dry_run=True)
                assert preview["selected_indexes"] == 3, preview
                pending = asyncio.create_task(call(first, "auto_index_manage", action="purge"))
                await asyncio.sleep(0.02)
                started = time.perf_counter()
                await asyncio.wait_for(first.send_ping(), timeout=2)
                await call(first, "auto_index_status")
                latency = round((time.perf_counter() - started) * 1000, 3)
                result = await pending
                assert result["complete"] and result["stopped_instances"] == 4, result
                assert result["removed_indexes"] == 3, result
                assert any(member["role"] == "external-mounter" for member in result["participants"]), result
                for session in (first, second, leaf, parent):
                    status = await call(session, "auto_index_status")
                    assert not status["enabled"] and status["suspension"]["phase"] == "purged", status
                    query = await call(session, "auto_index_symbol_search", text="item")
                    assert query["error"] == "workspace-suspended", query
                assert (await call(other, "auto_index_status"))["enabled"]
                assert (await call(other, "auto_index_symbol_search", text="item"))["items"]
                assert caches[2].is_dir() and caches[3].is_dir()
                assert not caches[0].exists() and not caches[1].exists()
                resource = await first.read_resource(result["details_resource"])
                assert json.loads(resource.contents[0].text)["complete"]
                try:
                    await first.read_resource("files://code.py")
                except McpError as error:
                    assert "workspace-suspended" in str(error)
                else:
                    raise AssertionError("suspended resource recreated the index")
                reconnect = await connect(stack, logs, root, env)
                reconnected = await call(reconnect, "auto_index_symbol_search", text="item")
                assert reconnected["error"] == "workspace-suspended", reconnected
                assert (await call(reconnect, "auto_index_status"))["last_purge"]["operation_id"] == result["operation_id"]
                enabled = await call(first, "auto_index_enable", auto_watch=True)
                assert not enabled.get("error"), enabled
                await ready(first)
                assert (await call(first, "auto_index_symbol_search", text="item"))["items"]
                assert (await call(second, "auto_index_symbol_search", text="item"))["error"] == "workspace-suspended"
                coordination.add(lock_directory(caches[1]))
            deadline = time.monotonic() + 5
            while any(process_alive(pid) is not False for pid in pids) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert all(process_alive(pid) is False for pid in pids), pids
            for directory in coordination:
                assert all(BuildLock(directory / name, resolve_cache=False).state_info() is None for name in LEASE_NAMES)
    return dict(ok=True, processes=5, stopped_instances=4, removed_indexes=3,
                ping_and_status_ms=latency, unrelated_preserved=True, reconnect_suspended=True,
                per_instance_resume=True, resources=True, process_and_lease_cleanup=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.installed))))


if __name__ == "__main__":
    main()
