import threading
from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService


def _blocking_rebuild(service: AutoIndexService, gate: threading.Event):
    """Replace _rebuild_now with a variant that stalls until gate is set.

    Lets a test observe search behaviour while the background build is parked
    mid-flight (state=running) before the atomic replace_all lands.
    """
    real = service._rebuild_now

    def _blocked(indexer=None, context=None):
        gate.wait(5.0)
        return real(indexer, context)

    return _blocked


def test_first_build_search_returns_not_ready(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    gate = threading.Event()
    monkeypatch.setattr(service, "_rebuild_now", _blocking_rebuild(service, gate))
    service.enable_reusing_index(str(project))

    # Empty DB + build in flight -> callers get an explicit not-ready envelope,
    # never an empty result they could misread as "absent in code".
    res = service.symbol_search("alpha")
    assert res["format"] == "auto_index_not_ready"
    assert res["index_status"]["state"] == "running"
    assert res["index_status"]["ready"] is False
    assert res["items"] == []

    txt = service.text_search("alpha")
    assert txt["format"] == "auto_index_not_ready"

    gate.set()
    assert service.background is not None
    assert service.background.wait(10.0)

    # Once ready the response is clean again (zero pollution) and finds the symbol.
    res2 = service.symbol_search("alpha")
    assert res2["format"] == "auto_index_symbol_search_indexed"
    assert "index_status" not in res2
    assert any(r["name"] == "alpha" for r in res2["items"])


def test_rebuild_over_existing_index_marks_stale(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def beta():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)  # synchronous: prior index exists

    gate = threading.Event()
    monkeypatch.setattr(service, "_rebuild_now", _blocking_rebuild(service, gate))
    service.rebuild()  # background rebuild, parked mid-flight

    # Prior index still readable thanks to the atomic replace_all boundary, so we
    # serve old results but flag them stale rather than hiding them.
    res = service.symbol_search("beta")
    assert res["format"] == "auto_index_symbol_search_indexed"
    assert res["index_status"]["state"] == "running"
    assert res["index_status"]["stale"] is True
    assert res["index_status"]["ready"] is True
    assert any(r["name"] == "beta" for r in res["items"])

    gate.set()
    assert service.background is not None
    assert service.background.wait(10.0)
    res2 = service.symbol_search("beta")
    assert "index_status" not in res2


def test_no_background_means_zero_pollution(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def gamma():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)  # rebuild_sync: no background runner

    assert service.background is None
    res = service.symbol_search("gamma")
    assert res["format"] == "auto_index_symbol_search_indexed"
    assert "index_status" not in res


def test_point_lookup_not_ready_instead_of_keyerror(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def delta():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    gate = threading.Event()
    monkeypatch.setattr(service, "_rebuild_now", _blocking_rebuild(service, gate))
    service.enable_reusing_index(str(project))

    # During the first build a missing file is "still indexing", not a hard miss.
    res = service.file_summary("main.py")
    assert res["format"] == "auto_index_not_ready"
    assert res["index_status"]["ready"] is False

    gate.set()
    assert service.background is not None
    assert service.background.wait(10.0)
    res2 = service.file_summary("main.py")
    assert res2["format"] == "auto_index_file_summary_full"


def test_stale_point_lookup_miss_returns_pending(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def old_symbol():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    (project / "new.py").write_text("def new_symbol():\n    return 2\n", encoding="utf-8")
    (project / "main.py").write_text("def new_main_symbol():\n    return 3\n", encoding="utf-8")
    gate = threading.Event()
    monkeypatch.setattr(service, "_rebuild_now", _blocking_rebuild(service, gate))
    service.rebuild()

    missing_file = service.file_summary("new.py")
    assert missing_file["format"] == "auto_index_not_ready"
    assert missing_file["index_status"]["stale"] is True

    missing_symbol = service.symbol_body("main.py", "new_main_symbol")
    assert missing_symbol["format"] == "auto_index_not_ready"
    assert missing_symbol["index_status"]["ready"] is True

    gate.set()
    assert service.background is not None
    assert service.background.wait(10.0)
    assert service.file_summary("new.py")["format"] == "auto_index_file_summary_full"
    assert service.symbol_body("main.py", "new_main_symbol")["format"] == "auto_index_symbol_body_full"


def test_background_rebuild_uses_captured_root_after_root_switch(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "first.py").write_text("def first_only():\n    return 1\n", encoding="utf-8")
    (second / "second.py").write_text("def second_only():\n    return 2\n", encoding="utf-8")

    service = AutoIndexService()
    gate = threading.Event()
    real = service._rebuild_now

    def _block_first_root(indexer=None, context=None):
        assert context is not None
        if context.root == first.resolve():
            gate.wait(5.0)
        return real(indexer, context)

    monkeypatch.setattr(service, "_rebuild_now", _block_first_root)
    service.enable_reusing_index(str(first))
    first_background = service.background
    assert first_background is not None

    service.enable(str(second), rebuild=True)
    assert service.root_path == second.resolve()
    assert service.resolve_path("second.py")["items"]
    assert not service.resolve_path("first.py")["items"]

    gate.set()
    assert first_background.wait(10.0)
    assert service.root_path == second.resolve()
    assert service.resolve_path("second.py")["items"]
    assert not service.resolve_path("first.py")["items"]
