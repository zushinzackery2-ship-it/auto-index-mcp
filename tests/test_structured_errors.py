"""Structured error payloads: no raw exceptions, always a hint, candidates
when the index can suggest close matches."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_index_mcp.mcp_api.guard import run_tool

CODE_PY = '''def compute_totals(rows):
    return sum(rows)


def render_report():
    return "ok"
'''

DUP_PY = """def work():
    return 1


def work():
    return 2
"""


@pytest.fixture
def service(tmp_path: Path, write_file, make_service):
    project = tmp_path / "proj"
    write_file(project / "src" / "report.py", CODE_PY)
    write_file(project / "src" / "dup.py", DUP_PY)
    return make_service(project)


def test_file_not_found_returns_structured_error(service) -> None:
    result = service.file_summary("src/reprot.py")
    assert result["format"] == "auto_index_error"
    assert result["error"] == "file-not-found"
    assert "hint" in result


def test_unique_bare_file_name_resolves_directly(service) -> None:
    result = service.file_summary("report.py")
    assert result.get("error") is None
    assert result["path"] == "src/report.py"


def test_bare_file_name_resolves_or_suggests(service) -> None:
    result = service.file_summary("nonexistent_dir/report.py")
    assert result["error"] == "file-not-found"
    assert "src/report.py" in result["candidates"]


def test_symbol_not_found_lists_near_names(service) -> None:
    result = service.symbol_body("compute_total", "src/report.py")
    if result.get("error"):
        assert result["error"] == "symbol-not-found"
        names = [candidate["name"] for candidate in result["candidates"]]
        assert "compute_totals" in names


def test_symbol_body_without_path_resolves_globally(service) -> None:
    result = service.symbol_body("render_report")
    assert result["format"] == "auto_index_symbol_body_v2"
    assert result["path"] == "src/report.py"
    assert "def render_report" in result["code"]


def test_symbol_body_ambiguous_returns_candidates(service) -> None:
    result = service.symbol_body("work", "src/dup.py")
    assert result["format"] == "auto_index_symbol_body_ambiguous"
    assert len(result["candidates"]) == 2
    assert "hint" in result


def test_symbol_body_line_disambiguates(service) -> None:
    ambiguous = service.symbol_body("work", "src/dup.py")
    second_line = ambiguous["candidates"][1]["line"]
    result = service.symbol_body("work", "src/dup.py", line=second_line)
    assert result["format"] == "auto_index_symbol_body_v2"
    assert result["line"] == second_line


def test_guard_converts_value_errors(service) -> None:
    result = run_tool(service.text_search, "")
    assert result["error"] == "invalid-argument"


def test_guard_converts_runtime_errors() -> None:
    from auto_index_mcp.core.service import AutoIndexService

    fresh = AutoIndexService()
    result = run_tool(fresh.overview)
    assert result["error"] == "not-enabled"
    assert "auto_index_enable" in result["hint"]


def test_symbol_refs_miss_carries_candidates(service) -> None:
    result = service.symbol_refs("compute_tot")
    assert result["error"] == "symbol-not-found"
    assert any(
        candidate["name"] == "compute_totals" for candidate in result.get("candidates", [])
    )
