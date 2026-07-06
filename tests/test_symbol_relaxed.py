from __future__ import annotations

from pathlib import Path

import pytest

# Progressive relaxation: only when the direct query has no hit anywhere does
# subtoken AND-matching kick in, and the response says so via match_mode.

PROJECT_PY = '''def resolve_project_callers(project):
    return project


def resolve_widget(widget):
    return widget


class CallersReport:
    def build(self, resolver):
        return resolver
'''


@pytest.fixture
def service(tmp_path: Path, write_file, make_service):
    project = tmp_path / "proj"
    write_file(project / "code.py", PROJECT_PY)
    return make_service(project)


def test_direct_hit_never_relaxes(service) -> None:
    result = service.symbol_search(text="resolve")
    assert result["match_mode"] == "ranked"
    assert result["items"]


def test_multiword_query_relaxes_to_subtoken_match(service) -> None:
    # "resolve callers" has no literal substring hit but both subtokens live
    # in resolve_project_callers.
    result = service.symbol_search(text="resolve callers")
    assert result["match_mode"] == "subtoken-relaxed"
    names = [item["name"] for item in result["items"]]
    assert names[0] == "resolve_project_callers"
    assert result["items"][0]["match"] == "subtoken"


def test_camel_case_query_relaxes(service) -> None:
    result = service.symbol_search(text="resolveCallers")
    assert result["match_mode"] == "subtoken-relaxed"
    assert [item["name"] for item in result["items"]] == ["resolve_project_callers"]


def test_name_carrying_all_subtokens_outranks_signature_split(service) -> None:
    # Both symbols carry "callers"+"resolver-ish" material, but only the
    # function name holds every subtoken; the class matches partly via its
    # method signature.
    result = service.symbol_search(text="resolve callers")
    names = [item["name"] for item in result["items"]]
    assert names[0] == "resolve_project_callers"


def test_single_token_miss_stays_empty(service) -> None:
    result = service.symbol_search(text="zzznotfound")
    assert result["match_mode"] == "ranked"
    assert result["items"] == []


def test_multiword_full_miss_stays_empty_but_relaxed_mode_not_claimed(service) -> None:
    result = service.symbol_search(text="zzz yyy")
    assert result["items"] == []
    assert result["match_mode"] == "ranked"


def test_relaxed_respects_kind_filter(service) -> None:
    result = service.symbol_search(text="callers report", kind="class")
    assert result["match_mode"] == "subtoken-relaxed"
    assert [item["name"] for item in result["items"]] == ["CallersReport"]


def test_deep_page_of_exhausted_direct_results_does_not_relax(service) -> None:
    # Page one has direct hits; asking far beyond the end must return an empty
    # page of the DIRECT result set, not silently switch to relaxed matches.
    first = service.symbol_search(text="resolve", limit=2)
    assert first["items"]
    beyond = service.symbol_search(text="resolve", limit=2, cursor="50")
    assert beyond["items"] == []
    assert beyond["match_mode"] == "ranked"


def test_relaxed_pagination_walks_relaxed_results(service) -> None:
    full = service.symbol_search(text="resolve callers", limit=10)
    assert full["match_mode"] == "subtoken-relaxed"
    page = service.symbol_search(text="resolve callers", limit=1)
    assert page["match_mode"] == "subtoken-relaxed"
    assert page["items"][0]["name"] == full["items"][0]["name"]
