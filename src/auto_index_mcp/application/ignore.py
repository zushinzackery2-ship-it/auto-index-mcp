from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.ignore_config import IgnoreConfig, clean_patterns
from ..domain.ignore_rules import IgnoreRules, ignore_fingerprint
from ..runtime.leases import BuildLock


class IgnoreCoordinator:
    def __init__(self, project) -> None:
        self.project = project

    def ignore_config(self) -> IgnoreConfig:
        return self.project._ignore_config

    def runtime_ignore_patterns(self) -> list[str]:
        return list(self.project._ignore_config.patterns)

    def auto_ignore_patterns(self) -> list[str]:
        return list(self.project._ignore_config.auto_patterns)

    def privileged_ignore_patterns(self) -> list[str]:
        return list(self.project._ignore_config.privileged_patterns)

    def ignore_status(self) -> dict[str, Any]:
        root = self.project.root_path or Path.cwd()
        config = self.ignore_config()
        metadata = self.project.store.get_metadata_map() if self.project.store is not None else {}
        known_paths = metadata.get("ignore_files", [".gitignore"])
        rules = IgnoreRules.from_root(root, config.patterns)
        status = rules.status(known_paths)
        status["root"] = str(root)
        status["auto_patterns"] = list(config.auto_patterns)
        status["privileged_patterns"] = list(config.privileged_patterns)
        status["fingerprint"] = ignore_fingerprint(
            root,
            config.patterns,
            config.auto_patterns,
            config.privileged_patterns,
            known_paths,
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
        lock = BuildLock(self.project.store.db_path.parent / "index.build.lock") if self.project.store else None
        if lock is not None and not lock.acquire(5):
            raise TimeoutError("ignore configuration is busy with an index writer")
        try:
            self._load_ignore_config_from_store()
            config = self._updated_ignore_config(patterns, mode, target)
            if self.project.store is not None:
                self.project.store.update_policy(config.to_metadata())
            self._set_ignore_config(config, dirty=self.project.store is None)
        finally:
            if lock is not None:
                lock.release()
        self.project._invalidate_view_cache()
        result = self.ignore_status()
        result["requires_rebuild"] = self.project.enabled
        return result

    def add_auto_ignore_patterns(self, patterns: list[str]) -> None:
        config = self.project._ignore_config.with_added_auto_patterns(patterns)
        self._set_ignore_config(config, dirty=True)

    def replace_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        self._set_ignore_config(config, dirty)

    def _load_ignore_config_from_store(self) -> None:
        store = self.project.store
        if store is None:
            return
        if self.project._ignore_config_dirty:
            self._persist_ignore_config_if_ready()
            return
        metadata = store.get_metadata_map()
        if IgnoreConfig.has_metadata(metadata):
            self._set_ignore_config(IgnoreConfig.from_metadata(metadata), dirty=False)

    def _persist_ignore_config_if_ready(self) -> None:
        store = self.project.store
        if store is None:
            return
        store.update_policy(self.project._ignore_config.to_metadata())
        self.project._ignore_config_dirty = False

    def _mark_ignore_config_persisted(self) -> None:
        self.project._ignore_config_dirty = False

    def _set_ignore_config(self, config: IgnoreConfig, dirty: bool) -> None:
        self.project._ignore_config = config
        self.project._ignore_config_dirty = dirty

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
            return self.project._ignore_config.with_patterns([]).without_auto_patterns()
        if mode == "replace":
            return self.project._ignore_config.with_patterns(clean_patterns(patterns)).without_auto_patterns()
        if mode == "add":
            return self.project._ignore_config.with_added_patterns(patterns or [])
        raise ValueError("mode must be one of: status, add, replace, clear")

    def _updated_privileged_ignore(self, patterns: list[str] | None, mode: str) -> IgnoreConfig:
        if mode == "clear":
            return self.project._ignore_config.with_privileged_patterns([])
        if mode == "replace":
            return self.project._ignore_config.with_privileged_patterns(clean_patterns(patterns))
        if mode == "add":
            return self.project._ignore_config.with_added_privileged_patterns(patterns or [])
        raise ValueError("mode must be one of: status, add, replace, clear")
