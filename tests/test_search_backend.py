from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_index_mcp.search import backend


class StreamingProcess:
    def __init__(self, lines: list[str]) -> None:
        self.stdout = iter(lines)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class StreamingFactory:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.processes: list[StreamingProcess] = []
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> StreamingProcess:
        self.commands.append(command)
        self.kwargs.append(kwargs)
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


def test_ripgrep_stops_process_after_search_limit(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target\n" * 20, encoding="utf-8")
    lines = [_rg_match(source, index, "target\n") for index in range(1, 21)]
    factory = StreamingFactory(lines)

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    result = backend._ripgrep(tmp_path, "target", True, False, 3, None, [{"path": "main.py"}])

    assert result.status == "ok"
    assert len(result.matches) == 3
    assert factory.processes[0].terminated is True
    assert factory.processes[0].killed is False


def test_ripgrep_searches_indexed_files_not_project_root(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, "target\n")])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    result = backend._ripgrep(tmp_path, "target", True, False, 10, None, [{"path": "main.py"}])

    assert result.matches == [{"path": "main.py", "line": 1, "text": "target"}]
    assert str(tmp_path) not in factory.commands[0]
    assert str(source) in factory.commands[0]


def test_ripgrep_maps_child_index_source_paths_to_prefixed_paths(tmp_path: Path, monkeypatch) -> None:
    child = tmp_path / "child"
    child.mkdir()
    source = child / "child.py"
    source.write_text("target\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, "target\n")])

    monkeypatch.setattr(backend.shutil, "which", lambda name: "rg")
    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    backend_name, matches = backend.search_text(
        tmp_path,
        [{"path": "child/child.py", "source_root": str(child), "source_path": "child.py"}],
        "target",
        True,
        False,
        10,
    )

    assert backend_name == "ripgrep-indexed-files"
    assert matches == [{"path": "child/child.py", "line": 1, "text": "target"}]


def test_ripgrep_uses_utf8_replacement_decoding(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("目标\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, "目标\n")])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    result = backend._ripgrep(tmp_path, "目标", True, False, 10, None, [{"path": "main.py"}])

    assert result.matches == [{"path": "main.py", "line": 1, "text": "目标"}]
    assert factory.kwargs[0]["encoding"] == "utf-8"
    assert factory.kwargs[0]["errors"] == "replace"


def test_ripgrep_json_parser_handles_colon_digit_text(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("value:12:target\n", encoding="utf-8")
    factory = StreamingFactory([_rg_match(source, 1, "value:12:target\n")])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    result = backend._ripgrep(tmp_path, "target", True, False, 10, None, [{"path": "main.py"}])

    assert result.status == "ok"
    assert result.matches == [{"path": "main.py", "line": 1, "text": "value:12:target"}]


def test_ripgrep_timeout_does_not_python_fallback(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target\n", encoding="utf-8")
    factory = StreamingFactory([])

    def trigger_timeout(process: StreamingProcess, timeout_seconds: float, timed_out: Any) -> DummyTimer:
        _ = timeout_seconds
        timed_out.set()
        process.terminate()
        return DummyTimer()

    def fail_python_search(*args: Any, **kwargs: Any) -> list[dict]:
        _ = args, kwargs
        raise AssertionError("timeout must not fall back to Python file scanning")

    monkeypatch.setattr(backend.shutil, "which", lambda name: "rg")
    monkeypatch.setattr(backend.subprocess, "Popen", factory)
    monkeypatch.setattr(backend, "_terminate_after", trigger_timeout)
    monkeypatch.setattr(backend, "_python_search", fail_python_search)

    backend_name, matches = backend.search_text(
        tmp_path,
        [{"path": "main.py"}],
        "target",
        True,
        False,
        10,
    )

    assert backend_name == "ripgrep-timeout"
    assert matches == []
