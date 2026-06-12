from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService


def test_many_child_indexes_merged(tmp_path: Path) -> None:
    project = tmp_path / "many_children"
    project.mkdir()

    for i in range(10):
        child = project / f"child_{i}"
        child.mkdir(parents=True)
        (child / "mod.py").write_text(f"def mod_{i}():\n    pass\n", encoding="utf-8")
        child_service = AutoIndexService()
        child_service.enable(str(child), rebuild=True)

    parent_service = AutoIndexService()
    result = parent_service.enable(str(project), rebuild=True)

    assert result["child_index_count"] == 10
    assert result["total_file_count"] == 10
    for i in range(10):
        assert parent_service.resolve_path(f"child_{i}/mod.py")["items"]


def test_deep_child_nesting_5_levels(tmp_path: Path) -> None:
    project = tmp_path / "deep_children"
    current = project
    roots = []
    for i in range(5):
        current = current / f"level_{i}"
        current.mkdir(parents=True)
        (current / f"file_{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
        roots.append(current)

    for root in reversed(roots):
        svc = AutoIndexService()
        svc.enable(str(root), rebuild=True)

    parent = AutoIndexService()
    result = parent.enable(str(project), rebuild=True)

    assert result["child_index_count"] == 1
    assert result["total_file_count"] == 5
    assert parent.resolve_path("level_4/file_4.py")["items"]


def test_sibling_child_indexes_no_conflict(tmp_path: Path) -> None:
    project = tmp_path / "siblings"
    project.mkdir()
    a = project / "pkg_a"
    b = project / "pkg_b"
    a.mkdir()
    b.mkdir()
    (a / "shared_name.py").write_text("def shared():\n    return 'a'\n", encoding="utf-8")
    (b / "shared_name.py").write_text("def shared():\n    return 'b'\n", encoding="utf-8")

    svc_a = AutoIndexService()
    svc_a.enable(str(a), rebuild=True)
    svc_b = AutoIndexService()
    svc_b.enable(str(b), rebuild=True)

    parent = AutoIndexService()
    parent.enable(str(project), rebuild=True)

    files = [item["path"] for item in parent.all_files()]
    assert "pkg_a/shared_name.py" in files
    assert "pkg_b/shared_name.py" in files
