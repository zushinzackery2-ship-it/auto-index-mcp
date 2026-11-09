"""Publish project resources atomically and pin them for control readers."""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import threading
import time

from ..domain.errors import NotEnabledError
from ..workspace.view import WorkspaceView

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectBinding:
    root: object
    index_root: object
    store: object
    embedding_store: object
    session: object
    mount_child_indexes: bool
    generation: int

    @contextmanager
    def open_view(self, control):
        view = WorkspaceView(self.store, control=control, mount_child_indexes=self.mount_child_indexes)
        try:
            yield view
        finally:
            view.close()


class BindingOwner:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._binding = None
        self._readers = 0
        self._retiring = False
        self.enabled = False
        self.cancelled = threading.Event()
        self.generation = 0
        self.stop_generation = 0
        self.last_root = None
        self.last_index_root = None
        self.last_mount = False

    @property
    def current(self):
        with self._condition:
            return self._binding

    def require(self):
        binding = self.current
        if binding is None or not self.enabled:
            raise NotEnabledError()
        return binding

    def activation_ticket(self):
        with self._condition:
            return self.stop_generation

    def validate_activation(self, ticket):
        with self._condition:
            if ticket != self.stop_generation:
                logger.info("lifecycle phase=activation-cancelled ticket=%s stop_generation=%s", ticket, self.stop_generation)
                raise InterruptedError("activation cancelled by a concurrent stop")

    def publish(self, root, index_root, store, embeddings, session, mount, ticket):
        with self._condition:
            self.validate_activation(ticket)
            self.generation += 1
            self.last_root, self.last_index_root, self.last_mount = root, index_root, mount
            self._binding = ProjectBinding(root, index_root, store, embeddings, session, mount, self.generation)
            self.enabled = True
            self._retiring = False
            self.cancelled.clear()
            logger.info("lifecycle phase=bound generation=%s root=%s mount=%s", self.generation, root, mount)
            return self._binding

    def is_current(self, binding):
        with self._condition:
            return self.enabled and not self.cancelled.is_set() and self._binding is binding

    def request_stop(self):
        with self._condition:
            self.stop_generation += 1
            self.cancelled.set()
            logger.info("lifecycle phase=quiescing generation=%s", self.generation)

    @contextmanager
    def read(self):
        with self._condition:
            binding = None if self._retiring else self._binding
            if binding is not None:
                self._readers += 1
        try:
            yield binding
        finally:
            if binding is not None:
                with self._condition:
                    self._readers -= 1
                    self._condition.notify_all()

    def retire(self, deadline):
        with self._condition:
            self._retiring = True
            while self._readers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._retiring = False
                    raise TimeoutError("control readers are still using the project binding")
                self._condition.wait(remaining)
            binding = self._binding
            self._binding = None
            self.enabled = False
            logger.info("lifecycle phase=retired generation=%s", self.generation)
            return binding
