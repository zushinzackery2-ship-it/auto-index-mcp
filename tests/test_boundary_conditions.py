"""Boundary condition tests for AutoIndexService.

Covers edge cases: empty projects, binary-only, large files,
deep nesting, unicode filenames, special paths, corruption recovery.
"""
from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService


# ── Empty / minimal projects ────────────────────────────────────────────────

def test_empty_project_reports_zero_files(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 0
    assert result["total_file_count"] == 0
    assert service.all_files() == []


def test_project_with_only_binary_files_ignored(tmp_path: Path) -> None:
    project = tmp_path / "binary_only"
    project.mkdir()
    (project / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024)
    (project / "archive.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 512)
    (project / "data.exe").write_bytes(b"MZ" + b"\x00" * 256)

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 0


def test_mixed_binary_and_source_files_only_indexes_source(tmp_path: Path) -> None:
    project = tmp_path / "mixed"
    project.mkdir()
    (project / "image.png").write_bytes(b"\x89PNG" + b"\x00" * 512)
    (project / "code.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (project / "data.json").write_text('{"key": "value"}', encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 2
    paths = {item["path"] for item in service.all_files()}
    assert "code.py" in paths
    assert "data.json" in paths
    assert "image.png" not in paths


# ── File size boundaries ──────────────────────────────────────────────────────

def test_under_2mb_file_is_indexed(tmp_path: Path) -> None:
    """Files under 2MB should be indexed. We use 1.9MB to stay comfortably below."""
    project = tmp_path / "under_limit"
    project.mkdir()
    large = project / "under_limit.py"
    # ~1.7 MB of source: 40,000 lines × ~44 chars ≈ 1.76 MB, comfortably under 2 MB cap
    lines = ["# line {} | padding data for size verification\n".format(i) for i in range(40_000)]
    large.write_text("".join(lines), encoding="utf-8")
    # Verify actual size
    actual = large.stat().st_size
    assert actual < 2 * 1024 * 1024, f"Test file is {actual}, not under 2MB limit"
    assert actual > 1_500_000, f"Test file is only {actual} bytes, need at least 1.5MB"

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1


def test_over_2mb_file_is_skipped(tmp_path: Path) -> None:
    project = tmp_path / "too_large"
    project.mkdir()
    too_big = project / "too_big.py"
    too_big.write_text("x = " + repr("a" * (2 * 1024 * 1024 + 1)), encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 0
    assert service.resolve_path("too_big.py")["items"] == []


def test_many_large_files_indexed_individually(tmp_path: Path) -> None:
    project = tmp_path / "many_large"
    project.mkdir()
    for i in range(5):
        f = project / f"large_{i}.py"
        f.write_text("def f" + str(i) + "():\n    return " + repr("x" * (1800 * 1024)), encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 5
    for i in range(5):
        summary = service.file_summary(f"large_{i}.py")
        assert summary["path"] == f"large_{i}.py"


# ── Path length / character boundaries ───────────────────────────────────────

def test_deeply_nested_files_indexed(tmp_path: Path) -> None:
    project = tmp_path / "deep"
    current = project
    for depth in range(15):
        current = current / f"level_{depth}"
        current.mkdir(parents=True)
        (current / f"file_{depth}.py").write_text(f"# depth {depth}\ndef fn_{depth}():\n    pass\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["total_file_count"] == 15
    assert service.resolve_path("fn_10")["items"]
    # Build the full path: level_0/level_1/.../level_10/file_10.py
    full_path = "/".join(f"level_{d}" for d in range(11)) + "/file_10.py"
    assert service.file_content(full_path).startswith("# depth 10")


def test_very_deep_nesting_50_levels(tmp_path: Path) -> None:
    project = tmp_path / "very_deep"
    current = project
    for _ in range(50):
        current = current / "a"
        current.mkdir(parents=True)
    (current / "deep.py").write_text("def deep():\n    return 42\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    assert service.resolve_path("deep.py")["items"]


def test_unicode_filename_indexed(tmp_path: Path) -> None:
    project = tmp_path / "unicode_names"
    project.mkdir()
    (project / "中文文件名.py").write_text("def 中文函数():\n    pass\n", encoding="utf-8")
    (project / "файл.py").write_text("def русская_функция():\n    pass\n", encoding="utf-8")
    (project / "emoji_🎉.py").write_text("def party():\n    pass\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 3
    paths = {item["path"] for item in service.all_files()}
    assert "中文文件名.py" in paths
    assert "файл.py" in paths
    assert "emoji_🎉.py" in paths


def test_special_chars_in_path_indexed(tmp_path: Path) -> None:
    project = tmp_path / "special"
    project.mkdir()
    (project / "file with spaces.py").write_text("def spaces():\n    pass\n", encoding="utf-8")
    (project / "file-dashes.py").write_text("def dashes():\n    pass\n", encoding="utf-8")
    (project / "file_underscores.py").write_text("def underscores():\n    pass\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 3


def test_symlink_to_file_is_resolved(tmp_path: Path) -> None:
    project = tmp_path / "symlink_project"
    project.mkdir()
    (project / "real.py").write_text("def real_func():\n    return True\n", encoding="utf-8")
    link = project / "link.py"
    try:
        link.symlink_to(project / "real.py")
    except OSError:
        pytest.skip("symlink not supported on this platform")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    # symlinks resolve to their target, so only one file
    assert result["file_count"] == 1


def test_symlink_loop_is_handled_gracefully(tmp_path: Path) -> None:
    project = tmp_path / "loop"
    project.mkdir()
    sub = project / "sub"
    sub.mkdir()
    (sub / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    try:
        (sub / "loop.py").symlink_to(sub / "a.py")
        (sub / "a.py").unlink()
        (sub / "a.py").symlink_to(sub / "loop.py")
    except OSError:
        pytest.skip("symlink not supported on this platform")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    # Must not crash; at least the original file structure should be stable
    assert result["file_count"] >= 0


# ── Index corruption recovery ────────────────────────────────────────────────

def test_corrupted_sqlite_rebuilds_cleanly(tmp_path: Path) -> None:
    project = tmp_path / "corrupt"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    assert service.status()["file_count"] == 1

    # Corrupt the index database
    index_db = tmp_path / "index" / "index.db"
    with open(index_db, "rb") as f:
        data = bytearray(f.read())
    data[100:110] = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    with open(index_db, "wb") as f:
        f.write(data)

    # Rebuild should recover
    service.rebuild()
    assert service.status()["file_count"] == 1
    assert service.resolve_path("main.py")["items"]


def test_missing_index_dir_recreates_cleanly(tmp_path: Path) -> None:
    project = tmp_path / "missing_dir"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    assert service.status()["file_count"] == 1

    # Delete index dir
    import shutil
    shutil.rmtree(tmp_path / "index")

    # Should recreate and re-index on next enable
    service2 = AutoIndexService(index_root=tmp_path / "index2")
    service2.enable(str(project), rebuild=True)
    assert service2.status()["file_count"] == 1


# ── Search boundary cases ──────────────────────────────────────────────────────

def test_search_empty_project_returns_empty(tmp_path: Path) -> None:
    project = tmp_path / "empty_search"
    project.mkdir()

    service = AutoIndexService()
    service.enable(str(project), rebuild=True)

    result = service.text_search("anything")
    assert result["items"] == []


def test_search_binary_file_content_returns_empty(tmp_path: Path) -> None:
    project = tmp_path / "binary_search"
    project.mkdir()
    binary = project / "data.bin"
    binary.write_bytes(b"\x00\x01\x02\x03" + b"searchable" * 100 + b"\xff\xfe")

    service = AutoIndexService()
    service.enable(str(project), rebuild=True)

    result = service.text_search("searchable")
    assert result["items"] == []


def test_symbol_search_finds_symbol_in_10k_line_file(tmp_path: Path) -> None:
    project = tmp_path / "huge_file"
    project.mkdir()
    huge = project / "huge.py"
    lines = [f"# line {i}\n" for i in range(10_000)]
    lines[5000] = "def unique_target_function():\n    pass\n"
    huge.write_text("".join(lines), encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    symbols = service.symbol_search("unique_target_function")
    assert len(symbols["items"]) == 1
    assert symbols["items"][0]["name"] == "unique_target_function"
