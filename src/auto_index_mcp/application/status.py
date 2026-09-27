from __future__ import annotations

from .health import embedding_status, watcher_status
from ..domain.responses import ENABLE_HINT
from ..runtime.timefmt import iso_time

from typing import Any

from ..runtime.background import (
    STATE_ERROR,
    STATE_RUNNING,
    timer_or_idle,
)
from ..runtime.leases import BuildLock


class StatusCoordinator:
    def __init__(self, project) -> None:
        self.project = project

    def build_timers(self) -> dict[str, Any]:
        """Live build timers for both pipelines.

        Each entry carries a real-time ``elapsed_seconds`` that ticks while the
        build runs and holds the final duration afterwards. The index timer
        falls back to the most recent synchronous rebuild (watcher-driven builds
        bypass the background runner) so build time is never lost.
        """
        return {
            "index": timer_or_idle(self.project.background, self.project._last_index_build),
            "embedding": timer_or_idle(self.project.embedding_background, None),
        }

    def _background_status(self) -> dict[str, Any]:
        indexer = self.project.background
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
        if timer.get("phase") == "waiting-for-writer" and self.project.index_root is not None:
            result["build_lock"] = BuildLock(self.project.index_root / "index.build.lock").state_info()
        return result

    def _has_indexed_data(self) -> bool:
        store = self.project.store
        if store is None:
            return False
        return bool(store.get_metadata_map().get("file_count"))

    def _index_status(self) -> dict[str, Any] | None:
        """Background-index state for responses, or None on the clean path."""
        bg = self.project.background
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


    def status(self) -> dict[str, Any]:
        """Compact index health: one level of nesting, ISO timestamps, no
        duplicated background result blobs (LLM callers read this a lot)."""
        store = self.project.store
        meta = store.get_metadata_map() if store else {}
        file_count = int(meta.get("file_count") or 0)
        total_file_count = int(meta.get("total_file_count") or file_count)
        timers = self.build_timers()
        result: dict[str, Any] = {
            "enabled": self.project.enabled,
            "root": str(self.project.root_path) if self.project.root_path else None,
            "index_path": str(store.db_path) if store else None,
            "log_path": getattr(self.project, "log_path", None),
            "file_count": file_count,
            "total_file_count": total_file_count,
            "child_index_count": int(meta.get("child_index_count") or 0),
            "updated_at": iso_time(meta.get("updated_at")),
            "source_generation": int(meta.get("source_generation", 0)),
            "policy_generation": int(meta.get("policy_generation", 0)),
            "filesystem_state": "watching" if self.project.watcher is not None else "not-observed",
            "watcher": watcher_status(self.project),
            "embedding": self._compact_embedding_status(timers["embedding"]),
            "index_build": _compact_timer_view(timers["index"]),
        }
        if self.project.root_path is not None:
            result["registered"] = self.project.registry.is_registered(self.project.index_root)
        if self.project.last_errors:
            result["error_count"] = len(self.project.last_errors)
            result["errors"] = self.project.last_errors[:5]
        if store is None or self.project.root_path is None:
            result["hint"] = ENABLE_HINT
        return result

    def _compact_embedding_status(self, timer: dict[str, Any]) -> dict[str, Any]:
        status = embedding_status(self.project)
        return dict((key, status[key]) for key in ("enabled", "model", "state", "vector_count", "complete", "error") if key in status)


def _compact_timer_view(timer: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": timer.get("state"),
        "phase": timer.get("phase"),
        "running": bool(timer.get("running")),
        "elapsed_seconds": timer.get("elapsed_seconds"),
    }
