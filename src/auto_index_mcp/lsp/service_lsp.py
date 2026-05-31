from __future__ import annotations

from pathlib import Path
from typing import Any

from .checks import run_check
from .manager import LspManager


class ServiceLspMixin:
    """Wires the LSP layer onto AutoIndexService.

    The manager is created lazily for the active index root and rebuilt if the
    root changes; start runs in the background so the tool never blocks.
    """

    def _lsp_manager(self) -> LspManager:
        lsp_dir = self.index_root / "lsp"
        manager = getattr(self, "_lsp", None)
        if manager is None or getattr(self, "_lsp_dir", None) != lsp_dir:
            if manager is not None:
                manager.shutdown(1.0)
            manager = LspManager(lsp_dir)
            self._lsp = manager
            self._lsp_dir = lsp_dir
        return manager

    def start_lsp(self, timeout_seconds: float = 10.0, background: bool = True) -> str:
        self._require_ready()
        files = self.view.all_files()
        manager = self._lsp_manager()
        if background:
            return manager.start_async(self.root_path, files, timeout_seconds)
        return manager.start(self.root_path, files, timeout_seconds)

    def check_lsp(self, path: str | None = None, limit: int = 80, timeout_seconds: float = 5.0) -> str:
        self._require_ready()
        manager = self._lsp_manager()
        starting = manager.check_start_status(self.root_path)
        if starting:
            return starting
        files = self.view.all_files()
        seed = self._lsp_signature_seed() if self.store else ""
        return run_check(manager, self.root_path, files, self._lsp_document, path, limit, timeout_seconds, seed)

    def stop_lsp(self, timeout_seconds: float = 5.0) -> str:
        manager = getattr(self, "_lsp", None)
        return manager.shutdown(timeout_seconds) if manager is not None else "LSP|stopped"

    def _lsp_document(self, item: dict[str, Any]) -> tuple[str, str]:
        self._require_ready()
        text = self.view.read_indexed_text(self.root_path, item)
        source_root = Path(item.get("source_root") or self.root_path).resolve()
        source_path = item.get("source_path", item["path"])
        return text, (source_root / source_path).resolve().as_uri()

    def _lsp_signature_seed(self) -> str:
        meta = self.store.get_metadata_map()
        return ":".join(str(meta.get(key, "")) for key in ("updated_at", "file_count", "child_index_count"))
