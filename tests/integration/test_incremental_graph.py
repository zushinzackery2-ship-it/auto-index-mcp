from __future__ import annotations

import random

from auto_index_mcp.core.service import AutoIndexService
from tests.fixtures.graph_oracle import resolve_project_callers
from auto_index_mcp.indexing.scanner import SourceScanner


def test_single_file_update_does_not_materialize_the_project(tmp_path, monkeypatch):
    for index in range(20):
        (tmp_path / f"f{index}.py").write_text(f"def value{index}():\n    return 1\n", encoding="utf-8")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))
        monkeypatch.setattr(service.store, "all_files", lambda: (_ for _ in ()).throw(AssertionError("full load")))
        (tmp_path / "f0.py").write_text("def changed():\n    return value1()\n", encoding="utf-8")
        result = service.sync_index_to_filesystem()
        assert result["rewritten"] == 1
        assert service.symbol_refs("value1")["items"][0]["callers"] == ["f0.py::changed"]
    finally:
        service.disable()


def test_incremental_graph_matches_full_resolution_after_name_collisions(tmp_path):
    source = tmp_path / "caller.py"
    source.write_text("def run():\n    return target()\n", encoding="utf-8")
    service = AutoIndexService()
    service.semantic_enabled = False
    randomizer = random.Random(42)
    try:
        service.enable(str(tmp_path))
        for _ in range(24):
            path = tmp_path / f"target{randomizer.randrange(4)}.py"
            if path.exists() and randomizer.randrange(2):
                path.unlink()
            else:
                path.write_text("def target():\n    return 1\n", encoding="utf-8")
            service.sync_index_to_filesystem()
            records = SourceScanner(str(tmp_path)).scan().records
            oracle = resolve_project_callers(records)
            expected = [(r.path, s.name, s.called_by) for r in oracle for s in r.symbols]
            actual = [(r["path"], s["name"], s["called_by"]) for r in service.store.all_files() for s in r["symbols"]]
            assert actual == expected
    finally:
        service.disable()


def test_module_references_keep_callbacks_and_reexports_alive(tmp_path):
    (tmp_path / "code.py").write_text(
        "def _handler():\n    return 1\n\nhandlers = [_handler]\n", encoding="utf-8",
    )
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))
        assert service.symbol_refs("_handler")["items"][0]["callers"] == ["<module>"]
        findings = service.dangling_check()["findings"]
        assert not any(f.get("symbol") == "_handler" for f in findings)
    finally:
        service.disable()
