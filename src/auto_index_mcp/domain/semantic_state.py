"""Semantic readiness is a projection of published sources and task state."""


def semantic_state(sources, background) -> tuple[str, str | None]:
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
