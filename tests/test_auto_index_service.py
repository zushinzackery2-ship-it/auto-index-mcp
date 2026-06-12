from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService


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


def test_core_pagination_rejects_invalid_values(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def helper():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    with pytest.raises(ValueError, match="limit"):
        service.query(limit=0)
    with pytest.raises(ValueError, match="limit"):
        service.text_search("helper", limit=0)
    with pytest.raises(ValueError, match="offset"):
        service.symbol_search(cursor="-1")


def test_default_index_lives_inside_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('local index')\n", encoding="utf-8")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["index_path"] == str(project / ".auto-index-mcp" / "index.db")
    assert (project / ".auto-index-mcp" / "index.db").exists()


def test_third_party_directory_is_not_indexed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    third_party = project / "third-party" / "vendor" / "lib"
    third_party.mkdir(parents=True)
    (project / "main.py").write_text("print('indexed')\n", encoding="utf-8")
    (third_party / "noise.h").write_text("#define NOISE 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    assert service.all_files()[0]["path"] == "main.py"


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


def test_typescript_symbols_and_text_search(tmp_path: Path) -> None:
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
    service.enable(str(project), rebuild=True)

    summary = service.file_summary("src/app.ts")
    search = service.text_search("helper", file_pattern="*.ts", context_lines=1)

    assert any(symbol["name"] == "App" for symbol in summary["symbols"])
    assert any(symbol["name"] == "start" for symbol in summary["symbols"])
    assert any(symbol["name"] == "helper" for symbol in summary["symbols"])
    assert search["items"][0]["path"] == "src/app.ts"
    assert search["items"][0]["context"]


def test_native_navigation_and_search_tools(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def target():\n    return 'ok'\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable(str(project), rebuild=True)

    summary = service.file_summary("main.py")
    symbol_body = service.symbol_body("main.py", "target")

    assert result["total_file_count"] == 1
    assert service.resolve_path("main.py")["items"][0]["path"] == "main.py"
    assert summary["symbols"][0]["name"] == "target"
    assert symbol_body["format"] == "auto_index_symbol_body_full"
    assert service.text_search("target")["items"][0]["path"] == "main.py"
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


def test_rebuild_prunes_called_by_for_deleted_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("from b import helper\n\ndef run():\n    return helper()\n", encoding="utf-8")
    (project / "b.py").write_text("def helper():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    helper = next(s for s in service.file_summary("b.py")["symbols"] if s["name"] == "helper")
    assert "a.py::run" in helper["called_by"]

    (project / "a.py").unlink()
    rebuild = service.rebuild()

    # b.py is unchanged on disk so it is reused, yet the reverse reference to the
    # deleted a.py must not survive the reuse path.
    assert rebuild["reused"] >= 1
    assert all(item["path"] != "a.py" for item in service.all_files())
    helper = next(s for s in service.file_summary("b.py")["symbols"] if s["name"] == "helper")
    assert "a.py::run" not in helper["called_by"]


def test_incremental_update_prunes_called_by_for_deleted_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("from b import helper\n\ndef run():\n    return helper()\n", encoding="utf-8")
    (project / "b.py").write_text("def helper():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    helper = next(s for s in service.file_summary("b.py")["symbols"] if s["name"] == "helper")
    assert "a.py::run" in helper["called_by"]

    (project / "a.py").unlink()
    service.sync_index_to_filesystem()

    assert all(item["path"] != "a.py" for item in service.all_files())
    helper = next(s for s in service.file_summary("b.py")["symbols"] if s["name"] == "helper")
    assert "a.py::run" not in helper["called_by"]


def test_invalid_root_rejected(tmp_path: Path) -> None:
    service = AutoIndexService(index_root=tmp_path / "index")

    with pytest.raises(ValueError):
        service.enable(str(tmp_path / "missing"))
