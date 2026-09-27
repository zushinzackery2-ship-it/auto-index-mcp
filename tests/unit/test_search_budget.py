from __future__ import annotations

import time

from auto_index_mcp.search import backend


def test_missing_rg_is_an_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "ripgrep_executable", lambda: None)
    result = backend.search_text(tmp_path, [], "(a+)+$", True, True, 10)
    assert result.status == "unavailable"
    assert not result.complete
    assert "ripgrep" in result.message.lower()


def test_invalid_regex_returns_the_engine_diagnostic(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    result = backend.search_text(tmp_path, [dict(path="a.py")], "(", True, True, 10)
    assert result.status == "invalid_pattern"
    assert result.message
    assert not result.complete


def test_request_deadline_covers_all_batches(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("text", encoding="utf-8")
    monkeypatch.setattr(backend, "SEARCH_TIMEOUT_SECONDS", 0)
    started = time.monotonic()
    result = backend.search_text(tmp_path, [dict(path="a.py")], "text", True, False, 10)
    assert result.status == "timeout"
    assert time.monotonic() - started < 1
