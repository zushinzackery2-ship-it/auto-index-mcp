from __future__ import annotations

from pathlib import Path
from typing import Any


class ServiceLspMixin:
    def start_lsp(self, timeout_seconds: float = 10.0, background: bool = False) -> str:
        files = self.view.all_files() if self.store else []
        if background:
            return self.lsp.start_async(self.root_path, files, timeout_seconds)
        return self.lsp.start(self.root_path, files, timeout_seconds)

    def lsp_start_status(self) -> str:
        return self.lsp.start_status(self.root_path)

    def check_lsp(self, path: str | None = None, limit: int = 80, timeout_seconds: float = 5.0) -> str:
        starting = self.lsp.check_start_status(self.root_path)
        if starting:
            return starting
        files = [] if path else (self.view.all_files() if self.store else [])
        signature_seed = self._lsp_workspace_signature_seed() if path and self.store else ""
        return self.lsp.check(self.root_path, files, self._lsp_document, path, limit, timeout_seconds, signature_seed)

    def stop_lsp(self, timeout_seconds: float = 5.0) -> str:
        return self.lsp.shutdown(self.root_path, timeout_seconds)

    def _lsp_document(self, item: dict[str, Any]) -> tuple[str, str]:
        self._require_ready()
        text = self.view.read_indexed_text(self.root_path, item)
        source_root = Path(item.get("source_root") or self.root_path).resolve()
        source_path = item.get("source_path", item["path"])
        return text, (source_root / source_path).resolve().as_uri()

    def _lsp_workspace_signature_seed(self) -> str:
        meta = self.store.get_metadata_map()
        return ":".join(str(meta.get(key, "")) for key in ("updated_at", "file_count", "child_index_count"))