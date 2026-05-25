from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService


class StressProcess:
    def __init__(self, command: list[str], cwd: str) -> None:
        self.command = command
        self.cwd = cwd
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(_message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}))
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.waits = 0
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.waits += 1
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class StressProcessFactory:
    def __init__(self) -> None:
        self.processes: list[StressProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> StressProcess:
        process = StressProcess(command, kwargs["cwd"])
        self.processes.append(process)
        return process


def test_lsp_auto_detection_survives_large_mixed_language_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".clangd").write_text("CompileFlags:\n  Add: [-Wall]\n", encoding="utf-8")
    (project / "compile_commands.json").write_text("[]\n", encoding="utf-8")
    _write_sources(project, count_per_family=200)

    factory = StressProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/fake/bin/{name}", factory)
    index_result = service.enable(str(project), rebuild=True)

    first = service.start_lsp(timeout_seconds=0.2)
    second = service.start_lsp(timeout_seconds=0.2)
    stopped = service.stop_lsp(timeout_seconds=0.05)

    assert index_result["file_count"] == 1201
    assert first.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert "S:clangd/c-family/ready/files=200/ccdb+/.clangd+" in first
    assert "S:pyright/python/ready/files=200" in first
    assert "S:tsserver/js-ts/ready/files=400" in first
    assert "S:rust-analyzer/rust/ready/files=200" in first
    assert "S:gopls/go/ready/files=200" in first
    assert second == first
    assert stopped.startswith(f"LSP|stopped|{project.as_posix()}")
    assert len(factory.processes) == 5
    assert not any(process.killed for process in factory.processes)
    assert not service.lsp.sessions


def test_lsp_repeated_start_shutdown_cycles_do_not_leak_sessions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_sources(project, count_per_family=20)

    factory = StressProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/fake/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    for _ in range(25):
        started = service.start_lsp(timeout_seconds=0.2)
        assert started.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
        assert len(service.lsp.sessions) == 5
        stopped = service.stop_lsp(timeout_seconds=0.05)
        assert stopped.startswith(f"LSP|stopped|{project.as_posix()}")
        assert not service.lsp.sessions

    assert len(factory.processes) == 125
    assert not any(process.killed for process in factory.processes)


def _write_sources(project: Path, count_per_family: int) -> None:
    folders = {
        "cpp": project / "cpp",
        "python": project / "python",
        "ts": project / "ts",
        "rust": project / "rust",
        "go": project / "go",
    }
    for folder in folders.values():
        folder.mkdir()

    for index in range(count_per_family):
        (folders["cpp"] / f"unit_{index}.cpp").write_text(f"int value_{index}()\n{{\n    return {index};\n}}\n", encoding="utf-8")
        (folders["python"] / f"tool_{index}.py").write_text(f"def tool_{index}():\n    return {index}\n", encoding="utf-8")
        (folders["ts"] / f"view_{index}.ts").write_text(f"export const view_{index} = {index};\n", encoding="utf-8")
        (folders["ts"] / f"widget_{index}.js").write_text(f"export const widget_{index} = {index};\n", encoding="utf-8")
        (folders["rust"] / f"lib_{index}.rs").write_text(f"pub fn lib_{index}() -> i32 {{ {index} }}\n", encoding="utf-8")
        (folders["go"] / f"main_{index}.go").write_text(f"package main\n\nfunc value{index}() int {{ return {index} }}\n", encoding="utf-8")


def _message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
