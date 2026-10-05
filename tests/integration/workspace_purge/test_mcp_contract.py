"""Agent schema, suspension errors and bounded report pages."""
import functools
import json
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
from auto_index_mcp.runtime.control.journal import read_report
from auto_index_mcp.runtime.files.json import atomic_json


def make_tools(service):
    mcp = FastMCP("PurgeContract")
    for register in (register_lifecycle_tools, register_navigation_tools, register_quality_tools,
                     register_search_tools, register_semantic_tools):
        register(mcp, service)
    return mcp


def call(mcp, name, **arguments):
    return anyio.run(functools.partial(mcp._tool_manager.get_tool(name).fn, **arguments))


def test_unbound_purge_does_not_auto_attach(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_PROJECT_PATH", str(tmp_path))
    service = AutoIndexService()
    result = call(make_tools(service), "auto_index_manage", action="purge")
    assert result["error"] == "workspace-not-bound", result
    assert service.root_path is None and service.store is None
    assert not (tmp_path / ".auto-index-mcp").exists()


def test_purge_schema_keeps_thirteen_tools(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    mcp = make_tools(make_service(tmp_path))
    tools = anyio.run(mcp.list_tools)
    assert len(tools) == 13
    manage = next(tool for tool in tools if tool.name == "auto_index_manage")
    parameters = manage.inputSchema["properties"]
    assert "purge" in parameters["action"]["enum"]
    assert parameters["dry_run"]["default"] is False
    assert parameters["timeout_seconds"]["default"] == 30
    assert "root_path" not in parameters and "force" not in parameters
    invalid = call(mcp, "auto_index_manage", action="diff", dry_run=True)
    assert invalid["error"] == "invalid-argument"
    invalid = call(mcp, "auto_index_manage", action="purge", timeout_seconds=0)
    assert invalid["error"] == "invalid-argument"


def test_ordinary_tools_and_resource_stay_suspended(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    mcp = make_tools(service)
    result = call(mcp, "auto_index_manage", action="purge")
    assert result["complete"], result
    queries = (
        ("auto_index_overview", dict()),
        ("auto_index_files", dict()),
        ("auto_index_file", dict(path="main.py")),
        ("auto_index_tree_get", dict()),
        ("auto_index_text_search", dict(pattern="item")),
        ("auto_index_symbol_search", dict(text="item")),
        ("auto_index_symbol_body", dict(symbol_name="item")),
        ("auto_index_symbol_refs", dict(symbol_name="item")),
        ("auto_index_quality_check", dict()),
        ("auto_index_semantic_search", dict(query="item")),
        ("auto_index_manage", dict(action="rebuild")),
    )
    for name, arguments in queries:
        suspended = call(mcp, name, **arguments)
        assert suspended["error"] == "workspace-suspended", (name, suspended)
    report = read_report(service.project.control.store, tmp_path, result["operation_id"], 0)
    assert report["complete"] and report["items"]
    assert not service.index_root.exists()
    enabled = call(mcp, "auto_index_enable", auto_watch=False)
    assert service.enabled and "error" not in enabled, enabled
    if service.background is not None:
        assert service.background.wait(5)
    assert call(mcp, "auto_index_symbol_search", text="item")["items"]


def test_report_pages_obey_count_and_bytes(tmp_path, make_service, write_file):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    store = service.project.control.store
    identity = "a" * 32
    rows = [dict(index=i, message="x" * 5000) for i in range(80)]
    atomic_json(store.operation_path(identity) / "report.json",
                dict(root=str(tmp_path), operation_id=identity, results=rows, blockers=[]))
    found, page = [], 0
    while True:
        report = read_report(store, tmp_path, identity, page)
        assert len(report["items"]) <= 50
        assert len(json.dumps(report, ensure_ascii=True).encode("utf-8")) <= 64 * 1024
        found.extend(row["index"] for row in report["items"])
        if report["next_uri"] is None:
            break
        page += 1
    assert found == list(range(80))
    try:
        read_report(store, tmp_path / "another", identity, 0)
    except ValueError as error:
        assert "another" in str(error)
    else:
        raise AssertionError("another workspace read the report")
    assert Path(store.operation_path(identity) / "report.json").is_file()


@pytest.mark.parametrize("oversized_summary", [False, True])
def test_report_pages_preserve_oversized_records(tmp_path, make_service, write_file, oversized_summary):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    store = make_service(tmp_path).project.control.store
    identity = "b" * 32
    row = dict(index=1, message='\u732b\\"\U0001f431' * 20000)
    summary = dict(root=str(tmp_path), operation_id=identity, complete=True)
    if oversized_summary:
        summary["note"] = "\u732b" * 20000
    atomic_json(store.operation_path(identity) / "report.json",
                dict(summary, results=[row], blockers=[], participants=[]))
    fragments, page = dict(), 0
    while True:
        report = read_report(store, tmp_path, identity, page)
        assert len(report["items"]) <= 50
        assert len(json.dumps(report, ensure_ascii=True).encode("utf-8")) <= 64 * 1024
        for item in report["items"]:
            assert item["format"] == "auto_index_report_fragment_v1"
            key = (item["collection"], item["row_index"])
            data = fragments.setdefault(key, "")
            assert item["offset"] == len(data)
            fragments[key] += item["data"]
            assert len(fragments[key]) <= item["total_characters"]
        if report["next_uri"] is None:
            break
        page += 1
        assert page < 50
    assert json.loads(fragments[("results", 0)]) == row
    if oversized_summary:
        assert json.loads(fragments[("summary", 0)]) == summary


def test_partial_report_is_readable_as_resource(tmp_path, make_service, write_file):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    write_file(service.index_root / "notes.txt", "keep")
    result = service.purge()
    assert result["status"] == "partial", result
    contents = anyio.run(functools.partial(make_tools(service).read_resource, result["details_resource"]))
    report = json.loads(next(iter(contents)).content)
    assert report["status"] == "partial" and report["items"], report
