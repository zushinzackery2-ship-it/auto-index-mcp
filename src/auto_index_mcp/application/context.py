"""Owns one project lifecycle and its published query view."""

from __future__ import annotations

from .build_context import RebuildContext

import threading
import os
from pathlib import Path
from typing import Any, Callable

from ..runtime.background import BackgroundIndexer
from ..domain.config import project_index_root
from ..domain.ignore_config import IgnoreConfig
from ..workspace.path_normalize import normalize_input_path
from ..indexing.tree_progress import TreeProgress
from ..storage.embeddings import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..storage.index import IndexStore
from ..indexing.watcher import FileEventWatcher
from ..registry import IndexRegistry
from ..domain.errors import NotEnabledError
from ..workspace.view import WorkspaceView

class ProjectContext:
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
        # Reuse child views until a published policy changes.
        self._view: WorkspaceView | None = None
        self._view_policy_generation: int | None = None
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

    @property
    def view(self) -> WorkspaceView:
        """Keep child views while the immutable project policy is unchanged."""
        self._store_context()
        generation = int(self.store.get_metadata_map().get("policy_generation", 0))
        if self._view is None or generation != self._view_policy_generation:
            self._ignore_config = IgnoreConfig.from_metadata(self.store.get_metadata_map())
            self._view = WorkspaceView(
                self._store_context(),
                ignore_patterns=list(self._ignore_config.patterns),
                auto_ignore_patterns=list(self._ignore_config.auto_patterns),
                privileged_patterns=list(self._ignore_config.privileged_patterns),
            )
            self._view_policy_generation = generation
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
            raise NotEnabledError()

    def _require_store(self) -> None:
        if self.store is None:
            raise NotEnabledError()

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

    def _context_is_current(self, context: RebuildContext) -> bool:
        return (
            self.enabled
            and self.root_path is not None
            and self.index_root is not None
            and self.store is context.store
            and (self.root_path.resolve(), self.index_root.resolve()) == context.key
        )
