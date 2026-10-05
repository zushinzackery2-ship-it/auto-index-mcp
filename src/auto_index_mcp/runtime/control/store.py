"""Strict, atomically published control state outside disposable caches."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
import uuid

from ...domain.config import control_directory
from ...domain.errors import ServiceError
from ...cache.identity import directory_identity
from ..leases import BuildLock
from ..budget import bounded_timeout, deadline_scope
from ..files.json import atomic_json, read_json
from ..files.gate import publication_gate
from .contracts import ACTIVE_PHASES, PROTOCOL_VERSION, intersects, remaining, within


class ControlStore:
    def __init__(self, directory=None) -> None:
        self.directory = Path(directory or control_directory()).resolve()
        self.path = self.directory / "state.json"
        self._read_lock = threading.RLock()
        self._transaction_lock = threading.RLock()
        self._fingerprint = None
        self._cached = None

    def snapshot(self) -> dict:
        with deadline_scope(time.monotonic() + 5.0), self._local_gate(self._read_lock), publication_gate(self.path):
            try:
                stat = self.path.stat()
                fingerprint = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if fingerprint != self._fingerprint:
                    raw = read_json(self.path)
                    if (not isinstance(raw, dict) or raw.get("version") != PROTOCOL_VERSION
                            or not isinstance(raw.get("scopes"), dict) or not isinstance(raw.get("generation"), int)):
                        raise ValueError("invalid control state schema")
                    for operation_id, record in raw["scopes"].items():
                        if (not isinstance(record, dict) or record.get("operation_id") != operation_id
                                or not isinstance(record.get("generation"), int) or not record.get("root")
                                or not Path(record["root"]).is_absolute()
                                or record.get("phase") not in (*ACTIVE_PHASES, "purged", "already-purged",
                                                               "busy", "refused", "stop-timeout", "partial", "interrupted")
                                or not isinstance(record.get("deadline"), (int, float))):
                            raise ValueError("invalid control operation")
                    self._cached, self._fingerprint = raw, fingerprint
                return copy.deepcopy(self._cached)
            except FileNotFoundError:
                self._cached, self._fingerprint = None, None
                return dict(version=PROTOCOL_VERSION, generation=0, scopes=dict())
            except (OSError, ValueError, TypeError) as exc:
                raise ServiceError("control-unavailable", str(exc), control_path=str(self.path)) from exc

    @contextmanager
    def _local_gate(self, lock):
        if not lock.acquire(timeout=bounded_timeout(5.0)):
            raise ServiceError("control-unavailable", "workspace control metadata is busy")
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def transaction(self, timeout=5.0):
        with deadline_scope(time.monotonic() + timeout), self._local_gate(self._transaction_lock):
            gate = BuildLock(self.directory / "control.lock", resolve_cache=False)
            if not gate.acquire(timeout):
                raise ServiceError("busy", "workspace control transaction is busy")
            try:
                yield
            finally:
                gate.release()

    def mutate(self, fn, timeout=5.0):
        with self.transaction(timeout):
            state = self.snapshot()
            result = fn(state)
            atomic_json(self.path, state)
            return result

    def begin(self, root: Path, deadline: float, members=()):
        acquired = []
        def publish(state):
            reusable = None
            for record in state["scopes"].values():
                if not intersects(root, record["root"]):
                    continue
                if record["phase"] in ACTIVE_PHASES and self.owner_alive(record):
                    raise ServiceError("busy", "overlapping workspace purge is active",
                                       operation_id=record["operation_id"])
                if (Path(record["root"]) == root and record["phase"] not in ("purged", "already-purged")
                        and (reusable is None or record["generation"] > reusable["generation"])):
                    reusable = record
            operation_id = reusable["operation_id"] if reusable else uuid.uuid4().hex
            operation = self.directory / "operations" / operation_id
            owner = BuildLock(operation / "owner.lock", resolve_cache=False)
            if not owner.try_acquire():
                raise ServiceError("busy", "workspace purge operation is owned", operation_id=operation_id)
            acquired.append(owner)
            state["generation"] += 1
            record = dict(operation_id=operation_id, root=str(root), root_identity=directory_identity(root),
                          generation=state["generation"], phase="quiescing", deadline=deadline,
                          pid=os.getpid(), protocol=PROTOCOL_VERSION, updated_at=time.time(),
                          paused_roots=sorted(set(member["root"] for member in members)))
            for previous in list(state["scopes"].values()):
                if Path(previous["root"]) == root:
                    del state["scopes"][previous["operation_id"]]
            state["scopes"][operation_id] = record
            return record, owner
        try:
            return self.mutate(publish, remaining(deadline))
        except BaseException:
            for owner in acquired:
                owner.release()
            raise

    def update(self, record, **changes) -> None:
        def publish(state):
            current = state["scopes"].get(record["operation_id"])
            if current is None or current["generation"] != record["generation"]:
                raise ServiceError("control-changed", "workspace control generation changed")
            current.update(changes, updated_at=time.time())
        self.mutate(publish, remaining(record["deadline"]))

    def records_for(self, root):
        return [record for record in self.snapshot()["scopes"].values() if intersects(root, record["root"])]

    def suspensions_for(self, root):
        return [record for record in self.records_for(root)
                if within(root, record["root"]) or str(Path(root).resolve()) in record.get("paused_roots", [])]

    def pause_root(self, record, root) -> None:
        def publish(state):
            current = state["scopes"].get(record["operation_id"])
            if current is None or current["generation"] != record["generation"]:
                raise ServiceError("control-changed", "workspace control generation changed")
            current["paused_roots"] = sorted(set((*current.get("paused_roots", []), root)))
        self.mutate(publish, remaining(record["deadline"]))

    def operation_path(self, operation_id: str) -> Path:
        if len(operation_id) != 32 or any(char not in "0123456789abcdef" for char in operation_id):
            raise ValueError("invalid operation_id")
        return self.directory / "operations" / operation_id

    def owner_alive(self, record) -> bool:
        path = self.operation_path(record["operation_id"]) / "owner.lock"
        return BuildLock(path, resolve_cache=False).state_info() is not None

    def members(self) -> list[dict]:
        result = []
        base = self.directory / "instances"
        if not base.exists():
            return result
        for path in base.glob("*/member.json"):
            if BuildLock(path.parent / "instance.lock", resolve_cache=False).state_info() is None:
                continue
            try:
                row = read_json(path)
            except (OSError, ValueError) as exc:
                raise ServiceError("control-unavailable", f"invalid participant: {path}") from exc
            if (not isinstance(row, dict) or row.get("protocol") != PROTOCOL_VERSION
                    or not row.get("instance_id") or not row.get("root") or not row.get("registry_dir")
                    or not isinstance(row.get("indexes"), list)):
                raise ServiceError("unsupported-participant", f"invalid participant: {path}")
            result.append(row)
        return result
