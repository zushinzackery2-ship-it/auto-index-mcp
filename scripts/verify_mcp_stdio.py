from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


REQUIRED_TOOLS = {
    "auto_index_enable",
    "auto_index_status",
    "auto_index_text_search",
    "set_project_path",
    "search_code_advanced",
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
    print(f"mcp stdio ok: {len(names)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
