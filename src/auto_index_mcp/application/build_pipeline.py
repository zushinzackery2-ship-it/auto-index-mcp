from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..domain.config import INDEX_VERSION, PARSER_VERSION
from ..runtime.background import (
    BackgroundIndexer,
    PHASE_ANALYZING,
    PHASE_DONE,
    PHASE_EMBEDDING,
    PHASE_SCANNING,
    PHASE_WRITING,
    STATE_DONE,
)
from ..domain.ignore_config import exact_path_pattern
from .build_context import RebuildContext
from .ignore_metadata import config_ignore_metadata
from ..runtime.timefmt import iso_time
from ..indexing.tree_progress import TreeProgress
from ..indexing.active_sources import annotate_active_sources, discover_active_source_paths
from ..indexing.scanner import SourceScanner
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes


class BuildPipeline:
    def __init__(self, project, ignore, embeddings) -> None:
        self.project, self.ignore, self.embeddings = project, ignore, embeddings

    def run(
        self,
        indexer: BackgroundIndexer | None = None,
        context: RebuildContext | None = None,
    ) -> dict[str, Any]:
        assert context is not None
        root = context.root
        store = context.store
        start = time.time()
        try:
            metadata = store.get_metadata_map()
            compatible = metadata.get("version") == INDEX_VERSION and metadata.get("parser_version") == PARSER_VERSION
            existing = {item["path"]: item for item in store.all_files()} if compatible else {}
        except Exception:
            existing = {}
        if indexer is not None:
            indexer.set_phase(PHASE_SCANNING)
        ignore_config = context.ignore_config
        ignore_patterns = ignore_config.patterns
        privileged_patterns = ignore_config.privileged_patterns
        progress = TreeProgress()
        progress.start(root)
        if self.project._context_is_current(context):
            self.project.tree_progress = progress
        children = discover_child_indexes(
            root,
            store.db_path,
            ignore_patterns=ignore_patterns,
        )
        boundary_roots = [Path(child.root) for child in children]
        try:
            scan = SourceScanner(
                str(root),
                extra_excludes=ignore_patterns,
                auto_excludes=ignore_config.auto_patterns,
                privileged_patterns=privileged_patterns,
                existing_records=existing,
                boundary_roots=boundary_roots,
                tree_progress=progress,
                cancelled=indexer.cancelled if indexer is not None else None,
            ).scan()
        finally:
            progress.finish()
        existing.clear()
        ignore_config = ignore_config.with_auto_patterns([exact_path_pattern(path) for path in scan.oversized_paths])
        if self.project._context_is_current(context):
            self.ignore.replace_ignore_config(ignore_config, dirty=True)
        if indexer is not None:
            indexer.check_cancelled()
            indexer.set_phase(PHASE_ANALYZING)
        active_paths = discover_active_source_paths(root) if any(r.language in ("c", "cpp") for r in scan.records) else set()
        records = annotate_active_sources(root, scan.records, active_paths)
        scan = replace(scan, records=[])
        if indexer is not None:
            indexer.check_cancelled()
            indexer.set_phase(PHASE_WRITING)
        children_dicts = child_indexes_to_dicts(children)
        total_file_count = len(records) + sum(child.file_count for child in children)
        if not self.project._context_is_current(context):
            return dict(status="cancelled", reason="project context changed before publication")
        store.replace_all(
            scan.root,
            records,
            children_dicts,
            dict(config_ignore_metadata(ignore_config, root), active_source_paths=sorted(active_paths),
                 _expected_policy_generation=context.policy_generation),
        )
        if self.project._context_is_current(context):
            self.ignore._mark_ignore_config_persisted()
        if self.project._context_is_current(context):
            self.project.last_errors = scan.errors[:50]
        if indexer is not None:
            indexer.set_phase(PHASE_EMBEDDING)
        embedding_meta = self.embeddings._embed_after_full_rebuild(root, store, context.embedding_indexer) if self.project._context_is_current(context) else None
        end = time.time()
        if indexer is None:
            # Synchronous rebuild has no BackgroundIndexer handle; record its
            # timing so build_timers can still report this build's duration.
            self.project._last_index_build = {
                "state": STATE_DONE,
                "phase": PHASE_DONE,
                "running": False,
                "elapsed_seconds": round(end - start, 3),
                "started_at": start,
                "finished_at": end,
            }
        result: dict[str, Any] = {
            "status": "indexed",
            "root": scan.root,
            "file_count": len(records),
            "total_file_count": total_file_count,
            "child_index_count": len(children),
            "skipped": scan.skipped,
            "reused": scan.reused,
            "elapsed_seconds": round(end - start, 3),
            "index_path": str(store.db_path),
            "updated_at": iso_time(store.get_metadata_map().get("updated_at")),
            "embedding": embedding_meta,
        }
        # Empty diagnostic lists are noise for LLM callers; include them only
        # when they carry information.
        privileged = scan.privileged_paths
        for key, value in (
            ("auto_ignored_paths", scan.oversized_paths),
            ("oversized_paths", scan.oversized_paths),
            ("privileged_paths", privileged),
        ):
            if value:
                result[key] = value
        if scan.errors:
            result["error_count"] = len(scan.errors)
            result["errors"] = scan.errors[:5]
        return result
