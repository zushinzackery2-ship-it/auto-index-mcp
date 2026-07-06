from __future__ import annotations

import pytest

from auto_index_mcp.core.subtoken import (
    BIGRAM_WEIGHT,
    UNIGRAM_WEIGHT,
    overlap_score,
    query_weights,
    split_identifier,
    target_token_set,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("resolve_project_callers", ["resolve", "project", "callers"]),
        ("ServiceBase", ["service", "base"]),
        ("parseJSONValue", ["parse", "json", "value"]),
        ("HTTPServer", ["http", "server"]),
        ("utf8Decode", ["utf", "8", "decode"]),
        ("base64_encode", ["base", "64", "encode"]),
        ("kebab-case-name", ["kebab", "case", "name"]),
        ("SCREAMING_SNAKE", ["screaming", "snake"]),
        ("already lower words", ["already", "lower", "words"]),
        ("__dunder__", ["dunder"]),
        ("x", ["x"]),
    ],
)
def test_split_identifier(text: str, expected: list[str]) -> None:
    assert split_identifier(text) == expected


def test_split_identifier_empty_and_punctuation() -> None:
    assert split_identifier("") == []
    assert split_identifier("!!! ###") == []


def test_split_identifier_mixed_free_text_query() -> None:
    assert split_identifier("find the ConnectionPool.acquire path") == [
        "find",
        "the",
        "connection",
        "pool",
        "acquire",
        "path",
    ]


def test_query_weights_unigrams_and_bigrams() -> None:
    weights = query_weights("resolve callers")
    assert weights["resolve"] == pytest.approx(UNIGRAM_WEIGHT)
    assert weights["callers"] == pytest.approx(UNIGRAM_WEIGHT)
    assert weights["resolvecallers"] == pytest.approx(BIGRAM_WEIGHT)


def test_query_weights_repeated_tokens_accumulate() -> None:
    weights = query_weights("lock lock")
    assert weights["lock"] == pytest.approx(2 * UNIGRAM_WEIGHT)
    assert weights["locklock"] == pytest.approx(BIGRAM_WEIGHT)


def test_query_weights_single_token_has_no_bigram() -> None:
    weights = query_weights("resolve")
    assert list(weights) == ["resolve"]


def test_target_token_set_contains_pairs() -> None:
    target = target_token_set("resolve_project_callers")
    assert {"resolve", "project", "callers"} <= target
    assert {"resolveproject", "projectcallers"} <= target


def test_overlap_full_match_is_one() -> None:
    weights = query_weights("resolve project callers")
    target = target_token_set("resolve_project_callers")
    assert overlap_score(weights, target, set()) == pytest.approx(1.0)


def test_overlap_no_match_is_zero() -> None:
    weights = query_weights("resolve callers")
    assert overlap_score(weights, target_token_set("open_database"), set()) == 0.0


def test_overlap_signature_match_is_discounted() -> None:
    weights = query_weights("config")
    name_hit = overlap_score(weights, {"config"}, set(), secondary_factor=0.6)
    signature_hit = overlap_score(weights, set(), {"config"}, secondary_factor=0.6)
    assert name_hit == pytest.approx(1.0)
    assert signature_hit == pytest.approx(0.6)
    assert signature_hit < name_hit


def test_overlap_partial_match_between_zero_and_one() -> None:
    weights = query_weights("resolve project callers")
    partial = overlap_score(weights, target_token_set("resolve_widget"), set())
    assert 0.0 < partial < 1.0


def test_overlap_word_order_pair_beats_scattered_words() -> None:
    weights = query_weights("project callers")
    adjacent = overlap_score(weights, target_token_set("project_callers_map"), set())
    scattered = overlap_score(weights, target_token_set("callers_of_project"), set())
    assert adjacent > scattered


def test_overlap_empty_query_is_zero() -> None:
    assert overlap_score({}, {"anything"}, set()) == 0.0
