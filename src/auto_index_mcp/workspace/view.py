from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage.index import IndexStore
from ..languages.text_decode import read_text_file
from .discovery import read_index_metadata
from .safety import ensure_relative_to
from .filesystem import diff_local
from .snapshot import WorkspaceSnapshot
from ..runtime.locking.session import SessionLease

@dataclass(frozen=True)
class FileLookup:
    item: dict[str, Any] | None


class WorkspaceView:
    def __init__(
        self,
        store: IndexStore,
        visited_db_paths: set[Path] | None = None,
        ignore_patterns: list[str] | None = None,
        auto_ignore_patterns: list[str] | None = None,
        privileged_patterns: list[str] | None = None,
        control=None,
        mount_child_indexes: bool = False,
    ) -> None:
        self.store = store
        self.control = control
        self.mount_child_indexes = mount_child_indexes
        self.visited_db_paths = {path.resolve() for path in visited_db_paths or set()}
        self.visited_db_paths.add(store.db_path.resolve())
        self.ignore_patterns = ignore_patterns or []
        self.auto_ignore_patterns = auto_ignore_patterns or []
        self.privileged_patterns = privileged_patterns or []
        self._active_children: list[dict[str, Any]] | None = None
        self._child_stores: dict[str, IndexStore] = {}
        self._child_views: dict[str, WorkspaceView] = {}
        self._child_sessions: dict[str, SessionLease] = {}
        self._source_generation = None
        self._query_local = threading.local()

    @contextmanager
    def read_scope(self):
        current = getattr(self._query_local, "snapshot", None)
        if current is not None:
            yield current
            return
        snapshot = WorkspaceSnapshot(self)
        self._query_local.snapshot = snapshot
        try:
            yield snapshot
        finally:
            self._query_local.snapshot = None
            snapshot.close()

    def reference_scope(self):
        with self.read_scope() as snapshot:
            return snapshot.scope()

    def close(self) -> None:
        for view in self._child_views.values():
            view.close()
        for session in self._child_sessions.values():
            session.release()
        self._child_sessions.clear()
        self._child_views.clear()
        self._child_stores.clear()

    def all_files(self) -> list[dict[str, Any]]:
        with self.read_scope() as snapshot:
            return snapshot.files()

    def child_indexes(self) -> list[dict[str, Any]]:
        return self._active_child_indexes()

    def iter_sources(self, root: Path):
        yield "", root, self.store
        for child in self._active_child_indexes():
            for prefix, source_root, store in self._child_view(child).iter_sources(Path(child["root"])):
                yield "/".join(part for part in (child["path"], prefix) if part), source_root, store

    def file_headers(self) -> list[dict[str, Any]]:
        with self.read_scope() as snapshot:
            return snapshot.headers()

    def search_targets(self) -> list[dict[str, Any]]:
        with self.read_scope() as snapshot:
            return snapshot.headers(targets=True)

    def query_symbols(self, text: str, kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.read_scope() as snapshot:
            return snapshot.query_symbols(text, kind, limit, offset)

    def query_symbols_relaxed(self, subtokens: list[str], kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.read_scope() as snapshot:
            return snapshot.query_symbols(subtokens, kind, limit, offset, relaxed=True)

    def get_file(self, path: str) -> FileLookup:
        with self.read_scope() as snapshot:
            files = snapshot.files(path)
            return FileLookup(files[0] if files else None)

    def read_text(self, root: Path, path: str) -> str:
        lookup = self.get_file(path)
        if lookup.item:
            return self.read_indexed_text(root, lookup.item)
        target = ensure_relative_to(root / path, root, path)
        return read_text_file(target)

    def read_indexed_text(self, root: Path, item: dict[str, Any]) -> str:
        source_root = Path(item.get("source_root") or root).resolve()
        source_path = item.get("source_path", item["path"])
        target = ensure_relative_to(source_root / source_path, source_root, item["path"])
        return read_text_file(target)


    def diff_filesystem(self, root: Path) -> dict[str, list[str]]:
        children = self._active_child_indexes()
        added, deleted, changed = diff_local(self, root, children)
        for child in children:
            child_diff = self._child_view(child).diff_filesystem(Path(child["root"]))
            added.extend(f"{child['path']}/{path}" for path in child_diff["added"])
            deleted.extend(f"{child['path']}/{path}" for path in child_diff["deleted"])
            changed.extend(f"{child['path']}/{path}" for path in child_diff["changed"])
        return {"added": sorted(added), "deleted": sorted(deleted), "changed": sorted(changed)}

    def _child_store(self, child: dict[str, Any]) -> IndexStore:
        db_path = Path(child["db_path"])
        if not db_path.exists() or not read_index_metadata(db_path):
            raise KeyError(f"child index is not available: {child['path']}")
        key = str(db_path.resolve())
        if key not in self._child_stores:
            session = SessionLease(db_path.parent, root=child["root"], control=self.control)
            session.acquire()
            self._child_sessions[key] = session
            self._child_stores[key] = IndexStore(db_path, session=session)
        return self._child_stores[key]

    def _child_view(self, child: dict[str, Any]) -> "WorkspaceView":
        key = str(Path(child["db_path"]).resolve())
        if key not in self._child_views:
            self._child_views[key] = WorkspaceView(
                self._child_store(child),
                self.visited_db_paths,
                self.ignore_patterns,
                self.auto_ignore_patterns,
                self.privileged_patterns,
                self.control,
                mount_child_indexes=True,
            )
        return self._child_views[key]

    def _active_child_indexes(self) -> list[dict[str, Any]]:
        if not self.mount_child_indexes:
            return []
        metadata = self.store.get_metadata_map()
        if metadata.get("mount_child_indexes", True) is not True:
            return []
        generation = (metadata.get("index_epoch"), metadata.get("source_generation"), metadata.get("policy_generation"))
        if generation != self._source_generation:
            self._source_generation = generation
            self._active_children = None
        if self._active_children is not None:
            return self._active_children
        children = []
        for child in self.store.child_indexes():
            db_path = Path(child["db_path"]).resolve()
            if db_path.exists() and db_path not in self.visited_db_paths and read_index_metadata(db_path):
                children.append(child)
        self._active_children = children
        return children
