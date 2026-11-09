"""Audit production source through a fresh process running the MCP contract."""
import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parents[2]


async def verify():
    temporary_root = REPOSITORY / ".pytest_cache"
    temporary_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="quality-protocol-", dir=temporary_root) as temporary:
        root = Path(temporary)
        environment = dict(os.environ, AUTO_INDEX_CACHE_MODE="user", AUTO_INDEX_CACHE_DIR=str(root / "cache"),
                           AUTO_INDEX_REGISTRY_DIR=str(root / "registry"), AUTO_INDEX_CONTROL_DIR=str(root / "control"),
                           AUTO_INDEX_SEMANTIC_MODE="off", PYTHONDONTWRITEBYTECODE="1")
        parameters = StdioServerParameters(command=sys.executable,
            args=["-B", "-m", "auto_index_mcp.server", "--no-watch"], env=environment, cwd=str(REPOSITORY))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                enabled = await session.call_tool("auto_index_enable", dict(root_path=str(REPOSITORY / "src"), auto_watch=False))
                assert not enabled.isError, enabled
                deadline = time.monotonic() + 60
                while True:
                    status = (await session.call_tool("auto_index_status", {})).structuredContent
                    if status["file_count"] and not status["index_build"]["running"]:
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"quality source build did not finish: {status}")
                    await asyncio.sleep(0.1)
                result = await session.call_tool("auto_index_quality_check", dict(kind="all", limit=1000))
                assert not result.isError, result
                report = result.structuredContent
                assert report["nesting"]["summary"]["total_findings"] == 0, report
                assert not any(item["confidence"] == "high" for item in report["dangling"]["findings"]), report
                destination = REPOSITORY / ".high-value-information/validation/architecture-mcp-quality.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(dict(nesting=report["nesting"]["summary"], dangling=report["dangling"]["summary"])))


if __name__ == "__main__":
    asyncio.run(verify())
