from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..domain.ignore_config import IgnoreConfig
from ..indexing.snapshot import snapshot_from_index, take_watch_snapshot, update_watch_snapshot
from ..indexing.updater import IndexUpdater


class WatchSession:
    """Each snapshot and write carries the policy generation it was made with."""

    def __init__(self, root, store, rebuild, after_update=None) -> None:
        self.root, self.store = root, store
        self.rebuild = rebuild
        self.after_update = after_update

    def _policy(self):
        metadata = self.store.get_metadata_map()
        return IgnoreConfig.from_metadata(metadata), int(metadata.get("policy_generation", 0))

    def baseline(self):
        return snapshot_from_index(self.root, self.store.file_headers(), self.store.child_indexes())

    def snapshot(self):
        config, generation = self._policy()
        children = [Path(child["root"]) for child in self.store.child_indexes()]
        current = take_watch_snapshot(self.root, children, self.store.db_path, list(config.patterns))
        return replace(current, policy_generation=generation)

    def update_snapshot(self, previous, paths):
        config, generation = self._policy()
        rules_changed = any(path.name == ".gitignore" for path in paths)
        if generation != previous.policy_generation or rules_changed:
            return replace(self.snapshot(), rules_changed=rules_changed)
        children = [Path(child["root"]) for child in self.store.child_indexes()]
        current = update_watch_snapshot(self.root, previous, paths, children, self.store.db_path, list(config.patterns))
        return replace(current, policy_generation=generation)

    def apply(self, previous, current):
        config, _ = self._policy()
        updater = IndexUpdater(self.root, self.store, self.rebuild, list(config.patterns),
                               list(config.auto_patterns), list(config.privileged_patterns), current.policy_generation)
        result = updater.apply(previous, current)
        if self.after_update is not None:
            self.after_update(previous, current, result)
        return result
