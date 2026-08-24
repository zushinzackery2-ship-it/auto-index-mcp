"""Centralized registry of every index this tool has created.

Indexes live inside the projects they describe (``<root>/.auto-index-mcp``),
so once a project is finished or deleted its index can easily be forgotten
and left behind. The registry keeps one small JSON file per user
(``registry.json`` under :func:`registry_directory`) recording where every
index was created, so ``auto-index-mcp list`` / ``clean`` can find and remove
them later without crawling the whole disk.

Each index directory additionally carries two markers written on creation:

* ``marker.json`` - proves the directory belongs to this tool and names the
  project root it indexes; ``clean`` refuses to delete a directory that
  cannot be attributed to us through this marker or the index metadata.
* ``CACHEDIR.TAG`` - the standard cache-directory tag so backup tools that
  honor the convention skip the index automatically.

Every mutating operation here is best-effort: registry bookkeeping must
never break ``enable``/``build``, so IO and parse failures are swallowed and
reported as a ``False`` return instead of an exception.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import __version__
from .core.config import INDEX_VERSION, registry_directory
from .indexing.build_lock import BuildLock
from .indexing.locator import INDEX_DIR_NAME
from .indexing.metadata_reader import DEFAULT_METADATA_READER

REGISTRY_FILE_NAME = "registry.json"
REGISTRY_VERSION = 1
TOOL_NAME = "auto-index-mcp"
MARKER_FILE_NAME = "marker.json"
CACHEDIR_TAG_NAME = "CACHEDIR.TAG"
# Standard cache-directory tag signature; the first 43 bytes are fixed by the
# spec (https://bford.info/cachedir/), everything after the newline is free.
CACHEDIR_TAG_CONTENT = (
    "Signature: 8a477f597d28d172789f06886806cdb5\n"
    "# This directory holds a code index created by auto-index-mcp.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)

# Refreshing last_attached_at on every enable would rewrite the registry file
# constantly under agent polling; once an hour is plenty for cleanup decisions.
TOUCH_INTERVAL_SECONDS = 3600.0

# The only files clean() will ever delete. Anything else in the directory is
# not ours, stays put, and keeps the directory from being rmdir'ed.
KNOWN_INDEX_FILES = (
    "index.db",
    "index.db-wal",
    "index.db-shm",
    "index.db-journal",
    "embeddings.db",
    "embeddings.db-wal",
    "embeddings.db-shm",
    "embeddings.db-journal",
    "index.build.lock",
    "embeddings.build.lock",
    MARKER_FILE_NAME,
    CACHEDIR_TAG_NAME,
)

_BUILD_LOCK_NAMES = ("index.build.lock", "embeddings.build.lock")
_BUILD_LOCK_STALE_SECONDS = 120.0


def registry_key(root: Path | str) -> str:
    """Stable registry key for a project root.

    Resolved POSIX form; case-folded on Windows so ``D:\\Work`` and
    ``d:/work`` land on the same entry.
    """
    key = Path(root).resolve().as_posix()
    if os.name == "nt":
        key = key.casefold()
    return key


class IndexRegistry:
    """JSON-file-backed registry of known index directories."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    def directory(self) -> Path:
        # Resolved per call so AUTO_INDEX_REGISTRY_DIR set after construction
        # (pytest monkeypatch, embedding hosts) still takes effect.
        return self._directory or registry_directory()

    def path(self) -> Path:
        return self.directory() / REGISTRY_FILE_NAME

    # ---- mutations (best-effort, never raise) ------------------------------

    def register(
        self,
        root: Path | str,
        index_dir: Path | str,
        source: str,
        ephemeral: bool = False,
    ) -> bool:
        """Upsert an entry for ``root``; preserves ``created_at`` on update."""
        try:
            now = time.time()
            key = registry_key(root)
            data = self._read()
            existing = data["entries"].get(key) or {}
            data["entries"][key] = {
                "root": str(Path(root).resolve()),
                "index_dir": str(Path(index_dir).resolve()),
                "created_at": existing.get("created_at", now),
                "last_attached_at": now,
                "tool_version": __version__,
                "index_version": INDEX_VERSION,
                "source": source,
                "ephemeral": bool(ephemeral),
            }
            self._write(data)
            return True
        except Exception:  # noqa: BLE001 - bookkeeping must not break enable
            return False

    def touch(self, root: Path | str) -> bool:
        """Refresh ``last_attached_at``, at most once per hour per entry."""
        try:
            key = registry_key(root)
            data = self._read()
            entry = data["entries"].get(key)
            if entry is None:
                return False
            now = time.time()
            last = float(entry.get("last_attached_at") or 0.0)
            if now - last < TOUCH_INTERVAL_SECONDS:
                return True
            entry["last_attached_at"] = now
            self._write(data)
            return True
        except Exception:  # noqa: BLE001
            return False

    def unregister(self, root: Path | str) -> bool:
        try:
            key = registry_key(root)
            data = self._read()
            if data["entries"].pop(key, None) is None:
                return False
            self._write(data)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- queries ------------------------------------------------------------

    def entries(self) -> dict[str, dict[str, Any]]:
        return self._read()["entries"]

    def is_registered(self, root: Path | str) -> bool:
        try:
            return registry_key(root) in self._read()["entries"]
        except Exception:  # noqa: BLE001
            return False

    def get(self, root: Path | str) -> dict[str, Any] | None:
        return self._read()["entries"].get(registry_key(root))

    def verify(self) -> list[dict[str, Any]]:
        """Every entry with liveness flags, sorted by key.

        ``orphan`` means the entry no longer points at a usable index: either
        the project root or the index directory has disappeared.
        """
        report: list[dict[str, Any]] = []
        for key, entry in sorted(self._read()["entries"].items()):
            root_exists = Path(entry.get("root", "")).is_dir()
            index_dir = Path(entry.get("index_dir", ""))
            index_exists = (index_dir / "index.db").exists()
            report.append(
                {
                    **entry,
                    "key": key,
                    "root_exists": root_exists,
                    "index_exists": index_exists,
                    "orphan": not (root_exists and index_exists),
                }
            )
        return report

    def scan_and_adopt(self, base: Path | str) -> list[dict[str, Any]]:
        """Find existing index databases under ``base`` and register the
        unknown ones (source ``"scan"``). Returns the newly adopted entries."""
        from .indexing.locator import iter_index_databases

        adopted: list[dict[str, Any]] = []
        for db_path in iter_index_databases(Path(base).resolve()):
            index_dir = db_path.parent
            meta = DEFAULT_METADATA_READER.read_metadata(db_path)
            raw_root = meta.get("root")
            root = Path(raw_root) if raw_root else index_dir.parent
            if self.is_registered(root):
                continue
            ephemeral = index_dir.resolve() != (Path(root).resolve() / INDEX_DIR_NAME)
            if self.register(root, index_dir, source="scan", ephemeral=ephemeral):
                entry = self.get(root)
                if entry is not None:
                    adopted.append({**entry, "key": registry_key(root)})
        return adopted

    # ---- persistence ---------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"version": REGISTRY_VERSION, "entries": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
            return {"version": REGISTRY_VERSION, "entries": {}}
        raw.setdefault("version", REGISTRY_VERSION)
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        directory = self.directory()
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f"{REGISTRY_FILE_NAME}.tmp.{os.getpid()}"
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path())


# ---- index-directory markers -------------------------------------------------


def write_index_markers(index_dir: Path | str, root: Path | str) -> None:
    """Drop ``marker.json`` and ``CACHEDIR.TAG`` into a fresh index directory.

    Idempotent and best-effort: existing markers are left untouched and IO
    failures never propagate into enable/build.
    """
    directory = Path(index_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / MARKER_FILE_NAME
        if not marker.exists():
            marker.write_text(
                json.dumps(
                    {
                        "tool": TOOL_NAME,
                        "root": str(Path(root).resolve()),
                        "created_at": time.time(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        tag = directory / CACHEDIR_TAG_NAME
        if not tag.exists():
            tag.write_text(CACHEDIR_TAG_CONTENT, encoding="ascii")
    except OSError:
        pass


def read_index_marker(index_dir: Path | str) -> dict[str, Any] | None:
    try:
        raw = json.loads((Path(index_dir) / MARKER_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


# ---- cleanup helpers -----------------------------------------------------------


def is_safe_to_delete(index_dir: Path | str, expected_root: Path | str) -> bool:
    """Attribution check run before any deletion.

    A directory named ``.auto-index-mcp`` is deletable when either its
    ``marker.json`` or its ``index.db`` metadata names ``expected_root``.
    A directory under any other name (ephemeral override locations) needs the
    explicit marker - index metadata alone is not enough proof of ownership
    for an arbitrarily named path.
    """
    directory = Path(index_dir)
    marker = read_index_marker(directory)
    marker_ok = (
        marker is not None
        and marker.get("tool") == TOOL_NAME
        and _same_root(marker.get("root"), expected_root)
    )
    if directory.name != INDEX_DIR_NAME:
        return marker_ok
    if marker_ok:
        return True
    meta = DEFAULT_METADATA_READER.read_metadata(directory / "index.db")
    return _same_root(meta.get("root"), expected_root)


def build_lock_active(index_dir: Path | str) -> bool:
    """True when another live process appears to be building in this directory."""
    for name in _BUILD_LOCK_NAMES:
        path = Path(index_dir) / name
        if not path.exists():
            continue
        info = BuildLock(path, stale_seconds=_BUILD_LOCK_STALE_SECONDS).state_info()
        if info is None:
            continue
        alive = info.get("holder_alive")
        if alive is True:
            return True
        age = float(info.get("lock_age_seconds") or 0.0)
        if alive is None and age <= _BUILD_LOCK_STALE_SECONDS:
            return True
    return False


def remove_index_dir(index_dir: Path | str) -> tuple[bool, list[str]]:
    """Delete the known index files, then the directory if it ended up empty.

    Returns ``(directory_removed, notes)``. Unknown files are never touched;
    their presence just leaves the directory in place with a note.
    """
    directory = Path(index_dir)
    notes: list[str] = []
    for name in KNOWN_INDEX_FILES:
        try:
            (directory / name).unlink(missing_ok=True)
        except OSError as exc:
            notes.append(f"could not delete {name}: {exc}")
    try:
        leftovers = [entry.name for entry in directory.iterdir()]
    except OSError:
        return False, notes
    if leftovers:
        notes.append(f"kept directory, unknown files remain: {', '.join(sorted(leftovers))}")
        return False, notes
    try:
        directory.rmdir()
    except OSError as exc:
        notes.append(f"could not remove directory: {exc}")
        return False, notes
    return True, notes


def index_dir_size(index_dir: Path | str) -> int:
    total = 0
    try:
        for entry in Path(index_dir).iterdir():
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _same_root(candidate: Any, expected: Path | str) -> bool:
    if not candidate:
        return False
    try:
        return registry_key(Path(str(candidate))) == registry_key(expected)
    except (OSError, ValueError):
        return False
