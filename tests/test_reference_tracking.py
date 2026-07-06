"""Value-reference tracking: functions passed as data count as used."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _symbol(summary: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in summary["symbols"] if item["name"] == name)


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

    summary = service.file_summary("mod.py")
    assert "run" in _symbol(summary, "_order_key")["called_by"]
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

    summary = service.file_summary("mod.py")
    assert "dispatch" in _symbol(summary, "_handler")["called_by"]
    assert "pick" in _symbol(summary, "_picked")["called_by"]
    assert "give" in _symbol(summary, "_given")["called_by"]
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

    summary = service.file_summary("mod.py")
    assert _symbol(summary, "_maybe_dead")["called_by"] == []
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

    summary = service.file_summary("mod.py")
    assert _symbol(summary, "helper")["called_by"] == ["run"]


def test_cross_file_caller_stays_qualified(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "a.py", "from b import helper\n\ndef run():\n    return helper()\n")
    write_file(project / "b.py", "def helper():\n    return True\n")
    service = make_service(project)

    summary = service.file_summary("b.py")
    assert _symbol(summary, "helper")["called_by"] == ["a.py::run"]


def test_conftest_fixtures_belong_to_test_scope(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "conftest.py", "def _seed_fixture():\n    return 1\n")
    write_file(project / "app.py", "def main():\n    return 0\n")
    service = make_service(project)

    assert "_seed_fixture" not in _finding_symbols(service.dangling_check())
    assert "_seed_fixture" in _finding_symbols(service.dangling_check(include_tests=True))


def test_called_by_preview_is_capped_with_total(tmp_path: Path, make_service, write_file) -> None:
    project = tmp_path / "proj"
    callers = "".join(
        f"def caller_{index:02d}():\n    return hub()\n\n" for index in range(30)
    )
    write_file(project / "mod.py", "def hub():\n    return 1\n\n" + callers)
    service = make_service(project)

    hub = _symbol(service.file_summary("mod.py"), "hub")
    assert len(hub["called_by"]) == 25
    assert hub["called_by_total"] == 30
    assert "refs" not in hub

    searched = next(
        item for item in service.symbol_search(text="hub")["items"] if item["name"] == "hub"
    )
    assert len(searched["called_by"]) == 25
    assert searched["called_by_total"] == 30
    assert "refs" not in searched


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
