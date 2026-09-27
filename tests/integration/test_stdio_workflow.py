import asyncio
import importlib.util
from pathlib import Path

import pytest


@pytest.mark.smoke
def test_real_stdio_tools_resources_watcher_and_cleanup():
    script = Path(__file__).resolve().parents[2] / "scripts" / "verify_mcp_stdio.py"
    spec = importlib.util.spec_from_file_location("verify_mcp_stdio", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = asyncio.run(module.verify_stdio())
    assert report["ok"] and report["tools"] == 13
    assert report["resources"] and report["structured_errors"]
    assert report["process_cleanup"] and report["lease_cleanup"]
