from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REQUIRED_TOOLS = {
    "auto_index_enable",
    "auto_index_ignore",
    "auto_index_status",
    "auto_index_text_search",
    "auto_index_symbol_search",
    "auto_index_semantic_search",
    "auto_index_embedding_status",
    "auto_index_file",
    "auto_index_quality_check",
}

FORBIDDEN_TOOLS = {
    "set_project_path",
    "find_files",
    "get_file_summary",
    "get_symbol_body",
    "search_code_advanced",
    "auto_index_get",
    "auto_index_file_summary",
    "auto_index_watcher_status",
    "auto_index_nesting_check",
    "auto_index_dangling_check",
}


async def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "auto_index_mcp.server"],
        cwd=project_root,
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    names = {tool.name for tool in tools.tools}
    missing = sorted(REQUIRED_TOOLS - names)
    if missing:
        print(f"missing tools: {', '.join(missing)}")
        return 1
    forbidden = sorted(FORBIDDEN_TOOLS & names)
    if forbidden:
        print(f"legacy tools still registered: {', '.join(forbidden)}")
        return 1
    print(f"mcp stdio ok: {len(names)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
