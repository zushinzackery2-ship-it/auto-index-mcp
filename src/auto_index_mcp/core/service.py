"""Public service facade backed by explicit application coordinators."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..application.context import ProjectContext
from ..application.facade_fields import ContextField
from ..domain.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..domain.ignore_config import IgnoreConfig
from ..search.navigation_format import MAX_PRESENTED_CALLERS
from ..application.lifecycle import LifecycleCoordinator
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
    index_root = ContextField("index_root")
    index_root_override = ContextField("index_root_override")
    store = ContextField("store")
    embedding_store = ContextField("embedding_store")
    embedding_indexer = ContextField("embedding_indexer")
    embedding_background = ContextField("embedding_background")
    background = ContextField("background")
    watcher = ContextField("watcher")
    registry = ContextField("registry")
    semantic_enabled = ContextField("semantic_enabled")
    semantic_auto_start = ContextField("semantic_auto_start")
    embedding_progress = ContextField("embedding_progress")
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
        self.watch = WatchCoordinator(project, self.builds, self.embeddings)
        self.lifecycle = LifecycleCoordinator(project, self.builds, self.ignore, self.embeddings, self.watch, self.state)
        self.navigation = NavigationQueries(project, self.state)
        self.search = SearchQueries(project, self.state)
        self.quality = QualityQueries(project, self.state)
        self.semantic = SemanticQueries(project, self.state, self.embeddings)

    def enable(self, root_path: str, rebuild: bool=True, refresh_embedder: bool=True, source: str='mcp-enable') -> dict[str, Any]:
        return self.lifecycle.enable(root_path, rebuild, refresh_embedder, source)

    def enable_reusing_index(self, root_path: str, rebuild: bool=False, wait_seconds: float=0.0, source: str='mcp-enable') -> dict[str, Any]:
        return self.lifecycle.enable_reusing_index(root_path, rebuild, wait_seconds, source)

    def disable(self) -> dict[str, Any]:
        return self.lifecycle.disable()

    def status(self) -> dict[str, Any]:
        return self.state.status()

    def clear(self, delete_file: bool=False) -> dict[str, Any]:
        return self.lifecycle.clear(delete_file)

    def ignore_config(self) -> IgnoreConfig:
        return self.ignore.ignore_config()

    def runtime_ignore_patterns(self) -> list[str]:
        return self.ignore.runtime_ignore_patterns()

    def auto_ignore_patterns(self) -> list[str]:
        return self.ignore.auto_ignore_patterns()

    def privileged_ignore_patterns(self) -> list[str]:
        return self.ignore.privileged_ignore_patterns()

    def ignore_status(self) -> dict[str, Any]:
        return self.ignore.ignore_status()

    def configure_ignore(self, patterns: list[str] | None=None, mode: str='status', target: str='ignore') -> dict[str, Any]:
        return self.ignore.configure_ignore(patterns, mode, target)

    def add_auto_ignore_patterns(self, patterns: list[str]) -> None:
        return self.ignore.add_auto_ignore_patterns(patterns)

    def replace_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        return self.ignore.replace_ignore_config(config, dirty)

    def ensure_embedding_background(self) -> dict[str, Any]:
        return self.embeddings.ensure_embedding_background()

    def build_timers(self) -> dict[str, Any]:
        return self.state.build_timers()

    def rebuild(self, reuse_if_fresh: bool=False) -> dict[str, Any]:
        return self.builds.rebuild(reuse_if_fresh)

    def rebuild_sync(self, reuse_if_fresh: bool=False) -> dict[str, Any]:
        return self.builds.rebuild_sync(reuse_if_fresh)

    def request_auto_watch_after_build(self) -> None:
        return self.builds.request_auto_watch_after_build()

    def cancel_auto_watch_after_build(self) -> None:
        return self.builds.cancel_auto_watch_after_build()

    def can_reuse_index_for(self, root: Path) -> bool:
        return self.builds.can_reuse_index_for(root)

    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        return self.builds.can_start_auto_watch(result)

    def start_watcher(self, debounce_seconds: float=DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool=False) -> dict[str, Any]:
        return self.watch.start_watcher(debounce_seconds, wait_ready)

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        return self.watch.sync_index_to_filesystem()

    def stop_watcher(self) -> dict[str, Any]:
        return self.watch.stop_watcher()

    def watcher_status(self) -> dict[str, Any]:
        return self.watch.watcher_status()

    def overview(self, limit: int=20) -> dict[str, Any]:
        return self.navigation.overview(limit)

    def tree_get(self, dir: str='', depth: int=2, limit: int=50) -> dict[str, Any]:
        return self.navigation.tree_get(dir, depth, limit)

    def find_files(self, query: str='', dir: str='', languages: list[str] | None=None, limit: int=20, cursor: str | None=None) -> dict[str, Any]:
        return self.navigation.find_files(query, dir, languages, limit, cursor)

    def file_summary(self, path: str) -> dict[str, Any]:
        return self.navigation.file_summary(path)

    def get(self, path: str) -> dict[str, Any]:
        return self.navigation.get(path)

    def file_content(self, path: str) -> str:
        return self.navigation.file_content(path)

    def diff_filesystem(self) -> dict[str, Any]:
        return self.navigation.diff_filesystem()

    def all_files(self) -> list[dict[str, Any]]:
        return self.navigation.all_files()

    def text_search(self, pattern: str, case_sensitive: bool=True, regex: bool=False, limit: int=20, file_pattern: str | None=None, context_lines: int=0, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.search.text_search(pattern, case_sensitive, regex, limit, file_pattern, context_lines, exclude_paths, active_only)

    def symbol_search(self, text: str='', kind: str='', limit: int=20, cursor: str | None=None) -> dict[str, Any]:
        return self.search.symbol_search(text, kind, limit, cursor)

    def symbol_body(self, symbol_name: str, path: str='', line: int=0) -> dict[str, Any]:
        return self.search.symbol_body(symbol_name, path, line)

    def symbol_refs(self, symbol_name: str, path: str='', direction: str='both', limit: int=MAX_PRESENTED_CALLERS) -> dict[str, Any]:
        return self.search.symbol_refs(symbol_name, path, direction, limit)

    def nesting_check(self, max_depth: int=4, languages: list[str] | None=None, limit: int=50, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.quality.nesting_check(max_depth, languages, limit, exclude_paths, active_only)

    def dangling_check(self, include_low_confidence: bool=False, include_tests: bool=False, limit: int=50, exclude_paths: list[str] | None=None, active_only: bool=False) -> dict[str, Any]:
        return self.quality.dangling_check(include_low_confidence, include_tests, limit, exclude_paths, active_only)

    def semantic_search(self, query: str, limit: int=10, min_score: float=0.0) -> dict:
        return self.semantic.semantic_search(query, limit, min_score)

    def embedding_status(self) -> dict:
        return self.semantic.embedding_status()
