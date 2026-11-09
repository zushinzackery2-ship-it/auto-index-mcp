"""Service callers and control callbacks share explicit resource ownership."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest

from auto_index_mcp.runtime.background import BackgroundIndexer


def test_direct_query_holds_resources_until_disable_drains(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    entered, release, stopping = threading.Event(), threading.Event(), threading.Event()
    original_query = service.search.symbol_search
    original_stop = service.stopping.request_stop

    def query(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_query(*args, **kwargs)

    def stop():
        result = original_stop()
        stopping.set()
        return result

    monkeypatch.setattr(service.search, "symbol_search", query)
    monkeypatch.setattr(service.stopping, "request_stop", stop)
    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(service.symbol_search, "target")
        assert entered.wait(5)
        shutdown = pool.submit(service.disable)
        try:
            assert stopping.wait(5)
            assert service.store is not None
            assert not shutdown.done()
        finally:
            release.set()
        assert pending.result(5)["items"][0]["name"] == "target"
        shutdown.result(5)
    assert service.store is None


def test_clear_respects_completion_callback_lifetime(tmp_path, write_file, make_service):
    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def complete(result):
        entered.set()
        assert release.wait(5)

    worker = BackgroundIndexer(lambda current: dict(status="indexed"), on_done=complete)
    service.background = worker
    worker.start()
    try:
        assert entered.wait(5)
        result = service.clear(delete_file=True)
        assert result["status"] == "clear-skipped-background-running"
        assert service.store is not None and service.store.db_path.exists()
    finally:
        release.set()
        worker.join(5)


def test_stopping_atomically_rejects_new_embedding_workers(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    started = []
    monkeypatch.setattr(BackgroundIndexer, "start", lambda self: started.append(self))
    service.stopping.request_stop()
    result = service.embeddings.ensure_embedding_background()
    assert result["status"] == "embedding-cancelled"
    assert started == []


def test_health_reads_do_not_replace_query_view(tmp_path, write_file, make_service, install_embedder):
    from auto_index_mcp.embedding.backend import BagHashEmbedder

    install_embedder(BagHashEmbedder())
    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    if service.embedding_background:
        assert service.embedding_background.wait(5)
    view = service.view
    service.store.update_policy({"runtime_ignore_patterns": ["ignored/**"]})
    service.status()
    assert service.project._view is view


def test_concurrent_rebuild_registers_one_live_worker(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def build(worker, context):
        entered.set()
        assert release.wait(5)
        return dict(status="indexed")

    monkeypatch.setattr(service.builds, "_run_rebuild_locked", build)
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(service.rebuild) for _ in range(2)]
            for future in futures:
                future.result(5)
        assert entered.wait(5)
    finally:
        release.set()
        assert service.background.join(5)


def test_stop_invalidates_activation_already_initializing(tmp_path, write_file, monkeypatch):
    from auto_index_mcp.core.service import AutoIndexService
    from auto_index_mcp.storage.embeddings import EmbeddingStore

    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = AutoIndexService()
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    initialize = EmbeddingStore.initialize
    request_stop = service.stopping.request_stop

    def block(store):
        initialize(store)
        entered.set()
        assert release.wait(5)

    def stop():
        workers = request_stop()
        stopped.set()
        return workers

    monkeypatch.setattr(EmbeddingStore, "initialize", block)
    monkeypatch.setattr(service.stopping, "request_stop", stop)
    with ThreadPoolExecutor(2) as pool:
        activation = pool.submit(service.enable, str(tmp_path))
        assert entered.wait(5)
        shutdown = pool.submit(service.disable)
        try:
            assert stopped.wait(5)
        finally:
            release.set()
        with pytest.raises(InterruptedError, match="concurrent stop"):
            activation.result(5)
        shutdown.result(5)
    assert not service.enabled and service.store is None
    assert not service.project.tasks.accepting
    assert service.project.tasks.busy_operation() is None


def test_busy_clear_restores_watcher(tmp_path, write_file, make_service, monkeypatch):
    from auto_index_mcp.runtime.maintenance import MaintenanceLease

    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    service.start_watcher(wait_ready=True)

    def busy(lease):
        raise TimeoutError("external owner")

    monkeypatch.setattr(MaintenanceLease, "__enter__", busy)
    result = service.clear()
    assert result["status"] == "clear-busy"
    assert service.watcher_status()["running"]
    assert service.project.tasks.accepting


def test_timed_out_retirement_restores_control_read_admission(tmp_path, write_file, make_service):
    import time

    write_file(tmp_path / "code.py", "def target():\n    return 1\n")
    service = make_service(tmp_path)
    owner = service.project.bindings
    with owner.read() as binding:
        with pytest.raises(TimeoutError):
            owner.retire(time.monotonic())
    with owner.read() as restored:
        assert restored is binding
    assert service.status()["enabled"]
