from __future__ import annotations

from pathlib import Path

import pytest

from auto_index_mcp.embedding.backend import BagHashEmbedder

# Tool-facing response contracts for the search surfaces this batch changed.

CODE_PY = '''def compute_totals(rows):
    return sum(rows)


class ReportBuilder:
    def render(self):
        return "ok"
'''


@pytest.fixture
def service(tmp_path: Path, write_file, install_embedder, make_service, wait_embedding):
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    install_embedder(BagHashEmbedder(dim=64))
    svc = make_service(project)
    wait_embedding(svc)
    return svc


def test_semantic_search_response_contract(service) -> None:
    result = service.semantic_search("compute totals", limit=5)
    assert result["format"] == "auto_index_semantic_search"
    assert result["count"] == len(result["items"])
    for item in result["items"]:
        assert {
            "file_path",
            "symbol_name",
            "symbol_line",
            "kind",
            "end_line",
            "signature",
            "complexity",
            "score",
            "vector_score",
            "lexical_score",
        } <= set(item)


def test_semantic_model_field_is_display_name_not_storage_key(service) -> None:
    result = service.semantic_search("compute totals", limit=5)
    assert result["model"] == "baghash"
    assert "#" not in result["model"]


def test_semantic_search_rejects_empty_query(service) -> None:
    with pytest.raises(ValueError):
        service.semantic_search("   ")


def test_semantic_search_clamps_limit(service) -> None:
    result = service.semantic_search("compute totals", limit=100000)
    assert result["count"] <= 100


def test_symbol_search_response_contract(service) -> None:
    result = service.symbol_search(text="compute")
    assert result["format"] == "auto_index_symbol_search_indexed"
    assert result["match_mode"] == "ranked"
    assert "cursor" in result
    for item in result["items"]:
        assert "match" in item
        assert "match_rank" not in item


def test_embedding_status_reports_both_counts(service) -> None:
    status = service.embedding_status()
    assert status["enabled"] is True
    assert status["vector_count"] >= status["embedded_symbol_count"] > 0
