from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.indexing.scanner import SourceScanner


def test_diff_filesystem_skips_child_symlink_files_outside_child_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")
    (outside / "external.py").write_text("def external_only():\n    return True\n", encoding="utf-8")

    child_service = AutoIndexService()
    child_service.enable(str(child), rebuild=True)
    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)
    _symlink_or_skip(outside / "external.py", child / "external_link.py")

    diff = parent_service.diff_filesystem()

    assert "child/external_link.py" not in diff["added"]
    assert diff["deleted"] == []
    assert diff["changed"] == []


def test_source_scanner_reports_out_of_root_paths_as_unindexable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("def external_only():\n    return True\n", encoding="utf-8")

    scanner = SourceScanner(str(project))

    with pytest.raises(ValueError, match="path is not indexable"):
        scanner.read_path(outside)


def test_diff_filesystem_ignores_child_index_rows_resolved_outside_parent_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")
    (outside / "external.py").write_text("def external_only():\n    return True\n", encoding="utf-8")

    child_service = AutoIndexService()
    child_result = child_service.enable(str(child), rebuild=True)
    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)
    assert parent_service.store is not None
    child_rows = parent_service.store.child_indexes()
    child_rows[0]["root"] = str(outside.resolve())
    child_rows[0]["db_path"] = child_result["index_path"]
    parent_service.store.replace_child_indexes(child_rows)

    diff = parent_service.diff_filesystem()

    assert diff["deleted"] == []
    assert diff["changed"] == []
    assert "child/external.py" not in diff["added"]


def _symlink_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink is not available in this environment: {exc}")