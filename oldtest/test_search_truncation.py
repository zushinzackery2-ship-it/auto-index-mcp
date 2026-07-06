from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_index_mcp.search import backend
from auto_index_mcp.search.backend import MAX_MATCH_TEXT_CHARS, _truncate_match_text
from auto_index_mcp.search.file_cache import clear_file_cache


class StreamingProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class StreamingFactory:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.processes: list[StreamingProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> StreamingProcess:
        process = StreamingProcess(self.lines)
        self.processes.append(process)
        return process


class DummyTimer:
    def cancel(self) -> None:
        pass


def _rg_match(path: Path, line: int, text: str) -> str:
    return (
        '{"type":"match","data":'
        f'{{"path":{{"text":{str(path)!r}}},"line_number":{line},"lines":{{"text":{text!r}}}}}'
        "}"
    ).replace("'", '"')


def _long_line(keyword: str, target_len: int) -> str:
    padding = "x" * max(0, target_len - len(keyword))
    return padding + keyword + padding


# ─── Unit: _truncate_match_text ─────────────────────────────


def test_truncate_short_line_unchanged() -> None:
    assert _truncate_match_text("short code line") == "short code line"


def test_truncate_strips_whitespace_first() -> None:
    assert _truncate_match_text("   spaced   ") == "spaced"


def test_truncate_exact_boundary_not_capped() -> None:
    text = "a" * MAX_MATCH_TEXT_CHARS
    assert _truncate_match_text(text) == text


def test_truncate_one_over_boundary_capped() -> None:
    text = "a" * (MAX_MATCH_TEXT_CHARS + 1)
    result = _truncate_match_text(text)
    assert result.endswith("...")
    assert len(result) == MAX_MATCH_TEXT_CHARS + 3


def test_truncate_massive_line_capped() -> None:
    text = "keyword" + "Z" * 50_000
    result = _truncate_match_text(text)
    assert result.endswith("...")
    assert len(result) == MAX_MATCH_TEXT_CHARS + 3


def test_truncate_strips_then_caps() -> None:
    text = "   " + "a" * (MAX_MATCH_TEXT_CHARS + 100) + "   "
    result = _truncate_match_text(text)
    assert result.endswith("...")
    assert len(result) == MAX_MATCH_TEXT_CHARS + 3


# ─── Integration: Python fallback ────────────────────────────


def test_python_fallback_truncates_long_line(tmp_path: Path) -> None:
    source = tmp_path / "minified.js"
    long_text = _long_line("target", MAX_MATCH_TEXT_CHARS + 500)
    source.write_text(long_text + "\n", encoding="utf-8")

    clear_file_cache()
    matches = backend._python_search(
        tmp_path, [{"path": "minified.js"}], "target", True, False, 10, None
    )
    assert len(matches) == 1
    assert matches[0]["text"].endswith("...")
    assert len(matches[0]["text"]) == MAX_MATCH_TEXT_CHARS + 3


def test_python_fallback_short_line_intact(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("def target():\n    return None\n", encoding="utf-8")

    clear_file_cache()
    matches = backend._python_search(
        tmp_path, [{"path": "main.py"}], "target", True, False, 10, None
    )
    assert matches[0]["text"] == "def target():"


def test_python_fallback_boundary_exact(tmp_path: Path) -> None:
    source = tmp_path / "exact.txt"
    text = "target" + "a" * (MAX_MATCH_TEXT_CHARS - len("target"))
    source.write_text(text + "\n", encoding="utf-8")

    clear_file_cache()
    matches = backend._python_search(
        tmp_path, [{"path": "exact.txt"}], "target", True, False, 10, None
    )
    assert len(matches[0]["text"]) == MAX_MATCH_TEXT_CHARS
    assert "..." not in matches[0]["text"]


# ─── Integration: ripgrep path ───────────────────────────────


def test_ripgrep_truncates_long_line(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "bundle.js"
    long_text = _long_line("target", MAX_MATCH_TEXT_CHARS + 500)
    source.write_text(long_text + "\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, long_text + "\n")])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)
    monkeypatch.setattr(backend, "_terminate_after", lambda *a: DummyTimer())

    result = backend._ripgrep(tmp_path, "target", True, False, 10, None, [{"path": "bundle.js"}])
    assert result.status == "ok"
    assert result.matches[0]["text"].endswith("...")
    assert len(result.matches[0]["text"]) == MAX_MATCH_TEXT_CHARS + 3


def test_ripgrep_short_line_intact(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target found here\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, "target found here\n")])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)
    monkeypatch.setattr(backend, "_terminate_after", lambda *a: DummyTimer())

    result = backend._ripgrep(tmp_path, "target", True, False, 10, None, [{"path": "main.py"}])
    assert result.matches[0]["text"] == "target found here"


# ─── Stress: batch long lines token economy ─────────────────


def test_stress_many_long_lines_bounded(tmp_path: Path) -> None:
    files: list[dict[str, Any]] = []
    for i in range(20):
        source = tmp_path / f"gen_{i}.js"
        lines = "\n".join(
            _long_line(f"target{i % 5}", MAX_MATCH_TEXT_CHARS + 800) for _ in range(50)
        )
        source.write_text(lines + "\n", encoding="utf-8")
        files.append({"path": f"gen_{i}.js"})

    clear_file_cache()
    matches = backend._python_search(tmp_path, files, "target0", True, False, 100, None)

    assert len(matches) <= 100
    max_text_len = max(len(m["text"]) for m in matches)
    assert max_text_len == MAX_MATCH_TEXT_CHARS + 3
    truncated = sum(1 for m in matches if m["text"].endswith("..."))
    assert truncated == len(matches), "all long-line matches should be truncated"


def test_stress_total_output_bounded(tmp_path: Path) -> None:
    source = tmp_path / "huge.sql"
    mega_line = "target" + "A" * 9_994
    source.write_text("\n".join([mega_line] * 500) + "\n", encoding="utf-8")

    clear_file_cache()
    matches = backend._python_search(
        tmp_path, [{"path": "huge.sql"}], "target", True, False, 50, None
    )

    assert len(matches) == 50
    total_chars = sum(len(m["text"]) for m in matches)
    ceiling = 50 * (MAX_MATCH_TEXT_CHARS + 3)
    assert total_chars <= ceiling, f"total {total_chars} exceeds ceiling {ceiling}"
    assert all(m["text"].endswith("...") for m in matches)
