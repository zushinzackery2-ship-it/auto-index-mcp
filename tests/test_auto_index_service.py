from pathlib import Path

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

    assert "Indexed 1 total files (1 local)" in compat.set_project_path(str(project))
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


def test_set_project_path_reuse_skips_synchronous_catch_up(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    # First service builds the persistent index.
    CompatService(AutoIndexService(index_root=tmp_path / "index")).set_project_path(str(project))

    # A fresh service reusing that index must NOT run a synchronous filesystem
    # catch-up on the request thread; the watcher owns offline catch-up.
    reused = AutoIndexService(index_root=tmp_path / "index")

    def _fail_sync(*args: object, **kwargs: object) -> dict:
        raise AssertionError("reuse path must not call sync_index_to_filesystem")

    monkeypatch.setattr(reused, "sync_index_to_filesystem", _fail_sync)
    result = CompatService(reused).set_project_path(str(project))

    assert "Indexed 1 total files" in result
    assert "main.py" in [item["path"] for item in reused.all_files()]


def test_rebuild_reuse_if_fresh_skips_rescan(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)  # builds a fresh index

    calls = {"n": 0}
    real_rebuild = service._rebuild_now

    def _spy() -> dict:
        calls["n"] += 1
        return real_rebuild()

    monkeypatch.setattr(service, "_rebuild_now", _spy)

    # Index is already fresh: reuse_if_fresh must not trigger a rescan.
    service.rebuild(reuse_if_fresh=True)
    assert calls["n"] == 0
    assert "main.py" in [item["path"] for item in service.all_files()]

    # A forced rebuild still rescans.
    service.rebuild()
    assert calls["n"] == 1


def test_rebuild_lock_timeout_does_not_rescan(tmp_path: Path, monkeypatch) -> None:
    from auto_index_mcp.core import service as service_module

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    class ContendedLock:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def acquire(self, wait_seconds: float) -> bool:
            _ = wait_seconds
            return False

        def release(self) -> None:
            pass

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=False)
    monkeypatch.setattr(service_module, "BuildLock", ContendedLock)
    monkeypatch.setattr(
        service,
        "_rebuild_now",
        lambda: (_ for _ in ()).throw(AssertionError("lock timeout must not rescan")),
    )

    result = service.rebuild()

    assert result["status"] == "build-lock-timeout"
    assert result["rebuild"] is False


def test_apply_skips_redundant_write_when_index_already_current(tmp_path: Path) -> None:
    from auto_index_mcp.indexing.snapshot import WatchSnapshot, take_watch_snapshot
    from auto_index_mcp.indexing.updater import IndexUpdater

    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    updater = IndexUpdater(service.root_path, service.store, service.rebuild)
    stale_previous = WatchSnapshot(files={}, child_indexes={})

    # DB already matches the live tree (as if a peer process just wrote it):
    # apply must skip the redundant read+resolve+write.
    result = updater.apply(stale_previous, take_watch_snapshot(service.root_path))
    assert result["status"] == "shared-index-current"

    # After a real on-disk change (different size) the DB is stale, so apply
    # must do the work rather than skip it.
    source.write_text("def a():\n    return 123456789\n", encoding="utf-8")
    result_stale = updater.apply(stale_previous, take_watch_snapshot(service.root_path))
    assert result_stale["status"] == "incremental"


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


def test_invalid_root_rejected(tmp_path: Path) -> None:
    service = AutoIndexService(index_root=tmp_path / "index")

    with pytest.raises(ValueError):
        service.enable(str(tmp_path / "missing"))
