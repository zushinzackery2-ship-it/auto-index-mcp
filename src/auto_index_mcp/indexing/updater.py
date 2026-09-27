from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Any

from .scanner import SourceScanner
from .snapshot import WatchSnapshot
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes
from ..storage.index import IndexStore
from ..runtime.leases import BuildLock
from ..domain.models import FileRecord, SymbolRecord


@dataclass(frozen=True)
class UpdateResult:
    status: str
    added: int
    modified: int
    deleted: int
    rewritten: int
    rebuild: bool
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "added": self.added,
            "modified": self.modified,
            "deleted": self.deleted,
            "rewritten": self.rewritten,
            "rebuild": self.rebuild,
            "elapsed_seconds": self.elapsed_seconds,
        }


class IndexUpdater:
    def __init__(
        self,
        root: Path,
        store: IndexStore,
        rebuild: Callable[[], dict[str, Any]],
        ignore_patterns: list[str] | None = None,
        auto_ignore_patterns: list[str] | None = None,
        privileged_patterns: list[str] | None = None,
        policy_generation: int | None = None,
    ) -> None:
        self.root = root
        self.store = store
        self.rebuild = rebuild
        self.ignore_patterns = ignore_patterns or []
        self.auto_ignore_patterns = auto_ignore_patterns or []
        self.privileged_patterns = privileged_patterns or []
        self.policy_generation = policy_generation

    def apply(self, previous: WatchSnapshot, current: WatchSnapshot) -> dict[str, Any]:
        lock = BuildLock(self.store.db_path.parent / "index.build.lock")
        if not lock.try_acquire():
            raise RuntimeError("index writer is busy; pending filesystem changes retained")
        try:
            return self._apply_locked(previous, current, lock)
        finally:
            lock.release()

    def _apply_locked(self, previous, current, lock):
        start = time.time()
        metadata = self.store.get_metadata_map()
        generation = int(metadata.get("policy_generation", 0))
        if self.policy_generation is not None and generation != self.policy_generation:
            raise RuntimeError("policy generation changed; pending filesystem changes retained")
        child_added, child_deleted, child_modified = current.child_index_changes(previous)
        added, deleted, modified = current.changed_files(previous)
        manifest_changed = any(path.lower().endswith(".vcxproj") for path in added + deleted + modified)
        policy_changed = generation != int(metadata.get("applied_policy_generation", 0))
        if child_added or child_deleted or manifest_changed or policy_changed or current.rules_changed:
            lock.release()
            result = self.rebuild()
            result["update_mode"] = "structural-rebuild"
            return result
        if child_modified:
            self.refresh_child_links()
        if not added and not deleted and not modified:
            result = UpdateResult("metadata-refresh", 0, 0, 0, 0, False, 0).to_dict()
            result["child_indexes_modified"] = len(child_modified)
            return result
        stored_records = {item["path"]: _dict_to_record(item) for item in self.store.get_files(added + modified + deleted)}
        if self._db_reflects_changes(stored_records, added, modified, deleted, current):
            # Another process sharing this index already wrote these filesystem
            # changes. Skip the redundant read+resolve+write; the watcher still
            # realigns its snapshot to the live tree on return.
            result = UpdateResult("shared-index-current", len(added), len(modified), len(deleted), 0, False, round(time.time() - start, 3)).to_dict()
            result["child_indexes_modified"] = len(child_modified)
            return result
        records = dict(stored_records)
        for path in deleted:
            records.pop(path, None)
        changed_records, unindexed = self._read_changed_files(added + modified)
        for path in unindexed:
            records.pop(path, None)
        for record in changed_records:
            records[record.path] = record
        active_records = [replace(record, active_source=self.store.is_active_source(record.path))
                          if record.language in ("c", "cpp") else record for record in records.values()]
        rewritten = self._rewrite_changed_records(stored_records, active_records, deleted + unindexed)
        result = UpdateResult(
            status="incremental",
            added=len(added),
            modified=len(modified),
            deleted=len(deleted),
            rewritten=rewritten,
            rebuild=False,
            elapsed_seconds=round(time.time() - start, 3),
        ).to_dict()
        result["child_indexes_modified"] = len(child_modified)
        return result

    def _db_reflects_changes(
        self,
        stored_records: dict[str, FileRecord],
        added: list[str],
        modified: list[str],
        deleted: list[str],
        current: WatchSnapshot,
    ) -> bool:
        for path in added + modified:
            if path in current.dirty_files:
                return False
            record = stored_records.get(path)
            if record is None or (record.size, record.mtime_ns) != current.files.get(path):
                return False
        return all(path not in stored_records for path in deleted)

    def refresh_child_links(self) -> None:
        children = discover_child_indexes(
            self.root,
            self.store.db_path,
            ignore_patterns=self.ignore_patterns,
        )
        self.store.replace_child_indexes(child_indexes_to_dicts(children))

    def _read_changed_files(self, paths: list[str]) -> tuple[list[FileRecord], list[str]]:
        scanner = SourceScanner(
            str(self.root),
            extra_excludes=self.ignore_patterns,
            auto_excludes=self.auto_ignore_patterns,
            privileged_patterns=self.privileged_patterns,
        )
        records = []
        unindexed = []
        for rel in paths:
            try:
                records.append(scanner.read_path(self.root / rel))
            except (FileNotFoundError, ValueError):
                unindexed.append(rel)
        return records, unindexed

    def _rewrite_changed_records(self, before: dict[str, FileRecord], after: list[FileRecord], deleted: list[str]) -> int:
        changed = [record for record in after if before.get(record.path) != record]
        existing_deleted = [path for path in deleted if path in before]
        self.store.apply_files(changed, existing_deleted)
        return len(changed) + len(existing_deleted)


def _dict_to_record(item: dict[str, Any]) -> FileRecord:
    return FileRecord(
        path=item["path"],
        name=item["name"],
        parent=item["parent"],
        extension=item["extension"],
        language=item["language"],
        size=item["size"],
        mtime_ns=item["mtime_ns"],
        sha1=item["sha1"],
        line_count=item["line_count"],
        imports=item["imports"],
        symbols=[SymbolRecord(**dict(symbol, called_by=[])) for symbol in item["symbols"]],
        quality_findings=item.get("quality_findings", []),
        active_source=item.get("active_source", True),
        snippet=item["snippet"],
        module_refs=item.get("module_refs", []),
        analysis_kind=item.get("analysis_kind", "heuristic"),
    )
