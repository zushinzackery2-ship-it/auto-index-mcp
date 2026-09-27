from __future__ import annotations

from ..application.health import embedding_status, _state

import math

from ..workspace.semantic import search_workspace


class SemanticQueries:
    def __init__(self, project, state, embeddings) -> None:
        self.project = project
        self.state = state
        self.embeddings = embeddings

    def semantic_search(self, query: str, limit: int = 10, min_score: float = 0.0) -> dict:
        self.project._require_ready()
        if not query.strip():
            raise ValueError("query is required")
        if not math.isfinite(min_score) or not -1 <= min_score <= 1:
            raise ValueError("min_score must be a finite cosine score between -1 and 1")
        if not self.project.semantic_enabled:
            return _unavailable("semantic search is disabled by AUTO_INDEX_SEMANTIC_MODE=off", "disabled")
        indexer = self.project.embedding_indexer
        if indexer is None:
            status = self.embedding_status()
            if status["state"] == "failed":
                return _unavailable(status.get("error", "embedding failed"), "failed")
            self.embeddings.ensure_embedding_background()
            return dict(_unavailable("embedding vectors are building in the background", "building"),
                        embedding_background=self.project.embedding_background.status() if self.project.embedding_background else None)
        if not self.embeddings._vectors_current(self.project.store, indexer):
            self.embeddings.ensure_embedding_background()
        hits, sources = search_workspace(self.project.view, self.project.root_path, indexer, query, max(1, min(int(limit), 100)), min_score)
        complete = all(source["complete"] for source in sources)
        state, error = _state(sources, self.project.embedding_background)
        result = dict(format="auto_index_semantic_search", model=indexer.backend.name, state=state,
                      count=len(hits), items=hits, complete=complete, sources=sources)
        if error:
            result["error"] = error
        if not complete:
            result["embedding"] = dict(status=state, vector_count=sum(s["vector_count"] for s in sources),
                                       background=self.project.embedding_background.status() if self.project.embedding_background else None)
        return self.state._with_index_status(result)

    def embedding_status(self) -> dict:
        return embedding_status(self.project)



def _unavailable(error: str, state: str) -> dict:
    return dict(format="auto_index_semantic_search_unavailable", error=error, state=state, items=[], complete=False)
