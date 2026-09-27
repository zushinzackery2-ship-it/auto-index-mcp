"""Value-reference tracking: functions passed as data count as used.

The call graph is exposed through ``symbol_refs`` (compact search rows no
longer embed ``called_by``); these tests pin both the tracking semantics and
that tool-facing surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _refs(service: Any, name: str, path: str = "", **kwargs: Any) -> dict[str, Any]:
    result = service.symbol_refs(name, path, **kwargs)
    assert result["format"] == "auto_index_symbol_refs_v2"
    return result["items"][0]


def _finding_symbols(report: dict[str, Any]) -> set[str]:
    return {finding.get("symbol", "") for finding in report["findings"]}


def test_sort_key_reference_creates_caller_edge(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def _order_key(item):\n"
        "    return item[0]\n"
        "\n"
        "def run(rows):\n"
        "    rows.sort(key=_order_key)\n"
        "    return rows\n",
    )
    service = make_service(project)

    assert "run" in _refs(service, "_order_key", "mod.py")["callers"]
    assert "_order_key" not in _finding_symbols(service.dangling_check())


def test_value_reference_forms_create_edges(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def _handler():\n"
        "    return 1\n"
        "\n"
        "def _picked():\n"
        "    return 2\n"
        "\n"
        "def _given():\n"
        "    return 3\n"
        "\n"
        "def dispatch():\n"
        "    table = {\"on\": _handler}\n"
        "    return table\n"
        "\n"
        "def pick():\n"
        "    chosen = _picked\n"
        "    return chosen\n"
        "\n"
        "def give():\n"
        "    return _given\n",
    )
    service = make_service(project)

    assert "dispatch" in _refs(service, "_handler")["callers"]
    assert "pick" in _refs(service, "_picked")["callers"]
    assert "give" in _refs(service, "_given")["callers"]
    assert _finding_symbols(service.dangling_check()).isdisjoint({"_handler", "_picked", "_given"})


def test_def_line_parameters_are_not_references(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def _maybe_dead(records):\n"
        "    return records\n"
        "\n"
        "def user(_maybe_dead):\n"
        "    return 1\n",
    )
    service = make_service(project)

    refs = _refs(service, "_maybe_dead", "mod.py")
    assert refs["callers"] == []
    assert refs["caller_count"] == 0
    assert "_maybe_dead" in _finding_symbols(service.dangling_check())


def test_same_file_caller_recorded_once(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return helper()\n",
    )
    service = make_service(project)

    assert _refs(service, "helper", "mod.py")["callers"] == ["run"]


def test_cross_file_caller_stays_qualified(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "a.py", "from b import helper\n\ndef run():\n    return helper()\n")
    write_file(project / "b.py", "def helper():\n    return True\n")
    service = make_service(project)

    assert _refs(service, "helper", "b.py")["callers"] == ["a.py::run"]


def test_refs_direction_filters_sides(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def run():\n"
        "    return helper()\n",
    )
    service = make_service(project)

    callers_only = _refs(service, "helper", direction="callers")
    assert "callers" in callers_only and "callees" not in callers_only
    callees_only = _refs(service, "run", direction="callees")
    assert "callees" in callees_only and "callers" not in callees_only
    assert "helper" in callees_only["callees"]


def test_conftest_fixtures_belong_to_test_scope(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "conftest.py", "def _seed_fixture():\n    return 1\n")
    write_file(project / "app.py", "def main():\n    return 0\n")
    service = make_service(project)

    assert "_seed_fixture" not in _finding_symbols(service.dangling_check())
    assert "_seed_fixture" in _finding_symbols(service.dangling_check(include_tests=True))


def test_caller_list_is_capped_with_total(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    callers = "".join(
        f"def caller_{index:02d}():\n    return hub()\n\n" for index in range(30)
    )
    write_file(project / "mod.py", "def hub():\n    return 1\n\n" + callers)
    service = make_service(project)

    hub = _refs(service, "hub", "mod.py")
    assert len(hub["callers"]) == 25
    assert hub["caller_count"] == 30

    widened = _refs(service, "hub", "mod.py", limit=100)
    assert len(widened["callers"]) == 30


def test_search_rows_stay_compact(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def hub():\n    return 1\n\ndef run():\n    return hub()\n",
    )
    service = make_service(project)

    searched = next(
        item for item in service.symbol_search(text="hub")["items"] if item["name"] == "hub"
    )
    assert "called_by" not in searched
    assert "calls" not in searched
    assert {"name", "kind", "line", "end_line", "signature", "path"} <= set(searched)


def test_full_detail_keeps_raw_record(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(
        project / "mod.py",
        "def _order_key(item):\n"
        "    return item[0]\n"
        "\n"
        "def run(rows):\n"
        "    rows.sort(key=_order_key)\n"
        "    return rows\n",
    )
    service = make_service(project)

    raw = service.get("mod.py")["item"]
    run = next(symbol for symbol in raw["symbols"] if symbol["name"] == "run")
    assert "_order_key" in run["refs"]
