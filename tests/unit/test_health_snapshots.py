"""Control responses retain task identities across concurrent removal."""
from types import SimpleNamespace
import threading

from auto_index_mcp.application.binding import BindingOwner
from auto_index_mcp.application.health import embedding_status, watcher_status
from auto_index_mcp.runtime.background import BackgroundIndexer


def test_watcher_handle_is_read_once():
    class Project:
        reads = 0

        @property
        def watcher(self):
            self.reads += 1
            if self.reads > 1:
                return None
            return SimpleNamespace(status=lambda: dict(running=True, role="owner", ready=True, change_count=1))

    project = Project()
    assert watcher_status(project)["running"]
    assert project.reads == 1


def test_embedding_worker_is_captured_before_owner_reset():
    class Project:
        reads = 0
        embedding_indexer = None
        semantic_enabled = True
        bindings = BindingOwner()
        tasks = SimpleNamespace(lock=threading.RLock())

        @property
        def embedding_background(self):
            self.reads += 1
            return BackgroundIndexer(lambda current: {}) if self.reads == 1 else None

    project = Project()
    result = embedding_status(project)
    assert result["embedding_background"]["state"] == "idle"
    assert project.reads == 1
