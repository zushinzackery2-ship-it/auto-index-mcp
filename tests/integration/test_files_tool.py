"""find_files: the merged fuzzy file finder (old query + resolve_path)."""

from __future__ import annotations

from pathlib import Path

import pytest

CODE_PY = "def compute(rows):\n    return sum(rows)\n"
UTIL_TS = "export function helper() { return 1; }\n"


@pytest.fixture
def service(tmp_path: Path, write_file, make_service):
    project = tmp_path / "proj"
    write_file(project / "src" / "core" / "compute.py", CODE_PY)
    write_file(project / "src" / "web" / "util.ts", UTIL_TS)
    write_file(project / "tools" / "compute_helper.py", "def helper_tool():\n    return 2\n")
    return make_service(project)


def _paths(result: dict) -> list[str]:
    return [item["path"] for item in result["items"]]


def test_name_fragment_matches_paths(service) -> None:
    result = service.find_files(query="compute")
    assert result["format"] == "auto_index_files_v2"
    assert set(_paths(result)) == {"src/core/compute.py", "tools/compute_helper.py"}


def test_backslash_query_is_normalized(service) -> None:
    result = service.find_files(query="src\\core")
    assert _paths(result) == ["src/core/compute.py"]


def test_glob_query(service) -> None:
    result = service.find_files(query="src/**/*.py")
    assert _paths(result) == ["src/core/compute.py"]


def test_language_filter(service) -> None:
    result = service.find_files(languages=["typescript"])
    assert _paths(result) == ["src/web/util.ts"]


def test_dir_filter_composes_with_query(service) -> None:
    result = service.find_files(query="compute", dir="tools")
    assert _paths(result) == ["tools/compute_helper.py"]


def test_symbol_name_fallback(service) -> None:
    result = service.find_files(query="helper_tool")
    assert _paths(result) == ["tools/compute_helper.py"]


def test_empty_query_lists_directory(service) -> None:
    result = service.find_files(dir="src")
    assert set(_paths(result)) == {"src/core/compute.py", "src/web/util.ts"}


def test_pagination_cursor(service) -> None:
    page_one = service.find_files(limit=2)
    assert len(page_one["items"]) == 2
    assert page_one["cursor"] is not None
    page_two = service.find_files(limit=2, cursor=page_one["cursor"])
    assert len(page_two["items"]) == 1
    assert page_two["cursor"] is None
    assert page_one["total_matches"] == 3
