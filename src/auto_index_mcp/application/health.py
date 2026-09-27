from __future__ import annotations

from typing import Any
from ..runtime.background import timer_or_idle
from ..runtime.timefmt import iso_time
from ..workspace.semantic import describe_sources


def embedding_status(project) -> dict:
    indexer = project.embedding_indexer
    sources = describe_sources(project.view, project.root_path, indexer) if indexer is not None and project.store else []
    state, error = _state(sources, project.embedding_background)
    if not project.semantic_enabled:
        state = "disabled"
    elif indexer is None and project.embedding_background is None:
        state = "on-demand"
    result = dict(enabled=indexer is not None, model=indexer.backend.name if indexer else None,
                  state=state, vector_count=sum(s["vector_count"] for s in sources),
                  embedded_symbol_count=sum(s["embedded_symbol_count"] for s in sources),
                  complete=bool(sources) and all(s["complete"] for s in sources), sources=sources,
                  build_timer=timer_or_idle(project.embedding_background, None))
    if error:
        result["error"] = error
    if project.embedding_background is not None:
        result["embedding_background"] = project.embedding_background.status()
    return result


def watcher_status(project) -> dict[str, Any]:
    """Compact watcher view for tool responses (full detail stays on the
    watcher object itself)."""
    if not project.watcher:
        return {"running": False}
    status = project.watcher.status()
    compact: dict[str, Any] = {
        "running": status["running"],
        "role": status["role"],
        "ready": status["ready"],
        "change_count": status["change_count"],
        "last_update_at": iso_time(status.get("last_update_at")),
    }
    if status.get("last_error"):
        compact["last_error"] = status["last_error"]
    return compact



def _state(sources, background) -> tuple[str, str | None]:
    timer = background.status() if background is not None else dict()
    last = timer.get("last_result") or dict()
    count = sum(s["vector_count"] for s in sources)
    error = timer.get("error") or next((s.get("error") for s in sources if s.get("error")), None)
    if last.get("status") == "embedding-unavailable":
        error = last.get("error") or "embedding backend unavailable"
    if error:
        return ("partial" if count else "failed"), error
    if sources and all(s["complete"] for s in sources):
        return ("ready" if count else "empty"), None
    if count:
        return "partial", None
    if last.get("status") in ("embedding-in-other-process", "embedding-waiting-for-index"):
        return "waiting", None
    return "building", None
