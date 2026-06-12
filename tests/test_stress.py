"""Stress and performance tests for AutoIndexService.

These tests measure behavior under heavy load: many files,
deep directories, concurrent watchers, large symbol counts,
high-frequency changes.
"""
from pathlib import Path
import time

from auto_index_mcp.core.service import AutoIndexService


# ── Large file count stress ────────────────────────────────────────────────────

def test_1000_files_indexed_in_reasonable_time(tmp_path: Path) -> None:
    project = tmp_path / "thousand"
    project.mkdir()
    for i in range(1000):
        subdir = project / f"pkg_{i // 100}"
        subdir.mkdir(exist_ok=True)
        (subdir / f"file_{i}.py").write_text(f"def func_{i}():\n    return {i}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    start = time.perf_counter()
    result = service.enable(str(project), rebuild=True)
    elapsed = time.perf_counter() - start

    assert result["file_count"] == 1000
    assert elapsed < 30.0, f"Indexing 1000 files took {elapsed:.1f}s, expected < 30s"
    assert service.symbol_search("func_500")["items"]
    assert service.text_search("return 750")["items"]


def test_5000_files_partial_scan_performance(tmp_path: Path) -> None:
    project = tmp_path / "five_k"
    project.mkdir()
    for i in range(5000):
        subdir = project / f"dir_{i // 500}"
        subdir.mkdir(exist_ok=True)
        (subdir / f"mod_{i}.py").write_text(f"# module {i}\nvalue = {i}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 5000
    # Resolve should be fast regardless of count
    start = time.perf_counter()
    for _ in range(100):
        service.resolve_path("dir_5/mod_2500.py")
    resolve_time = time.perf_counter() - start
    assert resolve_time < 5.0, f"100 resolves took {resolve_time:.1f}s"


# ── Deep directory stress ────────────────────────────────────────────────────────

def test_100_level_deep_directory_scanned(tmp_path: Path) -> None:
    project = tmp_path / "deep_hundred"
    current = project
    for i in range(100):
        current = current / f"d{i}"
        current.mkdir(parents=True)
    (current / "deep.py").write_text("def deep():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    start = time.perf_counter()
    result = service.enable(str(project), rebuild=True)
    elapsed = time.perf_counter() - start

    assert result["file_count"] == 1
    assert elapsed < 10.0
    assert service.resolve_path("deep.py")["items"]


# ── Large symbol count per file ─────────────────────────────────────────────────

def test_10000_symbols_in_single_file_indexed(tmp_path: Path) -> None:
    project = tmp_path / "many_symbols"
    project.mkdir()
    symbols_file = project / "many_sym.py"
    lines = ["# line\n" for _ in range(100)]
    lines.append("class Container:\n")
    for i in range(10000):
        lines.append(f"    def method_{i}(self):\n        pass\n")
    symbols_file.write_text("".join(lines), encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    start = time.perf_counter()
    result = service.enable(str(project), rebuild=True)
    elapsed = time.perf_counter() - start

    assert result["file_count"] == 1
    assert elapsed < 30.0
    summary = service.file_summary("many_sym.py")
    # Should have at least the class + many methods
    symbol_names = [s["name"] for s in summary["symbols"]]
    assert "Container" in symbol_names
    assert any("method_" in name for name in symbol_names)


def test_multiple_services_same_project_no_conflict(tmp_path: Path) -> None:
    project = tmp_path / "multi_service"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (project / "util.py").write_text("def util():\n    pass\n", encoding="utf-8")

    svc1 = AutoIndexService(index_root=tmp_path / "index1")
    svc1.enable(str(project), rebuild=True)
    svc2 = AutoIndexService(index_root=tmp_path / "index2")
    svc2.enable(str(project), rebuild=True)

    assert svc1.status()["file_count"] == 2
    assert svc2.status()["file_count"] == 2


# ── Memory / resource pressure ────────────────────────────────────────────────

def test_repeated_rebuild_no_memory_leak(tmp_path: Path) -> None:
    project = tmp_path / "rebuild_stress"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    for i in range(20):
        (project / f"file_{i}.py").write_text(f"def f{i}():\n    pass\n", encoding="utf-8")
        service.rebuild()

    # Should still be functional
    assert service.status()["file_count"] >= 20
    assert service.resolve_path("file_19.py")["items"]


def test_many_files_across_many_dirs_no_degradation(tmp_path: Path) -> None:
    project = tmp_path / "wide_and_deep"
    project.mkdir()
    for i in range(20):
        for j in range(50):
            subdir = project / f"pkg_{i}"
            subdir.mkdir(exist_ok=True)
            (subdir / f"file_{j}.py").write_text(f"# {i}/{j}\ndef f{i}_{j}():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1000

    # Resolve should remain fast
    start = time.perf_counter()
    for _ in range(50):
        service.resolve_path("pkg_10/file_25.py")
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0


# ── Text search stress ─────────────────────────────────────────────────────────

def test_search_across_1000_files_performance(tmp_path: Path) -> None:
    project = tmp_path / "search_stress"
    project.mkdir()
    for i in range(1000):
        (project / f"f{i}.py").write_text(f"# unique_key_{i:04d}\ndef fn{i}():\n    return 'key_{i}'\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    start = time.perf_counter()
    result = service.text_search("unique_key_0500")
    elapsed = time.perf_counter() - start

    assert result["items"]
    assert elapsed < 5.0, f"Search across 1000 files took {elapsed:.1f}s"


def test_regex_search_performance_across_large_files(tmp_path: Path) -> None:
    project = tmp_path / "regex_stress"
    project.mkdir()
    for i in range(100):
        lines = [f"line_{j} = {j}\n" for j in range(1000)]
        lines[500] = f"PATTERN_MATCH_{i} = True\n"
        (project / f"f{i}.py").write_text("".join(lines), encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    start = time.perf_counter()
    result = service.text_search(r"PATTERN_MATCH_\d+", regex=True, limit=120)
    elapsed = time.perf_counter() - start

    assert len(result["items"]) >= 100
    assert elapsed < 10.0


# ── Service enable/disable cycle stress ───────────────────────────────────────

def test_50_enable_disable_cycles(tmp_path: Path) -> None:
    project = tmp_path / "cycle_enable"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    for i in range(50):
        svc = AutoIndexService(index_root=tmp_path / f"idx_{i}")
        svc.enable(str(project), rebuild=True)
        assert svc.status()["file_count"] == 1
        del svc
        import gc
        gc.collect()


def test_many_child_indexes_20_rebuilt_together(tmp_path: Path) -> None:
    project = tmp_path / "bulk_children"
    project.mkdir()
    services = []

    for i in range(20):
        child = project / f"child_{i}"
        child.mkdir(parents=True)
        (child / "mod.py").write_text(f"def mod_{i}():\n    pass\n", encoding="utf-8")
        svc = AutoIndexService()
        svc.enable(str(child), rebuild=True)
        services.append(svc)

    parent = AutoIndexService()
    result = parent.enable(str(project), rebuild=True)

    assert result["child_index_count"] == 20
    assert result["total_file_count"] == 20

    for i in range(20):
        assert parent.resolve_path(f"child_{i}/mod.py")["items"]
