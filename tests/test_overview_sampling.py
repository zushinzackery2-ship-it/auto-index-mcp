"""Overview sampling: directory round-robin, archive demotion, symbol caps."""

from __future__ import annotations

from typing import Any

from auto_index_mcp.core.navigation_format import compact_file, overview_result, sample_files


def _file(path: str, symbols: int = 3, language: str = "python") -> dict[str, Any]:
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    name = path.rsplit("/", 1)[-1]
    return {
        "path": path,
        "name": name,
        "parent": parent,
        "language": language,
        "line_count": 10,
        "symbols": [{"name": f"sym_{index}"} for index in range(symbols)],
    }


def test_samples_round_robin_across_directories() -> None:
    files = [_file(f"oldtest/test_{index:02d}.py") for index in range(20)]
    files += [_file(f"src/mod_{index:02d}.py") for index in range(5)]
    picked = sample_files(files, 10)
    picked_dirs = {item["path"].split("/")[0] for item in picked}
    assert "src" in picked_dirs
    src_count = sum(1 for item in picked if item["path"].startswith("src/"))
    assert src_count == 5


def test_archive_directories_come_after_source() -> None:
    files = [_file("oldtest/test_a.py"), _file("src/app.py")]
    picked = sample_files(files, 2)
    assert picked[0]["path"].startswith("src/")


def test_entry_files_lead_within_a_directory() -> None:
    files = [
        _file("src/zz_helpers.py", symbols=9),
        _file("src/main.py", symbols=1),
    ]
    picked = sample_files(files, 2)
    assert picked[0]["path"] == "src/main.py"


def test_symbol_rich_files_beat_sparse_ones() -> None:
    files = [
        _file("src/sparse.py", symbols=0),
        _file("src/rich.py", symbols=7),
    ]
    picked = sample_files(files, 1)
    assert picked[0]["path"] == "src/rich.py"


def test_compact_file_caps_symbols_and_reports_total() -> None:
    item = _file("src/big.py", symbols=12)
    shaped = compact_file(item)
    assert len(shaped["symbols"]) == 8
    assert shaped["symbol_count"] == 12

    small = compact_file(_file("src/small.py", symbols=3))
    assert len(small["symbols"]) == 3
    assert "symbol_count" not in small


def test_overview_result_shape() -> None:
    files = [_file("src/app.py"), _file("oldtest/test_a.py")]
    result = overview_result(files, 20)
    assert result["format"] == "auto_index_overview_v2"
    assert result["file_count"] == 2
    assert result["languages"] == {"python": 2}
    assert set(result["top_directories"]) == {"src", "oldtest"}
    assert len(result["samples"]) == 2
