from pathlib import Path

import pytest

from auto_index_mcp.compatibility.code_index import CompatService
from auto_index_mcp.core.service import AutoIndexService


def test_parent_workspace_reuses_child_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")

    child_service = AutoIndexService()
    child_result = child_service.enable(str(child), rebuild=True)

    parent_service = AutoIndexService()
    parent_result = parent_service.enable(str(project), rebuild=True)
    parent_compat = CompatService(parent_service)

    assert child_result["index_path"] == str(child / ".auto-index-mcp" / "index.db")
    assert parent_result["file_count"] == 1
    assert parent_result["total_file_count"] == 2
    assert parent_result["child_index_count"] == 1
    assert parent_service.store is not None
    assert parent_service.store.all_files()[0]["path"] == "root.py"
    assert "Indexed 2 total files (1 local across 1 child indexes)" in parent_compat.set_project_path(str(project))

    files = [item["path"] for item in parent_service.all_files()]
    assert files == ["child/child.py", "root.py"]
    assert parent_service.resolve_path("child.py")["items"][0]["path"] == "child/child.py"
    assert parent_service.file_summary("child/child.py")["symbols"][0]["name"] == "child_only"
    assert parent_service.symbol_body("child/child.py", "child_only")["code"].startswith("def child_only")
    assert parent_service.file_content("child/child.py").startswith("def child_only")
    assert parent_service.text_search("child_only")["items"][0]["path"] == "child/child.py"
    assert parent_service.diff_filesystem()["deleted"] == []


def test_nested_child_indexes_recurse_from_each_child_database(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    grandchild = child / "grandchild"
    grandchild.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")
    (grandchild / "deep.py").write_text("def deep_only():\n    return True\n", encoding="utf-8")

    grandchild_service = AutoIndexService()
    grandchild_service.enable(str(grandchild), rebuild=True)
    child_service = AutoIndexService()
    child_result = child_service.enable(str(child), rebuild=True)
    parent_service = AutoIndexService()
    parent_result = parent_service.enable(str(project), rebuild=True)

    assert child_result["file_count"] == 1
    assert child_result["total_file_count"] == 2
    assert parent_result["file_count"] == 1
    assert parent_result["total_file_count"] == 3

    files = [item["path"] for item in parent_service.all_files()]
    assert files == ["child/child.py", "child/grandchild/deep.py", "root.py"]
    assert parent_service.resolve_path("deep.py")["items"][0]["path"] == "child/grandchild/deep.py"
    assert parent_service.file_summary("child/grandchild/deep.py")["symbols"][0]["name"] == "deep_only"
    assert parent_service.symbol_body("child/grandchild/deep.py", "deep_only")["code"].startswith("def deep_only")
    assert parent_service.file_content("child/grandchild/deep.py").startswith("def deep_only")
    search = parent_service.text_search("deep_only")
    assert search["backend"] == "ripgrep-indexed-files"
    assert search["items"][0]["path"] == "child/grandchild/deep.py"

    (grandchild / "deep.py").write_text("def deep_only():\n    return False\n", encoding="utf-8")
    assert parent_service.diff_filesystem()["changed"] == ["child/grandchild/deep.py"]


def test_file_content_rejects_same_prefix_path_escape(tmp_path: Path) -> None:
    project = tmp_path / "project"
    sibling = tmp_path / "project-other"
    project.mkdir()
    sibling.mkdir()
    (project / "main.py").write_text("print('inside')\n", encoding="utf-8")
    (sibling / "secret.py").write_text("print('outside')\n", encoding="utf-8")

    service = AutoIndexService()
    service.enable(str(project), rebuild=True)

    with pytest.raises(ValueError):
        service.file_content("../project-other/secret.py")


def test_missing_child_database_is_not_recreated_by_parent_view(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")

    child_service = AutoIndexService()
    child_result = child_service.enable(str(child), rebuild=True)
    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)
    assert parent_service.store is not None

    missing_db_path = Path(child_result["index_path"]).with_name("missing-index.db")
    child_rows = parent_service.store.child_indexes()
    child_rows[0]["db_path"] = str(missing_db_path)
    parent_service.store.replace_child_indexes(child_rows)

    files = [item["path"] for item in parent_service.all_files()]

    assert files == ["root.py"]
    assert not missing_db_path.exists()


def test_text_search_supports_literal_and_regex(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("token = 'abc123'\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    literal = service.text_search("abc123")
    regex = service.text_search(r"abc\d+", regex=True)

    assert literal["items"][0]["line"] == 1
    assert regex["items"][0]["path"] == "main.py"


def test_text_search_uses_lightweight_search_targets(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("token = 'abc123'\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    def fail_all_files() -> list[dict]:
        raise AssertionError("text search must not materialize full file records")

    monkeypatch.setattr(service.store, "all_files", fail_all_files)

    result = service.text_search("abc123")

    assert result["items"][0]["path"] == "main.py"
