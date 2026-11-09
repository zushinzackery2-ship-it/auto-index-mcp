from __future__ import annotations

from typing import Any
from contextlib import ExitStack
from ..runtime.background import timer_or_idle
from ..runtime.timefmt import iso_time
from ..workspace.semantic import describe_sources
from ..domain.semantic_state import semantic_state


def embedding_status(project, binding=None) -> dict:
    sources = []
    with ExitStack() as resources:
        with project.tasks.lock:
            captured = resources.enter_context(project.bindings.read())
            binding = binding or captured
            indexer = project.embedding_indexer
            background = project.embedding_background
        if indexer is not None and binding is not None:
            with binding.open_view(project.control) as view:
                sources = describe_sources(view, binding.root, indexer)
    state, error = semantic_state(sources, background)
    if not project.semantic_enabled:
        state = "disabled"
    elif indexer is None and background is None:
        state = "on-demand"
    result = dict(enabled=indexer is not None, model=indexer.backend.name if indexer else None,
                  state=state, vector_count=sum(s["vector_count"] for s in sources),
                  embedded_symbol_count=sum(s["embedded_symbol_count"] for s in sources),
                  complete=bool(sources) and all(s["complete"] for s in sources), sources=sources,
                  build_timer=timer_or_idle(background, None))
    if error:
        result["error"] = error
    if background is not None:
        result["embedding_background"] = background.status()
    return result


def watcher_status(project) -> dict[str, Any]:
    """Compact watcher view for tool responses (full detail stays on the
    watcher object itself)."""
    watcher = project.watcher
    if watcher is None:
        return {"running": False}
    status = watcher.status()
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
