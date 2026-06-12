from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService


def test_enable_reusing_index_skips_synchronous_catch_up(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    AutoIndexService(index_root=tmp_path / "index").enable_reusing_index(str(project))
    reused = AutoIndexService(index_root=tmp_path / "index")

    def _fail_sync(*args: object, **kwargs: object) -> dict:
        raise AssertionError("reuse path must not call sync_index_to_filesystem")

    monkeypatch.setattr(reused, "sync_index_to_filesystem", _fail_sync)
    result = reused.enable_reusing_index(str(project))

    assert result["total_file_count"] == 1
    assert "main.py" in [item["path"] for item in reused.all_files()]


def test_first_build_result_can_start_auto_watch(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    result = service.enable_reusing_index(str(project))

    assert service.can_start_auto_watch(result) is True


def test_rebuild_reuse_if_fresh_skips_rescan(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    calls = {"n": 0}
    real_rebuild = service._rebuild_now

    def _spy() -> dict:
        calls["n"] += 1
        return real_rebuild()

    monkeypatch.setattr(service, "_rebuild_now", _spy)

    service.rebuild(reuse_if_fresh=True)
    assert calls["n"] == 0
    assert "main.py" in [item["path"] for item in service.all_files()]

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
    root = service.root_path
    store = service.store
    assert root is not None
    assert store is not None

    updater = IndexUpdater(root, store, service.rebuild)
    stale_previous = WatchSnapshot(files={}, child_indexes={})

    result = updater.apply(stale_previous, take_watch_snapshot(root))
    assert result["status"] == "shared-index-current"

    source.write_text("def a():\n    return 123456789\n", encoding="utf-8")
    result_stale = updater.apply(stale_previous, take_watch_snapshot(root))
    assert result_stale["status"] == "incremental"
