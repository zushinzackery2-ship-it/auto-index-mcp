"""One receiver per service, independent of its filesystem watcher."""
import logging
import os
from pathlib import Path
import threading

from ..leases import BuildLock
from .admission import Admission
from .contracts import ACTIVE_PHASES, PROTOCOL_VERSION, within
from .store import ControlStore
from ..files.json import atomic_json

logger = logging.getLogger(__name__)


class ControlClient:
    def __init__(self, instance_id, on_stop) -> None:
        self.instance_id = instance_id
        self.store = ControlStore()
        self.admission = Admission(self.store)
        self.on_stop = on_stop
        self.root = None
        self.index_dir = None
        self.registry_dir = None
        self.suspension = None
        self.last_error = None
        self.closed = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._mounts = dict()
        self._observed = dict()
        self._member_lock = None
        self._member_path = self.store.directory / "instances" / instance_id / "member.json"

    def start(self, root, index_dir, registry_dir) -> None:
        with self._lock:
            self.root, self.index_dir = str(Path(root).resolve()), str(Path(index_dir).resolve())
            self.registry_dir = str(Path(registry_dir).resolve())
            if self._thread is None or not self._thread.is_alive():
                self.closed.clear()
                member_lock = BuildLock(self._member_path.parent / "instance.lock", resolve_cache=False)
                if not member_lock.try_acquire():
                    raise TimeoutError("instance registration is still owned")
                self._member_lock = member_lock
                try:
                    self._publish()
                except BaseException:
                    member_lock.release()
                    self._member_lock = None
                    raise
                self._thread = threading.Thread(target=self._receive, name="auto-index-control", daemon=True)
                self._thread.start()
            else:
                self._publish()

    def check(self, root=None) -> None:
        self.admission.check(root or self.root)

    def mount(self, directory, root) -> None:
        with self._lock:
            key = str(Path(directory).resolve())
            row = self._mounts.setdefault(key, dict(root=str(Path(root).resolve()), index_dir=key, count=0))
            row["count"] += 1
            if self._member_lock is not None:
                self._publish()

    def unmount(self, directory) -> None:
        with self._lock:
            key = str(Path(directory).resolve())
            row = self._mounts.get(key)
            if row is not None:
                row["count"] -= 1
                if row["count"] <= 0:
                    self._mounts.pop(key)
            if self._member_lock is not None:
                self._publish()

    def _publish(self) -> None:
        atomic_json(self._member_path, dict(instance_id=self.instance_id, protocol=PROTOCOL_VERSION,
                    pid=os.getpid(), root=self.root, registry_dir=self.registry_dir,
                    indexes=[dict(root=self.root, index_dir=self.index_dir), *self._mounts.values()]))

    def relevant(self, record) -> bool:
        with self._lock:
            roots = [self.root, *(row["root"] for row in self._mounts.values())]
        return self.root in record.get("paused_roots", []) or any(root and within(root, record["root"]) for root in roots)

    def _receive(self) -> None:
        while not self.closed.wait(0.25):
            try:
                for record in sorted(self.store.snapshot()["scopes"].values(), key=lambda row: row["generation"]):
                    self._observe(record)
            except Exception as exc:
                if self.last_error != str(exc):
                    logger.exception("control receiver failed instance=%s", self.instance_id)
                self.last_error = str(exc)

    def _observe(self, record) -> None:
        operation_id, generation = record["operation_id"], record["generation"]
        if not self.relevant(record):
            return
        if record["phase"] in ACTIVE_PHASES and not self.store.owner_alive(record):
            self.store.update(record, phase="interrupted")
            record = dict(record, phase="interrupted")
        if (self._observed.get(operation_id, 0) >= generation
                or self.admission.permits.get(operation_id) == generation):
            return
        self.suspension = record
        if self.root not in record.get("paused_roots", []):
            self.store.pause_root(record, self.root)
        before = self.inventory()
        try:
            self.on_stop(record)
            status, error = "stopped", None
        except Exception as exc:
            status, error = "stop-failed", str(exc)
            self.last_error = error
        self.ack(record, status, before, error)
        self._observed[operation_id] = generation

    def inventory(self) -> list[dict]:
        with self._lock:
            return [dict(root=self.root, index_dir=self.index_dir), *(dict(row) for row in self._mounts.values())]

    def ack(self, record, status, indexes, error=None) -> None:
        directory = self.store.operation_path(record["operation_id"]) / "acks" / str(record["generation"])
        atomic_json(directory / f"{self.instance_id}.json", dict(instance_id=self.instance_id,
                    pid=os.getpid(), status=status, indexes=indexes, error=error))

    def close(self, timeout=5.0) -> None:
        self.closed.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("control receiver is still stopping")
        with self._lock:
            if self._member_lock is not None:
                self._member_lock.release()
                self._member_lock = None
