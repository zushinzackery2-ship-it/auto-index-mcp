from __future__ import annotations

from typing import Any

from .background_indexer import (
    STATE_ERROR,
    STATE_RUNNING,
    timer_or_idle,
)
from .service_state import ServiceBase


class ServiceIndexStateMixin(ServiceBase):
    """Index readiness envelopes shared by navigation, search, and rebuild code."""

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
                "index_build": _compact_timer_view(self.build_timers()["index"]),
            }
        timer = indexer.timer()
        result: dict[str, Any] = {
            "status": "indexing-in-background",
            "index_build": _compact_timer_view(timer),
            "hint": "index is building in the background; poll auto_index_status() and retry shortly",
        }
        error = indexer.status().get("error")
        if error:
            result["error"] = error
        return result

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
        elapsed = status.get("elapsed_seconds")
        running_for = f" (running for {elapsed:.0f}s)" if isinstance(elapsed, (int, float)) else ""
        return {
            "format": "auto_index_not_ready",
            "items": [],
            "index_status": status,
            "hint": f"index is still building{running_for}; retry shortly or check auto_index_status()",
        }


def _compact_timer_view(timer: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": timer.get("state"),
        "phase": timer.get("phase"),
        "running": bool(timer.get("running")),
        "elapsed_seconds": timer.get("elapsed_seconds"),
    }
