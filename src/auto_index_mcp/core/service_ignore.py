from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ignore_config import IgnoreConfig, clean_patterns
from .ignore_rules import IgnoreRules, ignore_fingerprint
from ..indexing.store import IndexStore

if TYPE_CHECKING:
    from ..indexing.watcher import FileEventWatcher


class ServiceIgnoreMixin:
    if TYPE_CHECKING:
        root_path: Path | None
        store: IndexStore | None
        watcher: FileEventWatcher | None
        enabled: bool
        _ignore_config: IgnoreConfig
        _ignore_config_dirty: bool

        def _invalidate_view_cache(self) -> None: ...
        def watcher_status(self) -> dict[str, Any]: ...

    def ignore_config(self) -> IgnoreConfig:
        return self._ignore_config

    def runtime_ignore_patterns(self) -> list[str]:
        return list(self._ignore_config.patterns)

    def auto_ignore_patterns(self) -> list[str]:
        return list(self._ignore_config.auto_patterns)

    def privileged_ignore_patterns(self) -> list[str]:
        return list(self._ignore_config.privileged_patterns)

    def ignore_status(self) -> dict[str, Any]:
        root = self.root_path or Path.cwd()
        config = self.ignore_config()
        rules = IgnoreRules.from_root(root, config.patterns)
        status = rules.status()
        status["root"] = str(root)
        status["auto_patterns"] = config.auto_patterns
        status["privileged_patterns"] = config.privileged_patterns
        status["fingerprint"] = ignore_fingerprint(
            root,
            config.patterns,
            config.auto_patterns,
            config.privileged_patterns,
        )
        return status

    def configure_ignore(
        self,
        patterns: list[str] | None = None,
        mode: str = "status",
        target: str = "ignore",
    ) -> dict[str, Any]:
        if mode == "status":
            return self.ignore_status()
        config = self._updated_ignore_config(patterns, mode, target)
        self._set_ignore_config(config, dirty=True)
        self._persist_ignore_config_if_ready()
        self._invalidate_view_cache()
        if self.watcher is not None and self.watcher.is_running():
            self.watcher.stop()
            self.watcher = None
        result = self.ignore_status()
        result["requires_rebuild"] = self.enabled
        return result

    def add_auto_ignore_patterns(self, patterns: list[str]) -> None:
        config = self._ignore_config.with_added_auto_patterns(patterns)
        self._set_ignore_config(config, dirty=True)

    def replace_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        self._set_ignore_config(config, dirty)

    def _load_ignore_config_from_store(self) -> None:
        store = self.store
        if store is None:
            return
        if self._ignore_config_dirty:
            self._persist_ignore_config_if_ready()
            return
        metadata = store.get_metadata_map()
        if IgnoreConfig.has_metadata(metadata):
            self._set_ignore_config(IgnoreConfig.from_metadata(metadata), dirty=False)

    def _persist_ignore_config_if_ready(self) -> None:
        store = self.store
        if store is None:
            return
        store.update_metadata(self._ignore_config.to_metadata())
        self._ignore_config_dirty = False

    def _mark_ignore_config_persisted(self) -> None:
        self._ignore_config_dirty = False

    def _set_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        self._ignore_config = config
        self._ignore_config_dirty = dirty

    def _updated_ignore_config(
        self,
        patterns: list[str] | None,
        mode: str,
        target: str,
    ) -> IgnoreConfig:
        if target == "ignore":
            return self._updated_regular_ignore(patterns, mode)
        if target == "privileged":
            return self._updated_privileged_ignore(patterns, mode)
        raise ValueError("target must be one of: ignore, privileged")

    def _updated_regular_ignore(self, patterns: list[str] | None, mode: str) -> IgnoreConfig:
        if mode == "clear":
            return self._ignore_config.with_patterns([]).without_auto_patterns()
        if mode == "replace":
            return self._ignore_config.with_patterns(clean_patterns(patterns)).without_auto_patterns()
        if mode == "add":
            return self._ignore_config.with_added_patterns(patterns or [])
        raise ValueError("mode must be one of: status, add, replace, clear")

    def _updated_privileged_ignore(self, patterns: list[str] | None, mode: str) -> IgnoreConfig:
        if mode == "clear":
            return self._ignore_config.with_privileged_patterns([])
        if mode == "replace":
            return self._ignore_config.with_privileged_patterns(clean_patterns(patterns))
        if mode == "add":
            return self._ignore_config.with_added_privileged_patterns(patterns or [])
        raise ValueError("mode must be one of: status, add, replace, clear")
