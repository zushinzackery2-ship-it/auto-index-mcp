from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.indexing.store import IndexStore
from auto_index_mcp.mcp_api.lifecycle import register_lifecycle_tools


def test_rebuild_respects_gitignore_for_files_and_child_indexes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    ignored = project / "generated"
    ignored_child = ignored / "child"
    egg_info = project / "pkg.egg-info"
    ignored_child.mkdir(parents=True)
    egg_info.mkdir(parents=True)
    (project / ".gitignore").write_text("generated/\n*.egg-info/\n", encoding="utf-8")
    (project / "main.py").write_text("print('indexed')\n", encoding="utf-8")
    (ignored / "noise.py").write_text("print('ignored')\n", encoding="utf-8")
    (egg_info / "PKG-INFO.txt").write_text("ignored metadata\n", encoding="utf-8")
    _write_child_index(ignored_child)

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    assert result["child_index_count"] == 0
    assert result["total_file_count"] == 1
    assert [item["path"] for item in service.all_files()] == ["main.py"]


def test_runtime_ignore_patterns_affect_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    vendor = project / "vendor"
    vendor.mkdir(parents=True)
    (project / "main.py").write_text("print('indexed')\n", encoding="utf-8")
    (vendor / "noise.py").write_text("print('ignored')\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    status = service.configure_ignore(["vendor/"], mode="replace")
    result = service.enable(str(project), rebuild=True)

    assert status["runtime_patterns"] == ["vendor/"]
    assert result["file_count"] == 1
    assert [item["path"] for item in service.all_files()] == ["main.py"]


def test_oversized_source_is_auto_ignored_and_reported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_oversized_dump(project / "dump.cs")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 0
    assert result["auto_ignored_paths"] == ["dump.cs"]
    assert service.ignore_status()["auto_patterns"] == ["/dump.cs"]
    assert service.resolve_path("dump.cs")["items"] == []


def test_privileged_patterns_index_oversized_dump(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_oversized_dump(project / "dump.cs")

    service = AutoIndexService(index_root=tmp_path / "index")
    status = service.configure_ignore(["/dump.cs"], mode="add", target="privileged")
    result = service.enable(str(project), rebuild=True)

    assert status["privileged_patterns"] == ["/dump.cs"]
    assert result["file_count"] == 1
    assert result["privileged_paths"] == ["dump.cs"]
    assert service.file_summary("dump.cs")["path"] == "dump.cs"


def test_privileged_patterns_are_loaded_from_metadata(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_oversized_dump(project / "dump.cs")

    index_root = tmp_path / "index"
    first = AutoIndexService(index_root=index_root)
    first.configure_ignore(["/dump.cs"], mode="add", target="privileged")
    first.enable(str(project), rebuild=True)

    reused = AutoIndexService(index_root=index_root)
    result = reused.enable_reusing_index(str(project), wait_seconds=3.0)

    assert result["file_count"] == 1
    assert reused.ignore_status()["privileged_patterns"] == ["/dump.cs"]
    assert reused.file_summary("dump.cs")["path"] == "dump.cs"


def test_gitignore_change_invalidates_reusable_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    generated = project / "generated"
    generated.mkdir(parents=True)
    (project / "main.py").write_text("print('indexed')\n", encoding="utf-8")
    (generated / "noise.py").write_text("print('first build')\n", encoding="utf-8")

    index_root = tmp_path / "index"
    first = AutoIndexService(index_root=index_root)
    first.enable(str(project), rebuild=True)
    assert sorted(item["path"] for item in first.all_files()) == [
        "generated/noise.py",
        "main.py",
    ]

    (project / ".gitignore").write_text("generated/\n", encoding="utf-8")
    reused = AutoIndexService(index_root=index_root)
    result = reused.enable_reusing_index(str(project), rebuild=False)

    assert result["status"] == "indexing-in-background"
    assert reused.background is not None
    assert reused.background.wait(5.0) is True
    assert reused.background.status()["state"] == "done"
    assert [item["path"] for item in reused.all_files()] == ["main.py"]


def test_lifecycle_registers_ignore_tool() -> None:
    class FakeMcp:
        def __init__(self) -> None:
            self.names: list[str] = []

        def tool(self) -> Any:
            def decorate(func: Any) -> Any:
                self.names.append(func.__name__)
                return func

            return decorate

    fake = FakeMcp()
    register_lifecycle_tools(fake, object())  # type: ignore[arg-type]

    assert "auto_index_ignore" in fake.names


def _write_child_index(root: Path) -> None:
    store = IndexStore(root / ".auto-index-mcp" / "index.db")
    store.initialize()
    store.replace_all(str(root.resolve()), [], [])


def _write_oversized_dump(path: Path) -> None:
    padding = "// padding data for oversized source\n" * 70_000
    path.write_text("class DumpRoot {}\n" + padding, encoding="utf-8")
