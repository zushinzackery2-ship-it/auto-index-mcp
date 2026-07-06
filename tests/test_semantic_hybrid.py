from __future__ import annotations

from pathlib import Path

import pytest

from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.rerank import (
    LEXICAL_WEIGHT,
    VECTOR_WEIGHT,
    rerank_hits,
)

# Hybrid rerank: cosine recall stays, but a query that literally names an
# identifier promotes that identifier over lexically unrelated neighbors.
# BagHash hashes whole identifiers (underscores included), so the function
# NAME is invisible to its vectors - exactly the failure mode the lexical
# blend exists to correct.

CODE_PY = '''def resolve_project_callers(project):
    """resolve project callers"""
    return project


def unrelated_thing():
    """resolve project callers maybe later"""
    return None
'''


@pytest.fixture
def service(tmp_path: Path, write_file, install_embedder, make_service, wait_embedding):
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    install_embedder(BagHashEmbedder(dim=128))
    svc = make_service(project)
    wait_embedding(svc)
    return svc


def test_named_symbol_outranks_lexically_blind_cosine(service) -> None:
    result = service.semantic_search("resolve project callers", limit=5)
    names = [item["symbol_name"] for item in result["items"]]
    assert names[0] == "resolve_project_callers"


def test_items_carry_both_score_components(service) -> None:
    result = service.semantic_search("resolve project callers", limit=5)
    for item in result["items"]:
        assert 0.0 <= item["vector_score"] <= 1.0001
        assert 0.0 <= item["lexical_score"] <= 1.0001
        blended = VECTOR_WEIGHT * item["vector_score"] + LEXICAL_WEIGHT * item["lexical_score"]
        assert item["score"] == pytest.approx(blended, abs=1e-3)


def test_full_name_match_scores_full_lexical(service) -> None:
    result = service.semantic_search("resolve project callers", limit=5)
    by_name = {item["symbol_name"]: item for item in result["items"]}
    assert by_name["resolve_project_callers"]["lexical_score"] == pytest.approx(1.0)
    assert by_name["unrelated_thing"]["lexical_score"] == pytest.approx(0.0)


def test_results_ordered_by_blended_score(service) -> None:
    items = service.semantic_search("resolve project callers", limit=5)["items"]
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)


def test_min_score_still_gates_on_cosine(service) -> None:
    result = service.semantic_search("resolve project callers", limit=5, min_score=0.99)
    # No stored vector reaches a 0.99 cosine against this query; the lexical
    # blend must not resurrect candidates the cosine floor excluded.
    assert result["items"] == []


def test_rerank_hits_keeps_pure_vector_order_without_lexical_echo() -> None:
    hits = [
        {"file_path": "a.py", "symbol_name": "alpha", "symbol_line": 1, "signature": "def alpha():", "score": 0.9},
        {"file_path": "b.py", "symbol_name": "beta", "symbol_line": 1, "signature": "def beta():", "score": 0.5},
    ]
    ranked = rerank_hits("zzz completely unrelated", hits, 2)
    assert [item["symbol_name"] for item in ranked] == ["alpha", "beta"]
    assert all(item["lexical_score"] == 0.0 for item in ranked)


def test_rerank_hits_promotes_exact_identifier() -> None:
    hits = [
        {"file_path": "a.py", "symbol_name": "cache_flush", "symbol_line": 1, "signature": "def cache_flush():", "score": 0.80},
        {"file_path": "b.py", "symbol_name": "purge_stale_models", "symbol_line": 1, "signature": "def purge_stale_models():", "score": 0.86},
    ]
    ranked = rerank_hits("cache flush", hits, 2)
    assert ranked[0]["symbol_name"] == "cache_flush"


def test_rerank_hits_signature_only_match_is_weaker() -> None:
    hits = [
        {"file_path": "a.py", "symbol_name": "handler", "symbol_line": 1, "signature": "def handler(cache_flush):", "score": 0.7},
        {"file_path": "b.py", "symbol_name": "cache_flush", "symbol_line": 1, "signature": "def cache_flush():", "score": 0.7},
    ]
    ranked = rerank_hits("cache flush", hits, 2)
    assert ranked[0]["symbol_name"] == "cache_flush"
    assert ranked[0]["lexical_score"] > ranked[1]["lexical_score"] > 0.0


def test_rerank_hits_respects_limit() -> None:
    hits = [
        {"file_path": f"f{i}.py", "symbol_name": f"sym{i}", "symbol_line": 1, "signature": "", "score": 0.5}
        for i in range(10)
    ]
    assert len(rerank_hits("sym", hits, 3)) == 3
