"""Public service facade backed by explicit application coordinators."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from ..application.context import ProjectContext
from ..application.facade_fields import ContextField, TaskField
from ..application.requests import operation, request
from ..domain.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..domain.ignore_config import IgnoreConfig
from ..search.navigation_format import MAX_PRESENTED_CALLERS
from ..application.lifecycle import LifecycleCoordinator
from ..application.stopping import StoppingCoordinator
from ..application.purge.coordinator import PurgeCoordinator
from ..runtime.control.client import ControlClient
from ..runtime.control.contracts import ActivationIntent
from ..application.ignore import IgnoreCoordinator
from ..application.embedding import EmbeddingCoordinator
from ..application.status import StatusCoordinator
from ..application.rebuild import RebuildCoordinator
from ..application.watcher import WatchCoordinator
from ..search.navigation import NavigationQueries
from ..search.queries import SearchQueries
from ..quality.queries import QualityQueries
from ..search.semantic import SemanticQueries


class AutoIndexService:
    """Stable entrypoint; each operation delegates to its owning coordinator."""

    enabled = ContextField("enabled")
    root_path = ContextField("root_path")
    fixed_root = ContextField("fixed_root", writable=True)
    mount_child_indexes = ContextField("mount_child_indexes")
    index_root = ContextField("index_root")
    index_root_override = ContextField("index_root_override")
    store = ContextField("store")
    embedding_store = ContextField("embedding_store")
    embedding_background = TaskField("embedding")
    background = TaskField("build")
    watcher = ContextField("watcher")
    registry = ContextField("registry", writable=True)
    semantic_enabled = ContextField("semantic_enabled", writable=True)
    semantic_auto_start = ContextField("semantic_auto_start", writable=True)
    embedding_progress = ContextField("embedding_progress", writable=True)
    last_errors = ContextField("last_errors")
    tree_progress = ContextField("tree_progress")
    log_path = ContextField("log_path")
    view = ContextField("view")
    _request_lock = ContextField("_request_lock")

    def __init__(self, index_root: Path | None = None) -> None:
        project = ProjectContext(index_root)
        self.project = project
        self.ignore = IgnoreCoordinator(project)
        self.state = StatusCoordinator(project)
        self.embeddings = EmbeddingCoordinator(project)
        self.builds = RebuildCoordinator(project, self.ignore, self.embeddings, self.state,
                                         lambda **kwargs: self.watch.start_watcher(**kwargs))
        self.watch = WatchCoordinator(project, self.builds.capture_context, self.builds.run_captured,
                                      self.builds.rebuild_sync, self.embeddings.after_incremental,
                                      self.embeddings.maintain)
        self.stopping = StoppingCoordinator(project, self.builds, self.watch, self.state)
        self.purging = PurgeCoordinator(project)
        project.control = ControlClient(uuid.uuid4().hex, self.stopping.cooperate)
        self.lifecycle = LifecycleCoordinator(project, self.builds, self.ignore, self.embeddings, self.watch,
                                              self.state, self.stopping)
        self.navigation = NavigationQueries(project, self.state)
        self.search = SearchQueries(project, self.state)
        self.quality = QualityQueries(project, self.state)
        self.semantic = SemanticQueries(project, self.state, self.embeddings)

    @property
    def embedding_indexer(self):
        return self.project.embedding_indexer

    @embedding_indexer.setter
    def embedding_indexer(self, indexer):
        with self.project.tasks.lock:
            self.project.tasks.indexer = indexer

    def invoke(self, function, *args, control_plane=False, allow_suspended=False, **kwargs):
        if control_plane:
            return function(*args, **kwargs)
        with request(self.project, activation=allow_suspended, allow_suspended=allow_suspended):
            return function(*args, **kwargs)

    def configure_startup(self, root, mount_child_indexes=False):
        if self.project.bindings.current is not None:
            raise RuntimeError("startup binding can only be configured before enable")
        self.project.bindings.last_root = Path(root).resolve()
        self.project.bindings.last_index_root = self.project._db_path(Path(root), mount_child_indexes).parent
        self.project.bindings.last_mount = mount_child_indexes

    @operation(activation=True)
    def enable(self, root_path: str, rebuild: bool=True, refresh_embedder: bool=True, source: str='mcp-enable',
               activation=ActivationIntent.EXPLICIT, *, mount_child_indexes: bool=False) -> dict[str, Any]:
        with self._request_lock:
            return self.lifecycle.enable(root_path, rebuild, refresh_embedder, source, activation,
                                         mount_child_indexes=mount_child_indexes)

    @operation(activation=True)
    def enable_reusing_index(self, root_path: str, rebuild: bool=False, wait_seconds: float=0.0,
                            source: str='mcp-enable', activation=ActivationIntent.EXPLICIT, *,
                            mount_child_indexes: bool=False) -> dict[str, Any]:
        with self._request_lock:
            return self.lifecycle.enable_reusing_index(root_path, rebuild, wait_seconds, source, activation,
                                                       mount_child_indexes=mount_child_indexes)

    def disable(self) -> dict[str, Any]:
        return self.lifecycle.disable()

    def status(self) -> dict[str, Any]:
        return self.state.status()

    @operation()
    def clear(self, delete_file: bool=False) -> dict[str, Any]:
        return self.lifecycle.clear(delete_file)

    def purge(self, dry_run=False, timeout_seconds=30.0) -> dict[str, Any]:
        return self.purging.run(dry_run, timeout_seconds)

    @operation()
    def ignore_config(self) -> IgnoreConfig:
        return self.ignore.ignore_config()

    @operation()
    def runtime_ignore_patterns(self) -> list[str]:
        return self.ignore.runtime_ignore_patterns()

    @operation()
    def auto_ignore_patterns(self) -> list[str]:
        return self.ignore.auto_ignore_patterns()

    @operation()
    def privileged_ignore_patterns(self) -> list[str]:
        return self.ignore.privileged_ignore_patterns()

    @operation()
    def ignore_status(self) -> dict[str, Any]:
        return self.ignore.ignore_status()

    @operation()
    def configure_ignore(self, patterns: list[str] | None=None, mode: str='status', target: str='ignore') -> dict[str, Any]:
        return self.ignore.configure_ignore(patterns, mode, target)

    @operation()
    def add_auto_ignore_patterns(self, patterns: list[str]) -> None:
        return self.ignore.add_auto_ignore_patterns(patterns)

    @operation()
    def replace_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        return self.ignore.replace_ignore_config(config, dirty)

    @operation()
    def ensure_embedding_background(self) -> dict[str, Any]:
        return self.embeddings.ensure_embedding_background()

    def build_timers(self) -> dict[str, Any]:
        return self.state.build_timers()

    @operation()
    def rebuild(self, reuse_if_fresh: bool=False) -> dict[str, Any]:
        return self.builds.rebuild(reuse_if_fresh)

    @operation()
    def rebuild_sync(self, reuse_if_fresh: bool=False) -> dict[str, Any]:
        with self._request_lock:
            return self.builds.rebuild_sync(reuse_if_fresh)

    @operation()
    def request_auto_watch_after_build(self) -> None:
        return self.builds.request_auto_watch_after_build()

    @operation(activation=True)
    def cancel_auto_watch_after_build(self) -> None:
        return self.builds.cancel_auto_watch_after_build()

    @operation()
    def can_reuse_index_for(self, root: Path) -> bool:
        return self.builds.can_reuse_index_for(root)

    @operation()
    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        return self.builds.can_start_auto_watch(result)

    @operation()
    def start_watcher(self, debounce_seconds: float=DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool=False) -> dict[str, Any]:
        return self.watch.start_watcher(debounce_seconds, wait_ready)

    @operation()
    def sync_index_to_filesystem(self) -> dict[str, Any]:
        return self.watch.sync_index_to_filesystem()

    @operation()
    def stop_watcher(self, deadline=None) -> dict[str, Any]:
        return self.watch.stop_watcher(deadline)

    def watcher_status(self) -> dict[str, Any]:
        return self.watch.watcher_status()

    @operation(query=True)
    def overview(self, limit: int=20) -> dict[str, Any]:
        return self.navigation.overview(limit)

    @operation(query=True)
    def tree_get(self, dir: str='', depth: int=2, limit: int=50) -> dict[str, Any]:
        return self.navigation.tree_get(dir, depth, limit)

    @operation(query=True)
    def find_files(self, query: str='', dir: str='', languages: list[str] | None=None, limit: int=20, cursor: str | None=None) -> dict[str, Any]:
        return self.navigation.find_files(query, dir, languages, limit, cursor)

    @operation(query=True)
    def file_summary(self, path: str) -> dict[str, Any]:
        return self.navigation.file_summary(path)

    @operation(query=True)
    def get(self, path: str) -> dict[str, Any]:
        return self.navigation.get(path)

    @operation(query=True)
    def file_content(self, path: str) -> str:
        return self.navigation.file_content(path)

    @operation(query=True)
    def diff_filesystem(self) -> dict[str, Any]:
        return self.navigation.diff_filesystem()

    @operation(query=True)
    def all_files(self) -> list[dict[str, Any]]:
        return self.navigation.all_files()

    @operation(query=True)
    def text_search(self, pattern: str, case_sensitive: bool=True, regex: bool=False, limit: int=20, file_pattern: str | None=None, context_lines: int=0, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.search.text_search(pattern, case_sensitive, regex, limit, file_pattern, context_lines, exclude_paths, active_only)

    @operation(query=True)
    def symbol_search(self, text: str='', kind: str='', limit: int=20, cursor: str | None=None) -> dict[str, Any]:
        return self.search.symbol_search(text, kind, limit, cursor)

    @operation(query=True)
    def symbol_body(self, symbol_name: str, path: str='', line: int=0) -> dict[str, Any]:
        return self.search.symbol_body(symbol_name, path, line)

    @operation(query=True)
    def symbol_refs(self, symbol_name: str, path: str='', direction: str='both', limit: int=MAX_PRESENTED_CALLERS) -> dict[str, Any]:
        return self.search.symbol_refs(symbol_name, path, direction, limit)

    @operation(query=True)
    def nesting_check(self, max_depth: int=4, languages: list[str] | None=None, limit: int=50, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.quality.nesting_check(max_depth, languages, limit, exclude_paths, active_only)

    @operation(query=True)
    def dangling_check(self, include_low_confidence: bool=False, include_tests: bool=False, limit: int=50, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.quality.dangling_check(include_low_confidence, include_tests, limit, exclude_paths, active_only)

    @operation(query=True)
    def semantic_search(self, query: str, limit: int=10, min_score: float=0.0) -> dict:
        return self.semantic.semantic_search(query, limit, min_score)

    @operation()
    def embedding_status(self) -> dict:
        return self.semantic.embedding_status()
