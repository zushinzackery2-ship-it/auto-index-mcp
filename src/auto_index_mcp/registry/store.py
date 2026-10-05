from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..domain.config import INDEX_VERSION, registry_directory
from ..runtime.leases import BuildLock
from ..runtime.files.json import atomic_json, read_json
from ..domain.errors import ServiceError
from ..indexing.locator import INDEX_DIR_NAME
from ..cache.identity import directory_identity
from .markers import read_index_marker
from .paths import REGISTRY_FILE_NAME, REGISTRY_VERSION, TOUCH_INTERVAL_SECONDS, registry_key

logger = logging.getLogger(__name__)


class IndexRegistry:
    """Directory-keyed registry; each read/modify/publish is one leased transaction."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    def directory(self) -> Path:
        return self._directory or registry_directory()

    def path(self) -> Path:
        return self.directory() / REGISTRY_FILE_NAME

    def register(self, root, index_dir, source: str, ephemeral: bool = False, attached: bool = True) -> bool:
        def update(data):
            now = time.time()
            key = registry_key(index_dir)
            existing = data["entries"].get(key, dict())
            marker = read_index_marker(index_dir) or dict()
            if not existing and not attached and marker.get("index_id"):
                existing = next((entry for entry in data["entries"].values()
                                 if entry.get("index_id") == marker["index_id"]), dict())
            data["entries"][key] = dict(
                root=str(Path(root).resolve()), index_dir=str(Path(index_dir).resolve()),
                created_at=existing.get("created_at", marker.get("created_at", now)),
                last_attached_at=now if attached else existing.get("last_attached_at", marker.get("created_at", now)),
                tool_version=__version__, index_version=INDEX_VERSION,
                source=source, ephemeral=bool(ephemeral),
                root_identity=directory_identity(Path(root)), index_id=marker.get("index_id"),
                state="ready" if (Path(index_dir) / "index.db").is_file() else "residual",
            )
            return True
        return self._mutate(update)

    def touch(self, identity) -> bool:
        def update(data):
            keys = self._keys(data, identity)
            now = time.time()
            for key in keys:
                entry = data["entries"][key]
                if now - float(entry.get("last_attached_at") or 0) >= TOUCH_INTERVAL_SECONDS:
                    entry["last_attached_at"] = now
            return bool(keys)
        return self._mutate(update)

    def unregister(self, identity, expected_attached_at=None) -> bool:
        def update(data):
            keys = self._keys(data, identity)
            removed = False
            for key in keys:
                entry = data["entries"][key]
                if expected_attached_at is None or entry.get("last_attached_at") == expected_attached_at:
                    del data["entries"][key]
                    removed = True
            return removed
        return self._mutate(update)

    def _mutate(self, operation) -> bool:
        lock = BuildLock(self.directory() / "registry.lock")
        try:
            if not lock.acquire(5):
                raise TimeoutError("registry transaction is busy")
            data = self._read()
            before = json.dumps(data, sort_keys=True)
            result = operation(data)
            if before != json.dumps(data, sort_keys=True):
                self._write(data)
            return result
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("registry mutation failed path=%s pid=%s error=%s", self.path(), os.getpid(), exc)
            return False
        finally:
            lock.release()

    @staticmethod
    def _keys(data, identity) -> list[str]:
        key = registry_key(identity)
        if key in data["entries"]:
            return [key]
        return [k for k, entry in data["entries"].items() if registry_key(entry["root"]) == key]

    def entries(self) -> dict[str, dict[str, Any]]:
        return self._read()["entries"]

    def strict_entries(self) -> dict[str, dict[str, Any]]:
        return self._read(strict=True)["entries"]

    def is_registered(self, identity) -> bool:
        return bool(self._keys(self._read(), identity))

    def get(self, identity) -> dict | None:
        data = self._read()
        keys = self._keys(data, identity)
        preferred = registry_key(Path(identity) / INDEX_DIR_NAME)
        key = preferred if preferred in keys else next(iter(keys), None)
        return data["entries"].get(key)

    def verify(self, roots=None) -> list[dict]:
        from .projection import projected_entries
        report = []
        entries = projected_entries(self.entries(), roots or [])
        for key, entry in sorted(entries.items()):
            root_exists = Path(entry["root"]).is_dir()
            index_exists = (Path(entry["index_dir"]) / "index.db").is_file()
            report.append(dict(entry, key=key, root_exists=root_exists, index_exists=index_exists,
                               orphan=not (root_exists and index_exists)))
        return report

    def scan_and_adopt(self, base) -> list[dict]:
        from .adoption import scan_and_adopt
        return scan_and_adopt(self, base)

    def reconcile_root(self, root) -> None:
        from .adoption import reconcile_root
        reconcile_root(self, root)

    def set_state(self, index_dir, state) -> bool:
        def update(data):
            entry = data["entries"].get(registry_key(index_dir))
            if entry is None:
                return False
            entry["state"] = state
            return True
        return self._mutate(update)

    def prune_moved_aliases(self, index_dir) -> bool:
        def update(data):
            key = registry_key(index_dir)
            entry = data["entries"].get(key) or dict()
            identity = entry.get("index_id")
            if not identity:
                return False
            obsolete = [other for other, value in data["entries"].items() if other != key
                        and value.get("index_id") == identity and not Path(value["index_dir"]).exists()]
            for other in obsolete:
                del data["entries"][other]
            return bool(obsolete)
        return self._mutate(update)

    def _read(self, strict=False) -> dict:
        try:
            raw = read_json(self.path())
            entries = raw["entries"]
            if not isinstance(entries, dict):
                raise ValueError("entries must be an object")
            migrated = dict()
            for entry in entries.values():
                if isinstance(entry, dict) and entry.get("root") and entry.get("index_dir"):
                    migrated[registry_key(entry["index_dir"])] = entry
            return dict(version=REGISTRY_VERSION, entries=migrated)
        except FileNotFoundError:
            return dict(version=REGISTRY_VERSION, entries=dict())
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if strict:
                raise ServiceError("registry-unavailable", str(exc), registry_path=str(self.path())) from exc
            logger.warning("registry read failed path=%s error=%s", self.path(), exc)
            return dict(version=REGISTRY_VERSION, entries=dict())

    def _write(self, data: dict) -> None:
        atomic_json(self.path(), data)
