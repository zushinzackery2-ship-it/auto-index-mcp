from __future__ import annotations

from typing import Any

from ..core.subtoken import overlap_score, query_weights, target_token_set

# Hybrid scoring weights, mirroring RagFlow's production rerank: vector
# similarity carries most of the signal, lexical overlap corrects the pure
# cosine ranking whenever the query names identifiers directly.
LEXICAL_WEIGHT = 0.3
VECTOR_WEIGHT = 0.7
# A query term matched only in the signature counts at a discount versus a
# match in the symbol name (RagFlow-style field boosting).
SIGNATURE_MATCH_FACTOR = 0.6

# The cosine stage over-fetches so the lexical blend has candidates to
# promote; small limits still see a meaningful pool.
CANDIDATE_POOL_FACTOR = 4
CANDIDATE_POOL_MIN = 48


def candidate_pool_size(limit: int) -> int:
    return max(limit * CANDIDATE_POOL_FACTOR, CANDIDATE_POOL_MIN)


def rerank_hits(query: str, hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Blend cosine similarity with identifier-level lexical overlap.

    ``score`` becomes the blended value the list is ordered by;
    ``vector_score`` and ``lexical_score`` expose the components. Queries with
    no lexical echo in a candidate degrade to pure vector order because every
    blended score is then the same monotonic transform of the cosine.
    """
    weights = query_weights(query)
    reranked: list[dict[str, Any]] = []
    for hit in hits:
        vector_score = float(hit["score"])
        name_tokens = target_token_set(hit.get("symbol_name", ""))
        signature_tokens = target_token_set(hit.get("signature", ""))
        lexical = overlap_score(
            weights, name_tokens, signature_tokens, SIGNATURE_MATCH_FACTOR
        )
        blended = VECTOR_WEIGHT * vector_score + LEXICAL_WEIGHT * lexical
        item = dict(hit)
        item["vector_score"] = round(vector_score, 4)
        item["lexical_score"] = round(lexical, 4)
        item["score"] = round(blended, 4)
        reranked.append(item)
    reranked.sort(
        key=lambda item: (-item["score"], item["file_path"], item["symbol_line"])
    )
    return reranked[: max(1, limit)]
