"""Pathological source inputs must finish inside an external process budget."""
from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("size", [64_000, 1_800_000])
def test_python_large_literal_has_bounded_analysis(tmp_path, size):
    path = tmp_path / "large.py"
    path.write_text("def value():\n    return " + repr("x" * size) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; from pathlib import Path; "
         "from auto_index_mcp.indexing.scanner import SourceScanner; "
         "p=Path(sys.argv[1]); "
         "r=SourceScanner(str(p.parent), max_bytes=2000000).read_path(p); "
         "assert r.symbols[0].name == 'value'", str(path)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_inline_cpp_analysis_scales_linearly():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "from auto_index_mcp.languages.c_family import extract_c_family_symbols; "
         "lines=[f'int f{i}() {{ return {i}; }}' for i in range(8000)]; "
         "symbols=extract_c_family_symbols(lines); "
         "assert len(symbols)==8000; "
         "assert all(s.line==s.end_line for s in symbols)"],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
