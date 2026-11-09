"""Absence of indexed callers is evaluated against analysis capabilities."""
import pytest


def test_inherited_private_callback_is_unknown_not_high_confidence(tmp_path, write_file, make_service):
    write_file(tmp_path / "bridge.py", "from external import Emitter\n\nclass Adapter(Emitter):\n"
               "    def _read_events(self):\n        return []\n")
    service = make_service(tmp_path)
    assert not any(row.get("symbol") == "_read_events" for row in service.dangling_check()["findings"])
    detailed = service.dangling_check(include_low_confidence=True)
    finding = next(row for row in detailed["findings"] if row.get("symbol") == "_read_events")
    assert finding["confidence"] == "low"
    assert "inherited-dispatch" in finding["limitations"]


@pytest.mark.parametrize("source", ["def broken(:\n", "void work() {}\n"])
def test_incomplete_or_heuristic_scope_does_not_certify_unused(tmp_path, write_file, make_service, source):
    extension = ".py" if "broken" in source else ".cpp"
    write_file(tmp_path / ("other" + extension), source)
    write_file(tmp_path / "private.py", "def _orphan():\n    return 1\n")
    service = make_service(tmp_path)
    result = service.dangling_check(include_low_confidence=True)
    finding = next(row for row in result["findings"] if row.get("symbol") == "_orphan")
    assert finding["confidence"] != "high"
    assert "incomplete-reference-analysis" in finding["limitations"]


def test_private_python_function_still_has_actionable_unused_evidence(tmp_path, write_file, make_service):
    write_file(tmp_path / "private.py", "def _orphan():\n    return 1\n")
    service = make_service(tmp_path)
    result = service.dangling_check()
    finding = next(row for row in result["findings"] if row.get("symbol") == "_orphan")
    assert finding["confidence"] == "high"
    assert finding["scope"] == "indexed-workspace"
    assert result["coverage"]["external_callers"] == "unknown"


def test_output_filter_cannot_raise_analysis_confidence(tmp_path, write_file, make_service):
    write_file(tmp_path / "broken.py", "def broken(:\n")
    write_file(tmp_path / "private.py", "def _orphan():\n    return 1\n")
    service = make_service(tmp_path)
    result = service.dangling_check(include_low_confidence=True, exclude_paths=["broken.py"])
    finding = next(row for row in result["findings"] if row.get("symbol") == "_orphan")
    assert finding["confidence"] == "low"
    assert not result["coverage"]["complete_reference_analysis"]


@pytest.mark.parametrize("header", ["@framework\nclass Handler:", "class Handler(metaclass=Meta):"])
def test_class_dispatch_constraints_reach_members(tmp_path, write_file, make_service, header):
    write_file(tmp_path / "handler.py", header + "\n    def _event(self):\n        return 1\n")
    service = make_service(tmp_path)
    result = service.dangling_check(include_low_confidence=True)
    finding = next(row for row in result["findings"] if row.get("symbol") == "_event")
    assert finding["confidence"] == "low"
    assert "class-dispatch" in finding["limitations"]
