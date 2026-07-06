from __future__ import annotations

from pathlib import Path

import pytest

# Relevance ranking for text-driven symbol search: exact name > name prefix >
# name substring > signature-only, with the documented ServiceBase inversion
# as the regression anchor.

BASE_PY = '''class ServiceBase:
    def shared(self):
        return 1


class ServiceIgnoreMixin(ServiceBase):
    def ignored(self):
        return 2


class ServiceRebuildMixin(ServiceBase):
    def rebuild(self):
        return 3
'''

NAMES_PY = '''def lock():
    return 1


def lock_guard():
    return 2


def unlock():
    return 3


def spinlock_try():
    return 4


def acquire(lock_name):
    return 5
'''


@pytest.fixture
def service(tmp_path: Path, write_file, make_service):
    project = tmp_path / "proj"
    write_file(project / "a_base.py", BASE_PY)
    write_file(project / "names.py", NAMES_PY)
    return make_service(project)


def _names(result: dict) -> list[str]:
    return [item["name"] for item in result["items"]]


def test_exact_name_outranks_signature_mention(service) -> None:
    result = service.symbol_search(text="ServiceBase")
    names = _names(result)
    assert names[0] == "ServiceBase"
    assert set(names[1:]) == {"ServiceIgnoreMixin", "ServiceRebuildMixin"}


def test_match_labels_expose_the_tier(service) -> None:
    result = service.symbol_search(text="ServiceBase")
    by_name = {item["name"]: item["match"] for item in result["items"]}
    assert by_name["ServiceBase"] == "exact-name"
    assert by_name["ServiceIgnoreMixin"] == "signature"
    assert "match_rank" not in result["items"][0]


def test_tier_order_exact_prefix_substring_signature(service) -> None:
    result = service.symbol_search(text="lock")
    names = _names(result)
    assert names[0] == "lock"
    # Prefix tier before substring tiers.
    assert names[1] == "lock_guard"
    assert set(names[2:4]) == {"unlock", "spinlock_try"}
    # `acquire` only mentions lock in its signature and comes last.
    assert names[-1] == "acquire"
    matches = [item["match"] for item in result["items"]]
    assert matches == sorted(
        matches,
        key=["exact-name", "name-prefix", "name-substring", "signature"].index,
    )


def test_exact_match_is_case_insensitive(service) -> None:
    result = service.symbol_search(text="servicebase")
    assert _names(result)[0] == "ServiceBase"
    assert result["items"][0]["match"] == "exact-name"


def test_shorter_name_wins_ties_within_a_tier(service) -> None:
    names = _names(service.symbol_search(text="lo"))
    substr = [name for name in names if name in ("lock", "lock_guard", "unlock")]
    assert substr[0] == "lock"


def test_match_mode_is_ranked_for_text_queries(service) -> None:
    assert service.symbol_search(text="lock")["match_mode"] == "ranked"


def test_browse_mode_keeps_path_order_and_no_match_field(service) -> None:
    result = service.symbol_search(text="")
    assert result["match_mode"] == "all"
    items = result["items"]
    assert items == sorted(items, key=lambda item: (item["file_path"].lower(), item["line"]))
    assert all("match" not in item for item in items)


def test_kind_filter_composes_with_ranking(service) -> None:
    result = service.symbol_search(text="lock", kind="function")
    assert _names(result)[0] == "lock"
    assert all(item["kind"] == "function" for item in result["items"])


def test_pagination_is_stable_across_pages(service) -> None:
    full = _names(service.symbol_search(text="lock", limit=10))
    page_one = service.symbol_search(text="lock", limit=2)
    page_two = service.symbol_search(text="lock", limit=2, cursor=page_one["cursor"])
    paged = _names(page_one) + _names(page_two)
    assert paged == full[:4]


def test_ranking_survives_repeated_calls(service) -> None:
    first = _names(service.symbol_search(text="lock"))
    second = _names(service.symbol_search(text="lock"))
    assert first == second
