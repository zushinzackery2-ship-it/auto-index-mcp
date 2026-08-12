"""Input-path tolerance: backslashes, absolute paths, case, quotes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auto_index_mcp.core.path_normalize import normalize_input_path, relativize_to_root

CODE_PY = "def compute(rows):\n    return sum(rows)\n"


def test_backslashes_become_forward_slashes() -> None:
    assert normalize_input_path("src\\pkg\\mod.py") == "src/pkg/mod.py"


def test_quotes_whitespace_and_dot_prefix_are_stripped() -> None:
    assert normalize_input_path('  "./src/mod.py"  ') == "src/mod.py"


def test_duplicate_slashes_collapse() -> None:
    assert normalize_input_path("src//pkg///mod.py") == "src/pkg/mod.py"


def test_absolute_path_inside_root_is_relativized(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    absolute = str(root / "src" / "mod.py")
    assert normalize_input_path(absolute, root) == "src/mod.py"


def test_absolute_path_outside_root_is_left_absolute(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    other = str(tmp_path / "elsewhere" / "mod.py")
    normalized = normalize_input_path(other, root)
    assert normalized == other.replace("\\", "/")


@pytest.mark.skipif(os.name != "nt", reason="case folding applies to Windows roots")
def test_relativize_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    root = tmp_path / "Proj"
    root.mkdir()
    swapped = str(root).lower() + "/src/mod.py"
    assert relativize_to_root(swapped, root) == "src/mod.py"


def test_service_lookup_accepts_windows_style_paths(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "src" / "mod.py", CODE_PY)
    service = make_service(project)

    summary = service.file_summary("src\\mod.py")
    assert summary["path"] == "src/mod.py"

    absolute = str(project / "src" / "mod.py")
    summary = service.file_summary(absolute)
    assert summary["path"] == "src/mod.py"


def test_service_lookup_falls_back_case_insensitively(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "src" / "Mod.py", CODE_PY)
    service = make_service(project)

    summary = service.file_summary("src/mod.py")
    assert summary["path"] == "src/Mod.py"


def test_symbol_body_accepts_normalized_paths(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "src" / "mod.py", CODE_PY)
    service = make_service(project)

    body = service.symbol_body("compute", "src\\mod.py")
    assert body["format"] == "auto_index_symbol_body_v2"
    assert "def compute" in body["code"]
