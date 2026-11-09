"""Own every background thread through work, callback and cancellation."""
import logging
import threading

logger = logging.getLogger(__name__)


class TaskOwner:
    def __init__(self):
        self.lock = threading.RLock()
        self.watcher_lock = threading.RLock()
        self._accepting = False
        self._workers = []
        self._latest = {}
        self.watcher = None
        self.indexer = None
        self.auto_watch_key = None
        self.build_key = None

    def open(self):
        with self.lock:
            self._accepting = True

    @property
    def accepting(self):
        with self.lock:
            return self._accepting

    def latest(self, operation):
        with self.lock:
            return self._latest.get(operation)

    def register(self, operation, worker, *, start=True):
        with self.lock:
            if not self._accepting:
                logger.info("task phase=rejected operation=%s reason=quiescing", operation)
                return False
            self._workers = [item for item in self._workers if item.is_alive()]
            self._workers.append(worker)
            self._latest[operation] = worker
            if start:
                worker.start()
            logger.info("task phase=registered operation=%s id=%s", operation, worker.operation_id)
            return True

    def cancel(self):
        with self.lock:
            self._accepting = False
            self.auto_watch_key = None
            for worker in self._workers:
                worker.cancelled.set()
            return list(self._workers)

    def reset(self):
        with self.lock:
            if any(worker.is_alive() for worker in self._workers):
                raise RuntimeError("cannot reset tasks before their threads exit")
            self._workers.clear()
            self._latest.clear()
            self.indexer = None
            self.build_key = None
            self.auto_watch_key = None

    def start_watcher(self, watcher):
        with self.lock:
            if not self._accepting:
                return False
            self.watcher = watcher
            watcher.start()
            return True

    def pause(self):
        with self.lock:
            busy = self.busy_operation()
            if busy is None:
                self._accepting = False
            return busy

    def busy_operation(self):
        with self.lock:
            return next((worker.operation for worker in self._workers if worker.is_alive()), None)
