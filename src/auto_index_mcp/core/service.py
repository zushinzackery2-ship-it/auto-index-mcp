from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_WATCH_DEBOUNCE_SECONDS, project_index_root
from .lsp import LspManager
from .navigation_format import compact_file, overview_result, tree_result
from ..workspace.view import WorkspaceView
from ..indexing.analysis import resolve_project_callers
from ..indexing.scanner import SourceScanner
from ..indexing.snapshot import snapshot_from_index, take_watch_snapshot
from ..search.backend import search_text
from ..indexing.store import IndexStore
from ..indexing.updater import IndexUpdater
from ..indexing.watcher import FileEventWatcher
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes


class AutoIndexService:
    def __init__(self, index_root: Path | None = None) -> None:
        self.index_root_override = index_root
        self.index_root: Path | None = index_root
        self.root_path: Path | None = None
        self.enabled = False
        self.last_errors: list[str] = []
        self.store: IndexStore | None = None
        self.watcher: FileEventWatcher | None = None
        self.lsp = LspManager()

    @property
    def file_count(self) -> int:
        self._require_store()
        return len(self.view.all_files())

    @property
    def view(self) -> WorkspaceView:
        self._require_store()
        return WorkspaceView(self.store)

    def enable(self, root_path: str, rebuild: bool = True) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"root_path is not a directory: {root_path}")
        if self.root_path and self.root_path != root:
            self.stop_lsp()
            self.stop_watcher()
        self.root_path = root
        self.enabled = True
        self.index_root = self.index_root_override or project_index_root(root)
        self.store = IndexStore(self._db_path(root))
        self.store.initialize()
        if rebuild:
            return self.rebuild()
        return self.status()

    def disable(self) -> dict[str, Any]:
        self.stop_lsp()
        self.stop_watcher()
        self.enabled = False
        return self.status()

    def rebuild(self) -> dict[str, Any]:
        self._require_ready()
        start = time.time()
        existing = {item["path"]: item for item in self.store.all_files()}
        children = discover_child_indexes(self.root_path, self.store.db_path)
        boundary_roots = [Path(child.root) for child in children]
        scan = SourceScanner(str(self.root_path), existing_records=existing, boundary_roots=boundary_roots).scan()
        records = resolve_project_callers(scan.records)
        self.store.replace_all(scan.root, records, child_indexes_to_dicts(children))
        self.last_errors = scan.errors[:50]
        return {
            "status": "indexed",
            "root": scan.root,
            "file_count": len(records),
            "total_file_count": len(records) + sum(child.file_count for child in children),
            "child_index_count": len(children),
            "skipped": scan.skipped,
            "reused": scan.reused,
            "error_count": len(scan.errors),
            "elapsed_seconds": round(time.time() - start, 3),
            "index_path": str(self.store.db_path),
        }

    def status(self) -> dict[str, Any]:
        meta = self.store.get_metadata_map() if self.store else {}
        return {
            "enabled": self.enabled,
            "root": str(self.root_path) if self.root_path else None,
            "index_path": str(self.store.db_path) if self.store else None,
            "file_count": meta.get("file_count", 0),
            "total_file_count": (meta.get("file_count", 0) + sum(child["file_count"] for child in self.store.child_indexes())) if self.store else 0,
            "child_index_count": meta.get("child_index_count", 0),
            "updated_at": meta.get("updated_at"),
            "last_error_count": len(self.last_errors),
            "last_errors": self.last_errors[:10],
        }

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        self._require_store()
        if delete_file:
            self.stop_lsp()
            self.stop_watcher()
            self.store.delete_file()
            self.store = None
            self.enabled = False
        else:
            self.store.clear()
        return self.status()

    def start_watcher(self, debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool = True) -> dict[str, Any]:
        self._require_ready()
        if debounce_seconds < 0.05:
            raise ValueError("debounce_seconds must be >= 0.05")
        if (
            self.watcher
            and self.watcher.is_running()
            and self.watcher.root.resolve() == self.root_path.resolve()
            and self.watcher.debounce_seconds == debounce_seconds
        ):
            return self.watcher_status()
        if self.watcher:
            self.watcher.stop()
        children = lambda: [Path(child["root"]) for child in self.store.child_indexes()]
        snapshot = lambda: take_watch_snapshot(self.root_path, children(), self.store.db_path)
        previous = snapshot_from_index(self.root_path, self.store.all_files(), self.store.child_indexes()) if not wait_ready else None
        self.watcher = FileEventWatcher(self.root_path, snapshot, IndexUpdater(self.root_path, self.store, self.rebuild).apply, debounce_seconds, previous)
        self.watcher.start(wait_ready=wait_ready)
        return self.watcher_status()

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        self._require_ready()
        child_indexes = self.store.child_indexes()
        child_roots = [Path(child["root"]) for child in child_indexes]
        previous = snapshot_from_index(self.root_path, self.store.all_files(), child_indexes)
        current = take_watch_snapshot(self.root_path, child_roots, self.store.db_path)
        return IndexUpdater(self.root_path, self.store, self.rebuild).apply(previous, current)

    def stop_watcher(self) -> dict[str, Any]:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        return self.watcher_status()

    def start_lsp(self, timeout_seconds: float = 10.0, background: bool = False) -> str:
        files = self.view.all_files() if self.store else []
        if background:
            return self.lsp.start_async(self.root_path, files, timeout_seconds)
        return self.lsp.start(self.root_path, files, timeout_seconds)

    def lsp_start_status(self) -> str:
        return self.lsp.start_status(self.root_path)

    def check_lsp(self, path: str | None = None, limit: int = 80, timeout_seconds: float = 5.0) -> str:
        files = self.view.all_files() if self.store else []
        return self.lsp.check(self.root_path, files, self._lsp_document, path, limit, timeout_seconds)

    def stop_lsp(self, timeout_seconds: float = 5.0) -> str:
        return self.lsp.shutdown(self.root_path, timeout_seconds)

    def watcher_status(self) -> dict[str, Any]:
        if not self.watcher:
            return {"running": False}
        return self.watcher.status()

    def overview(self, limit: int = 30) -> dict[str, Any]:
        self._require_store()
        files = self.view.all_files()
        return overview_result(files, limit)

    def tree_get(self, root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any]:
        self._require_store()
        files = self.view.all_files()
        return tree_result(files, root_path, depth, limit)

    def query(self, text: str = "", languages: list[str] | None = None, parent: str = "", limit: int = 80, cursor: str | None = None) -> dict[str, Any]:
        self._require_store()
        offset = int(cursor or "0")
        rows = self.view.query(text, languages or [], parent, limit, offset)
        next_cursor = str(offset + limit) if len(rows) == limit else None
        return {"format": "auto_index_query_indexed", "items": [compact_file(row) for row in rows], "cursor": next_cursor}

    def text_search(
        self,
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 80,
        file_pattern: str | None = None,
        context_lines: int = 0,
    ) -> dict[str, Any]:
        self._require_ready()
        if not pattern:
            raise ValueError("pattern is required")
        backend, matches = search_text(
            self.root_path,
            self.view.all_files(),
            pattern,
            case_sensitive,
            regex,
            limit,
            file_pattern,
        )
        if context_lines > 0:
            matches = [self._with_context(match, context_lines) for match in matches]
        return {"format": "auto_index_text_search_indexed", "backend": backend, "items": matches}

    def symbol_search(self, text: str = "", kind: str = "", limit: int = 80, cursor: str | None = None) -> dict[str, Any]:
        self._require_store()
        offset = int(cursor or "0")
        rows = self.view.query_symbols(text, kind, limit, offset)
        next_cursor = str(offset + limit) if len(rows) == limit else None
        return {"format": "auto_index_symbol_search_indexed", "items": rows, "cursor": next_cursor}

    def symbol_body(self, path: str, symbol_name: str) -> dict[str, Any]:
        self._require_ready()
        if not path or not symbol_name:
            raise ValueError("path and symbol_name are required")
        lookup = self.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        matches = [symbol for symbol in lookup.item["symbols"] if symbol["name"] == symbol_name]
        if not matches:
            raise KeyError(f"symbol not found: {symbol_name}")
        if len(matches) > 1:
            return {"format": "auto_index_symbol_body_ambiguous", "candidates": matches}
        symbol = matches[0]
        lines = self.view.read_indexed_text(self.root_path, lookup.item).splitlines()
        start = max(1, symbol["line"])
        end = min(len(lines), symbol["end_line"])
        code = "\n".join(lines[start - 1:end])
        return {"format": "auto_index_symbol_body_full", "symbol": symbol, "path": path, "code": code}

    def file_summary(self, path: str) -> dict[str, Any]:
        self._require_store()
        lookup = self.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        symbols = lookup.item["symbols"]
        return {
            "format": "auto_index_file_summary_full",
            "path": lookup.item["path"],
            "language": lookup.item["language"],
            "line_count": lookup.item["line_count"],
            "imports": lookup.item["imports"],
            "symbol_count": len(symbols),
            "symbols": symbols,
            "total_complexity": sum(symbol.get("complexity", 1) for symbol in symbols),
            "max_complexity": max((symbol.get("complexity", 1) for symbol in symbols), default=0),
        }

    def get(self, path: str) -> dict[str, Any]:
        self._require_store()
        lookup = self.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        return {"format": "auto_index_get_full", "item": lookup.item}

    def file_content(self, path: str) -> str:
        self._require_ready()
        return self.view.read_text(self.root_path, path)

    def resolve_path(self, path: str, limit: int = 20) -> dict[str, Any]:
        self._require_store()
        needle = path.lower().replace("\\", "/")
        matches = []
        for item in self.view.all_files():
            candidate = item["path"].lower()
            if candidate == needle or item["name"].lower() == needle or needle in candidate:
                matches.append(compact_file(item))
            if len(matches) >= limit:
                break
        return {"format": "auto_index_resolve_indexed", "items": matches}

    def diff_filesystem(self) -> dict[str, Any]:
        self._require_ready()
        diff = self.view.diff_filesystem(self.root_path)
        added = diff["added"]
        deleted = diff["deleted"]
        changed = diff["changed"]
        return {"format": "auto_index_diff_indexed", "added": added[:100], "deleted": deleted[:100], "changed": changed[:100], "added_count": len(added), "deleted_count": len(deleted), "changed_count": len(changed)}

    def all_files(self) -> list[dict[str, Any]]:
        self._require_store()
        return self.view.all_files()

    def _lsp_document(self, item: dict[str, Any]) -> tuple[str, str]:
        self._require_ready()
        text = self.view.read_indexed_text(self.root_path, item)
        source_root = Path(item.get("source_root") or self.root_path).resolve()
        source_path = item.get("source_path", item["path"])
        return text, (source_root / source_path).resolve().as_uri()

    def _with_context(self, match: dict[str, Any], context_lines: int) -> dict[str, Any]:
        enriched = dict(match)
        try:
            enriched["context"] = self.view.context_for_match(self.root_path, match, context_lines)
        except UnicodeDecodeError:
            enriched["context"] = []
        return enriched

    def _db_path(self, root: Path) -> Path:
        index_root = self.index_root_override or project_index_root(root)
        return index_root / "index.db"

    def _require_ready(self) -> None:
        self._require_store()
        if self.root_path is None:
            raise RuntimeError("auto-index root is not configured")

    def _require_store(self) -> None:
        if self.store is None:
            raise RuntimeError("auto-index is not enabled")
