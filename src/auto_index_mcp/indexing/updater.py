from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from ..core.quality_dangling import with_project_quality_findings
from .analysis import resolve_project_callers
from .scanner import SourceScanner
from .snapshot import WatchSnapshot
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes
from .store import IndexStore
from ..core.models import FileRecord, SymbolRecord


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
    def __init__(self, root: Path, store: IndexStore, rebuild: Callable[[], dict[str, Any]]) -> None:
        self.root = root
        self.store = store
        self.rebuild = rebuild

    def apply(self, previous: WatchSnapshot, current: WatchSnapshot) -> dict[str, Any]:
        start = time.time()
        child_added, child_deleted, child_modified = current.child_index_changes(previous)
        if child_added or child_deleted:
            result = self.rebuild()
            result["update_mode"] = "structural-rebuild"
            return result
        if child_modified:
            self.refresh_child_links()
        added, deleted, modified = current.changed_files(previous)
        if not added and not deleted and not modified:
            result = UpdateResult("metadata-refresh", 0, 0, 0, 0, False, 0).to_dict()
            result["child_indexes_modified"] = len(child_modified)
            return result
        stored_records = {item["path"]: _dict_to_record(item) for item in self.store.all_files()}
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
        resolved = with_project_quality_findings(resolve_project_callers(sorted(records.values(), key=lambda item: item.path.lower())))
        rewritten = self._rewrite_changed_records(stored_records, resolved, deleted + unindexed)
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
            record = stored_records.get(path)
            if record is None or (record.size, record.mtime_ns) != current.files.get(path):
                return False
        return all(path not in stored_records for path in deleted)

    def refresh_child_links(self) -> None:
        children = discover_child_indexes(self.root, self.store.db_path)
        self.store.replace_child_indexes(child_indexes_to_dicts(children))

    def _read_changed_files(self, paths: list[str]) -> tuple[list[FileRecord], list[str]]:
        scanner = SourceScanner(str(self.root))
        records = []
        unindexed = []
        for rel in paths:
            try:
                records.append(scanner.read_path(self.root / rel))
            except (OSError, UnicodeDecodeError, ValueError):
                unindexed.append(rel)
        return records, unindexed

    def _rewrite_changed_records(self, before: dict[str, FileRecord], after: list[FileRecord], deleted: list[str]) -> int:
        changed = [record for record in after if before.get(record.path) != record]
        existing_deleted = [path for path in deleted if path in before]
        self.store.delete_files(existing_deleted)
        self.store.replace_files(changed)
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
        symbols=[SymbolRecord(**symbol) for symbol in item["symbols"]],
        quality_findings=item.get("quality_findings", []),
        snippet=item["snippet"],
    )
