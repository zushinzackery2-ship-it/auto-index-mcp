"""A generation published between initial enable and cache creation closes admission."""
import time

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.domain.errors import ServiceError


def test_purge_between_enable_check_and_attachment_creates_no_cache(tmp_path, write_file, make_service, monkeypatch):
    root = tmp_path / "project"
    write_file(root / "main.py", "def item():\n    return 1\n")
    first = make_service(root)
    candidate = AutoIndexService(index_root=tmp_path / "external")
    control, start = candidate.project.control, candidate.project.control.start
    active = []

    def freeze_then_start(*args):
        active.append(control.store.begin(root, time.monotonic() + 5))
        return start(*args)

    monkeypatch.setattr(control, "start", freeze_then_start)
    try:
        try:
            candidate.enable(str(root), refresh_embedder=False)
        except ServiceError as error:
            assert error.code == "busy"
        else:
            raise AssertionError("enable crossed the purge admission barrier")
        assert not candidate.index_root.exists()
        assert candidate.project.session_lease is None
        assert (first.index_root / "index.db").exists()
    finally:
        for record, owner in active:
            control.store.update(record, phase="interrupted")
            owner.release()
        candidate.disable()


def test_overlapping_purge_returns_owner_operation_id(tmp_path, write_file, make_service):
    child = tmp_path / "child"
    write_file(child / "main.py", "def item():\n    return 1\n")
    service = make_service(child)
    store = service.project.control.store
    record, owner = store.begin(tmp_path, time.monotonic() + 5)
    try:
        result = service.purge()
        assert result["status"] == "busy" and result["operation_id"] == record["operation_id"], result
        assert result["removed_indexes"] == 0
    finally:
        store.update(record, phase="interrupted")
        owner.release()
