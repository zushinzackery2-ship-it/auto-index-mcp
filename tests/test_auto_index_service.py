from pathlib import Path
import time

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.compatibility.code_index import CompatService


def test_rebuild_query_and_get(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "import os\n\n"
        "def helper():\n"
        "    return True\n\n"
        "class Runner:\n"
        "    def run(self):\n"
        "        if helper():\n"
        "            return True\n"
        "        return False\n",
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    query = service.query(text="Runner")
    assert query["items"][0]["path"] == "main.py"
    item = service.get("main.py")
    assert any(symbol["name"] == "Runner" for symbol in item["item"]["symbols"])

    symbols = service.symbol_search(text="Runner")
    body = service.symbol_body("main.py", "Runner")

    assert symbols["items"][0]["name"] == "Runner"
    assert symbols["items"][0]["kind"] == "class"
    assert "class Runner" in body["code"]
    assert "def run" in body["code"]
    summary = service.file_summary("main.py")
    helper = next(symbol for symbol in summary["symbols"] if symbol["name"] == "helper")
    runner = next(symbol for symbol in summary["symbols"] if symbol["name"] == "Runner")
    run_method = next(symbol for symbol in summary["symbols"] if symbol["name"] == "run")

    assert summary["total_complexity"] >= 3
    assert "Runner" in helper["called_by"]
    assert "helper" in runner["calls"]
    assert run_method["kind"] == "method"


def test_default_index_lives_inside_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('local index')\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["index_path"] == str(project / ".auto-index-mcp" / "index.db")
    assert (project / ".auto-index-mcp" / "index.db").exists()


def test_diff_filesystem_reports_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("print('a')\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    source.write_text("print('b')\n", encoding="utf-8")

    diff = service.diff_filesystem()

    assert diff["changed"] == ["main.py"]


def test_tree_overview_and_resolve(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.ts").write_text("function start() {}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    overview = service.overview()
    tree = service.tree_get(depth=1)
    resolved = service.resolve_path("app")

    assert overview["languages"]["typescript"] == 1
    assert tree["folders"][0]["folder"] == "src"
    assert resolved["items"][0]["path"] == "src/app.ts"


def test_typescript_symbols_and_compat_search(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.ts").write_text(
        "export class App {\n"
        "  start(): void {\n"
        "    helper();\n"
        "  }\n"
        "}\n"
        "const helper = () => true;\n",
        encoding="utf-8",
    )

    service = AutoIndexService(index_root=tmp_path / "index")
    compat = CompatService(service)
    compat.set_project_path(str(project))

    summary = service.file_summary("src/app.ts")
    search = compat.search_code_advanced("helper", file_pattern="*.ts", context_lines=1)

    assert any(symbol["name"] == "App" for symbol in summary["symbols"])
    assert any(symbol["name"] == "start" for symbol in summary["symbols"])
    assert any(symbol["name"] == "helper" for symbol in summary["symbols"])
    assert search["matches"][0]["path"] == "src/app.ts"
    assert search["matches"][0]["context"]


def test_code_index_compatibility_tools(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def target():\n    return 'ok'\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    compat = CompatService(service)

    assert "Indexed 1 files" in compat.set_project_path(str(project))
    assert compat.find_files("*.py") == ["main.py"]
    assert compat.get_file_summary("main.py")["functions"][0]["name"] == "target"
    assert compat.get_symbol_body("main.py", "target")["status"] == "success"
    assert compat.search_code_advanced("target")["matches"][0]["path"] == "main.py"
    assert service.file_content("main.py").startswith("def target")


def test_cross_file_called_by(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("from b import helper\n\ndef run():\n    return helper()\n", encoding="utf-8")
    (project / "b.py").write_text("def helper():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("b.py")
    helper = next(symbol for symbol in summary["symbols"] if symbol["name"] == "helper")

    assert "a.py::run" in helper["called_by"]


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

    assert child_result["index_path"] == str(child / ".auto-index-mcp" / "index.db")
    assert parent_result["file_count"] == 1
    assert parent_result["total_file_count"] == 2
    assert parent_result["child_index_count"] == 1
    assert parent_service.store.all_files()[0]["path"] == "root.py"

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
    assert search["backend"] == "indexed-files"
    assert search["items"][0]["path"] == "child/grandchild/deep.py"

    (grandchild / "deep.py").write_text("def deep_only():\n    return False\n", encoding="utf-8")
    assert parent_service.diff_filesystem()["changed"] == ["child/grandchild/deep.py"]


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


def test_watcher_refreshes_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('ready')\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(interval_seconds=0.25)

    try:
        (project / "new_file.py").write_text("def created():\n    return True\n", encoding="utf-8")
        deadline = time.time() + 5
        found = False
        while time.time() < deadline:
            if service.resolve_path("new_file.py")["items"]:
                found = True
                break
            time.sleep(0.1)
        assert found
        assert service.watcher_status()["change_count"] >= 1
    finally:
        service.stop_watcher()


def test_watcher_slims_parent_when_child_index_appears(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")

    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)

    assert parent_service.status()["file_count"] == 2
    assert [item["path"] for item in parent_service.store.all_files()] == ["child/child.py", "root.py"]

    parent_service.start_watcher(interval_seconds=0.25)
    try:
        child_service = AutoIndexService()
        child_service.enable(str(child), rebuild=True)

        deadline = time.time() + 5
        slimmed = False
        while time.time() < deadline:
            status = parent_service.status()
            if status["file_count"] == 1 and status["child_index_count"] == 1:
                slimmed = True
                break
            time.sleep(0.1)

        assert slimmed
        assert [item["path"] for item in parent_service.store.all_files()] == ["root.py"]
        assert parent_service.resolve_path("child.py")["items"][0]["path"] == "child/child.py"
    finally:
        parent_service.stop_watcher()


def test_invalid_root_rejected(tmp_path: Path) -> None:
    service = AutoIndexService(index_root=tmp_path / "index")

    with pytest.raises(ValueError):
        service.enable(str(tmp_path / "missing"))
