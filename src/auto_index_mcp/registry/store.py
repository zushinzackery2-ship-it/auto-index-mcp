from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..domain.config import INDEX_VERSION, registry_directory
from ..runtime.leases import BuildLock
from ..indexing.locator import INDEX_DIR_NAME, iter_index_databases
from ..storage.metadata import DEFAULT_METADATA_READER
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

    def register(self, root, index_dir, source: str, ephemeral: bool = False) -> bool:
        def update(data):
            now = time.time()
            key = registry_key(index_dir)
            existing = data["entries"].get(key, dict())
            data["entries"][key] = dict(
                root=str(Path(root).resolve()), index_dir=str(Path(index_dir).resolve()),
                created_at=existing.get("created_at", now), last_attached_at=now,
                tool_version=__version__, index_version=INDEX_VERSION,
                source=source, ephemeral=bool(ephemeral),
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

    def is_registered(self, identity) -> bool:
        return bool(self._keys(self._read(), identity))

    def get(self, identity) -> dict | None:
        data = self._read()
        keys = self._keys(data, identity)
        preferred = registry_key(Path(identity) / INDEX_DIR_NAME)
        key = preferred if preferred in keys else next(iter(keys), None)
        return data["entries"].get(key)

    def verify(self) -> list[dict]:
        report = []
        for key, entry in sorted(self.entries().items()):
            root_exists = Path(entry["root"]).is_dir()
            index_exists = (Path(entry["index_dir"]) / "index.db").is_file()
            report.append(dict(entry, key=key, root_exists=root_exists, index_exists=index_exists,
                               orphan=not (root_exists and index_exists)))
        return report

    def scan_and_adopt(self, base) -> list[dict]:
        adopted = []
        for db_path in iter_index_databases(Path(base).resolve()):
            index_dir = db_path.parent
            meta = DEFAULT_METADATA_READER.read_metadata(db_path)
            root = Path(meta.get("root") or index_dir.parent)
            if self.is_registered(index_dir):
                continue
            ephemeral = index_dir.resolve() != root.resolve() / INDEX_DIR_NAME
            if self.register(root, index_dir, "scan", ephemeral):
                adopted.append(dict(self.get(index_dir), key=registry_key(index_dir)))
        return adopted

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8"))
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
            logger.warning("registry read failed path=%s error=%s", self.path(), exc)
            return dict(version=REGISTRY_VERSION, entries=dict())

    def _write(self, data: dict) -> None:
        directory = self.directory()
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix="registry-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path())
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
