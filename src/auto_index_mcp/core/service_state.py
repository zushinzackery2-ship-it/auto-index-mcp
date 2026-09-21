"""Shared state and cross-mixin contracts for AutoIndexService.

Every service mixin inherits :class:`ServiceBase`, so the mutable service
state is declared and initialized in exactly one place and the methods one
mixin expects from another are spelled out as explicit stubs instead of
per-file ``TYPE_CHECKING`` re-declarations. ``AutoIndexService`` composes the
mixins; Python's MRO guarantees every concrete implementation is found before
the stubs below.
"""

from __future__ import annotations

import threading
import os
import time
from pathlib import Path
from typing import Any, Callable

from .background_indexer import BackgroundIndexer
from .config import project_index_root
from .ignore_config import IgnoreConfig
from .path_normalize import normalize_input_path
from .tree_progress import TreeProgress
from ..embedding.embedding_store import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..indexing.store import IndexStore
from ..indexing.watcher import FileEventWatcher
from ..registry import IndexRegistry
from ..workspace.view import WorkspaceView

# View cache TTL - must be <= WorkspaceView cache TTL for consistency
_VIEW_CACHE_TTL_SECONDS = 0.5


class ServiceBase:
    """Single home of the service state plus the smallest shared helpers."""

    def __init__(self, index_root: Path | None = None) -> None:
        self._request_lock = threading.RLock()
        self.index_root_override = index_root
        self.index_root: Path | None = index_root
        self.root_path: Path | None = None
        self.enabled = False
        self.last_errors: list[str] = []
        self.store: IndexStore | None = None
        self.watcher: FileEventWatcher | None = None
        self.embedding_indexer: SymbolEmbedder | None = None
        self.embedding_background: BackgroundIndexer | None = None
        # Per-symbol vectors live in a standalone embeddings.db, decoupled from
        # the navigable index.db (separate write lock, schema, lifecycle).
        self.embedding_store: EmbeddingStore | None = None
        # Background full-tree rebuild runner. Stays None when the project is
        # enabled against a reusable existing index (fast path) or while idle.
        self.background: BackgroundIndexer | None = None
        # Set when a watcher should auto-start as soon as a background build ends.
        self._auto_watch_after_build = False
        self._auto_watch_context_key: tuple[Path, Path] | None = None
        self._background_context_key: tuple[Path, Path] | None = None
        # Timing of the most recent synchronous full rebuild (watcher-driven or
        # programmatic enable). Background builds report timing through their own
        # BackgroundIndexer; this captures the path that has no runner handle.
        self._last_index_build: dict[str, Any] | None = None
        self._ignore_config = IgnoreConfig()
        self._ignore_config_dirty = False
        self.tree_progress = TreeProgress()
        # Use a shared view with TTL-based caching for better incremental update responsiveness
        self._view: WorkspaceView | None = None
        self._view_created_at: float = 0.0
        # Guards check-then-act on the embedding background handle so two callers
        # never spawn duplicate embedding passes over the same store.
        self._embedding_lock = threading.Lock()
        self._watcher_lock = threading.RLock()
        # CLI/pre-build hooks: opt out of semantic vectors entirely, and an
        # optional (done, total, reused) callback for embedding progress bars.
        semantic_mode = os.environ.get("AUTO_INDEX_SEMANTIC_MODE", "on-demand").strip().lower()
        if semantic_mode not in ("on-demand", "eager", "off"):
            raise ValueError("AUTO_INDEX_SEMANTIC_MODE must be on-demand, eager, or off")
        self.semantic_enabled = semantic_mode != "off"
        self.semantic_auto_start = semantic_mode == "eager"
        self.embedding_progress: Callable[[int, int, int], None] | None = None
        # User-level registry of created index directories; every call on it
        # is best-effort so bookkeeping can never break enable/build.
        self.registry = IndexRegistry()

    # ---- shared infrastructure -------------------------------------------------

    @property
    def view(self) -> WorkspaceView:
        """Get WorkspaceView with TTL-based caching."""
        self._store_context()
        now = time.time()
        if self._view is None or (now - self._view_created_at) > _VIEW_CACHE_TTL_SECONDS:
            self._view = WorkspaceView(
                self._store_context(),
                ignore_patterns=self.runtime_ignore_patterns(),
                auto_ignore_patterns=self.auto_ignore_patterns(),
                privileged_patterns=self.privileged_ignore_patterns(),
            )
            self._view_created_at = now
        return self._view

    def _invalidate_view_cache(self) -> None:
        """Invalidate the cached view after mutations."""
        self._view = None

    def _db_path(self, root: Path) -> Path:
        index_root = self.index_root_override or project_index_root(root)
        return index_root / "index.db"

    def _require_ready(self) -> None:
        self._store_context()
        if self.root_path is None:
            raise RuntimeError("auto-index root is not configured")

    def _require_store(self) -> None:
        if self.store is None:
            raise RuntimeError("auto-index is not enabled")

    def _store_context(self) -> IndexStore:
        self._require_store()
        assert self.store is not None
        return self.store

    def _ready_context(self) -> tuple[Path, IndexStore]:
        self._require_ready()
        assert self.root_path is not None
        assert self.store is not None
        return self.root_path, self.store

    def _lookup_indexed_file(self, path: str) -> tuple[str, Any]:
        """Tolerant point lookup for AI-supplied paths.

        Normalizes the input (backslashes, absolute-inside-root), then falls
        back to a case-insensitive scan, then to a unique path-suffix match so
        a bare file name resolves when unambiguous.
        Returns ``(resolved_path, FileLookup)``.
        """
        normalized = normalize_input_path(path, self.root_path)
        lookup = self.view.get_file(normalized)
        if lookup.item is not None:
            return normalized, lookup
        lowered = normalized.lower()
        if lowered:
            suffix_hits: list[str] = []
            for header in self.view.file_headers():
                candidate = header["path"].lower()
                if candidate == lowered:
                    return header["path"], self.view.get_file(header["path"])
                if candidate.endswith("/" + lowered):
                    suffix_hits.append(header["path"])
            if len(suffix_hits) == 1:
                return suffix_hits[0], self.view.get_file(suffix_hits[0])
        return normalized, lookup

    def _path_candidates(self, path: str, limit: int = 5) -> list[str]:
        """Closest indexed paths for a failed lookup, best match first."""
        needle = normalize_input_path(path, self.root_path).lower()
        if not needle:
            return []
        name = needle.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0]
        scored: list[tuple[int, str]] = []
        for header in self.view.file_headers():
            candidate = header["path"].lower()
            candidate_name = header["name"].lower()
            if candidate == needle or candidate.endswith("/" + needle):
                score = 0
            elif candidate_name == name:
                score = 1
            elif name and name in candidate_name:
                score = 2
            elif stem and stem in candidate:
                score = 3
            else:
                continue
            scored.append((score, header["path"]))
        scored.sort(key=lambda pair: (pair[0], len(pair[1]), pair[1]))
        return [item for _, item in scored[:limit]]

    # ---- cross-mixin contracts ---------------------------------------------------
    # Concrete implementations live in the named mixin (or AutoIndexService) and
    # win over these stubs through the MRO. They exist so each mixin can call
    # into its siblings without re-declaring signatures per file.

    def status(self) -> dict[str, Any]:  # AutoIndexService
        raise NotImplementedError

    def _background_status(self) -> dict[str, Any]:  # ServiceIndexStateMixin
        raise NotImplementedError

    def _index_status(self) -> dict[str, Any] | None:  # ServiceIndexStateMixin
        raise NotImplementedError

    def _with_index_status(self, result: dict[str, Any]) -> dict[str, Any]:  # ServiceIndexStateMixin
        raise NotImplementedError

    def _not_ready_response(self) -> dict[str, Any] | None:  # ServiceIndexStateMixin
        raise NotImplementedError

    def ignore_config(self) -> IgnoreConfig:  # ServiceIgnoreMixin
        raise NotImplementedError

    def runtime_ignore_patterns(self) -> list[str]:  # ServiceIgnoreMixin
        raise NotImplementedError

    def auto_ignore_patterns(self) -> list[str]:  # ServiceIgnoreMixin
        raise NotImplementedError

    def privileged_ignore_patterns(self) -> list[str]:  # ServiceIgnoreMixin
        raise NotImplementedError

    def replace_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:  # ServiceIgnoreMixin
        raise NotImplementedError

    def _mark_ignore_config_persisted(self) -> None:  # ServiceIgnoreMixin
        raise NotImplementedError

    def rebuild_sync(self, reuse_if_fresh: bool = False) -> dict[str, Any]:  # ServiceRebuildMixin
        raise NotImplementedError

    def start_watcher(
        self,
        debounce_seconds: float = 0.25,
        wait_ready: bool = False,
    ) -> dict[str, Any]:  # ServiceWatcherMixin
        raise NotImplementedError

    def ensure_embedding_background(self) -> dict[str, Any]:  # ServiceEmbeddingMixin
        raise NotImplementedError

    def _embed_after_full_rebuild(
        self,
        root: Path,
        store: IndexStore | None = None,
        indexer: SymbolEmbedder | None = None,
    ) -> dict[str, Any] | None:  # ServiceEmbeddingMixin
        raise NotImplementedError

    def _create_embedding_indexer(
        self,
        embedding_store: EmbeddingStore | None = None,
    ) -> SymbolEmbedder | None:  # ServiceEmbeddingMixin
        raise NotImplementedError

    def _embed_after_incremental(
        self,
        root: Path,
        store: IndexStore,
        previous: Any,
        current: Any,
        result: dict[str, Any],
    ) -> None:  # ServiceEmbeddingMixin
        raise NotImplementedError
