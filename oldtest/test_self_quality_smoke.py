from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "self_quality_check.py"
_spec = importlib.util.spec_from_file_location("self_quality_check", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
run_self_quality_check = _module.run_self_quality_check


@pytest.mark.smoke
def test_self_quality_check_passes_on_repo() -> None:
    project = Path(__file__).resolve().parents[1]
    report = run_self_quality_check(project)

    assert report["ok"], report["failures"]
    assert report["enable"]["status"] == "indexed"
    assert report["enable"]["error_count"] == 0
    assert report["resolve_path_items"] >= 1
    assert report["text_search_items"] >= 1
