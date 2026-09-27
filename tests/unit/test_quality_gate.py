from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "self_quality_check.py"
SPEC = importlib.util.spec_from_file_location("quality_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gate_rejects_real_nesting_and_unused_private_symbol(tmp_path):
    (tmp_path / "main.py").write_text(
        "def main():\n"
        "    for a in []:\n"
        "        for b in []:\n"
        "            for c in []:\n"
        "                for d in []:\n"
        "                    for e in []:\n"
        "                        print(a, b, c, d, e)\n"
        "def _unused():\n"
        "    return 1\n", encoding="utf-8",
    )
    report = MODULE.run_self_quality_check(tmp_path)
    assert not report["ok"]
    assert any("deep nesting" in message for message in report["failures"])
    assert any(item["symbol"] == "_unused" for item in report["unexpected_findings"])


def test_gate_rejects_oversized_modules_and_parse_failures(tmp_path):
    (tmp_path / "main.py").write_text("# line\n" * 301 + "def broken(:\n", encoding="utf-8")
    report = MODULE.run_self_quality_check(tmp_path)
    assert not report["ok"]
    assert report["line_limit_findings"] == [dict(path="main.py", lines=302)]
    assert report["parser_failures"] == ["main.py"]


def test_gate_rejects_missing_coverage_and_truncated_findings():
    nesting = dict(summary=dict(total_findings=0, nesting_coverage=0.5, reliable=True), findings=[])
    dangling = dict(summary=dict(total_findings=1), findings=[])
    report = MODULE.evaluate_quality(nesting, dangling, [])
    assert report["failures"] == [
        "nesting analysis coverage is incomplete", "dangling findings were truncated",
    ]


def test_reviewed_public_symbol_cannot_hide_high_confidence_finding():
    finding = dict(path="module.py", symbol="_dead", kind="unused_symbol", confidence="high")
    nesting = dict(summary=dict(total_findings=0, nesting_coverage=1.0, reliable=True), findings=[])
    dangling = dict(summary=dict(total_findings=1), findings=[finding])
    report = MODULE.evaluate_quality(nesting, dangling, [], dict([(("module.py", "_dead"), "old review")]))
    assert report["unexpected_findings"] == [finding]


def test_gate_disables_service_when_quality_check_raises(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    disabled = []
    original = MODULE.AutoIndexService.disable

    def disable(service):
        disabled.append(service)
        return original(service)

    def fail(*args, **kwargs):
        raise RuntimeError("injected quality failure")

    monkeypatch.setattr(MODULE.AutoIndexService, "disable", disable)
    monkeypatch.setattr(MODULE.AutoIndexService, "nesting_check", fail)
    with pytest.raises(RuntimeError, match="injected quality failure"):
        MODULE.run_self_quality_check(tmp_path)
    assert len(disabled) == 1
    assert not disabled[0].enabled
