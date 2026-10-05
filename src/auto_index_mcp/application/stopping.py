"""Drain accepted requests before disposing shared lifecycle resources."""
import time

from ..runtime.control.contracts import remaining
from ..runtime.diagnostics import close_logging


class StoppingCoordinator:
    def __init__(self, project, builds, watch, state) -> None:
        self.project, self.builds, self.watch, self.state = project, builds, watch, state

    def request_stop(self) -> list:
        self.project.quiescing = True
        self.project.lifecycle_cancelled.set()
        self.builds.cancel_auto_watch_after_build()
        with self.project._embedding_lock:
            workers = [worker for worker in (self.project.background, self.project.embedding_background)
                       if worker is not None]
            for worker in workers:
                worker.cancelled.set()
        return workers

    def stop(self, deadline=None, keep_control=False) -> dict:
        deadline = deadline if deadline is not None else time.monotonic() + 10.0
        workers = self.request_stop()
        control = self.project.control
        if not keep_control:
            control.close(remaining(deadline))
        self._drain(deadline, control if keep_control else None)
        try:
            self.project.enabled = False
            self.watch.stop_watcher(deadline=deadline)
            for worker in workers:
                if not worker.join(remaining(deadline)):
                    raise TimeoutError(f"{worker.operation} is still stopping; lifecycle resources retained")
            self.project.tree_progress.clear()
            self.project.embedding_indexer = None
            try:
                return self.state.status()
            finally:
                self._dispose()
        finally:
            self.project._request_lock.release()

    def _dispose(self) -> None:
        self.project._invalidate_view_cache()
        close_logging(getattr(self.project, "log_path", None))
        self.project.log_path = None
        if self.project.session_lease is not None:
            self.project.session_lease.release()
            self.project.session_lease = None
        self.project.store = None
        self.project.embedding_store = None

    def _drain(self, deadline, control) -> None:
        while True:
            if control is not None and control.closed.is_set():
                raise InterruptedError("control receiver was closed")
            if self.project._request_lock.acquire(timeout=min(0.05, remaining(deadline))):
                return
            if remaining(deadline) <= 0:
                raise TimeoutError("accepted service request is still draining")

    def cooperate(self, record) -> None:
        self.stop(deadline=record["deadline"], keep_control=True)
