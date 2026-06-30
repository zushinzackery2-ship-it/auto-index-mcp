from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .background_indexer import (
    BackgroundIndexer,
    STATE_ERROR,
    STATE_RUNNING,
    timer_or_idle,
)
from ..indexing.store import IndexStore


class ServiceIndexStateMixin:
    """Index readiness envelopes shared by navigation, search, and rebuild code."""

    if TYPE_CHECKING:
        root_path: Path | None
        store: IndexStore | None
        background: BackgroundIndexer | None
        embedding_background: BackgroundIndexer | None
        _last_index_build: dict[str, Any] | None

    def build_timers(self) -> dict[str, Any]:
        """Live build timers for both pipelines.

        Each entry carries a real-time ``elapsed_seconds`` that ticks while the
        build runs and holds the final duration afterwards. The index timer
        falls back to the most recent synchronous rebuild (watcher-driven builds
        bypass the background runner) so build time is never lost.
        """
        return {
            "index": timer_or_idle(self.background, self._last_index_build),
            "embedding": timer_or_idle(self.embedding_background, None),
        }

    def _background_status(self) -> dict[str, Any]:
        indexer = self.background
        if indexer is None:
            return {
                "status": "idle",
                "background_index": None,
                "build_timers": self.build_timers(),
            }
        return {
            "status": "indexing-in-background",
            "background_index": indexer.status(),
            "build_timers": self.build_timers(),
        }

    def _has_indexed_data(self) -> bool:
        store = self.store
        if store is None:
            return False
        return bool(store.get_metadata_map().get("file_count"))

    def _index_status(self) -> dict[str, Any] | None:
        """Background-index state for responses, or None on the clean path."""
        bg = self.background
        if bg is None:
            return None
        snap = bg.status()
        state = snap["state"]
        if state not in (STATE_RUNNING, STATE_ERROR):
            return None
        ready = self._has_indexed_data()
        return {
            "state": state,
            "phase": snap["phase"],
            "ready": ready,
            "stale": ready and state == STATE_RUNNING,
            "root": str(self.root_path) if self.root_path else None,
            "started_at": snap["started_at"],
            "elapsed_seconds": snap["elapsed_seconds"],
            "error": snap["error"],
        }

    def _with_index_status(self, result: dict[str, Any]) -> dict[str, Any]:
        status = self._index_status()
        if status is None:
            return result
        if not status["ready"]:
            return self._not_ready_envelope(status)
        merged = dict(result)
        merged["index_status"] = status
        return merged

    def _not_ready_response(self) -> dict[str, Any] | None:
        status = self._index_status()
        if status is None:
            return None
        if status["ready"] and not status["stale"]:
            return None
        return self._not_ready_envelope(status)

    @staticmethod
    def _not_ready_envelope(status: dict[str, Any]) -> dict[str, Any]:
        return {"format": "auto_index_not_ready", "items": [], "index_status": status}
