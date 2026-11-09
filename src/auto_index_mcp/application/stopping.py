"""Drain accepted requests before disposing shared lifecycle resources."""
import time

from ..runtime.control.contracts import remaining
from ..runtime.diagnostics import close_logging


class StoppingCoordinator:
    def __init__(self, project, builds, watch, state) -> None:
        self.project, self.builds, self.watch, self.state = project, builds, watch, state

    def request_stop(self) -> list:
        with self.project.tasks.lock:
            self.project.bindings.request_stop()
            return self.project.tasks.cancel()

    def stop(self, deadline=None, keep_control=False) -> dict:
        deadline = deadline if deadline is not None else time.monotonic() + 10.0
        workers = self.request_stop()
        control = self.project.control
        if not keep_control:
            control.close(remaining(deadline))
        self._drain(deadline, control if keep_control else None)
        try:
            workers = self.request_stop()
            self.watch.stop_watcher(deadline=deadline)
            for worker in workers:
                if not worker.join(remaining(deadline)):
                    raise TimeoutError(f"{worker.operation} is still stopping; lifecycle resources retained")
            self.project.tree_progress.clear()
            self.project.tasks.indexer = None
            result = self.state.status()
            self._dispose(deadline)
            return dict(result, enabled=False)
        finally:
            self.project._request_lock.release()

    def _dispose(self, deadline) -> None:
        binding = self.project.bindings.retire(deadline)
        self.project._invalidate_view_cache()
        close_logging(getattr(self.project, "log_path", None))
        self.project.log_path = None
        if binding is not None:
            binding.session.release()

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
