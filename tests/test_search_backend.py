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

    def __call__(self, command: list[str], **kwargs: Any) -> StreamingProcess:
        _ = kwargs
        self.commands.append(command)
        process = StreamingProcess(self.lines)
        self.processes.append(process)
        return process


def test_ripgrep_stops_process_after_search_limit(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target\n" * 20, encoding="utf-8")
    lines = [f"{source}:{index}:target" for index in range(1, 21)]
    factory = StreamingFactory(lines)

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    matches = backend._ripgrep(tmp_path, "target", True, False, 3, None, [{"path": "main.py"}])

    assert matches is not None
    assert len(matches) == 3
    assert factory.processes[0].terminated is True
    assert factory.processes[0].killed is False


def test_ripgrep_searches_indexed_files_not_project_root(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "main.py"
    source.write_text("target\n", encoding="utf-8")
    factory = StreamingFactory([f"{source}:1:target"])

    monkeypatch.setattr(backend.subprocess, "Popen", factory)

    matches = backend._ripgrep(tmp_path, "target", True, False, 10, None, [{"path": "main.py"}])

    assert matches == [{"path": "main.py", "line": 1, "text": "target"}]
    assert str(tmp_path) not in factory.commands[0]
    assert str(source) in factory.commands[0]


def test_ripgrep_maps_child_index_source_paths_to_prefixed_paths(tmp_path: Path, monkeypatch) -> None:
    child = tmp_path / "child"
    child.mkdir()
    source = child / "child.py"
    source.write_text("target\n", encoding="utf-8")
    factory = StreamingFactory([f"{source}:1:target"])

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
