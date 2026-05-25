from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService


class FakeProcess:
    def __init__(self, command: list[str], cwd: str) -> None:
        self.command = command
        self.cwd = cwd
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(_message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}))
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeProcessFactory:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, kwargs["cwd"])
        self.processes.append(process)
        return process


def test_lsp_start_reports_not_configured_without_project() -> None:
    service = AutoIndexService()

    result = service.start_lsp(timeout_seconds=0.1)

    assert result == "LSP|not_configured"


def test_lsp_start_detects_language_families_and_starts_available_servers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".clangd").write_text("CompileFlags:\n  Add: [-Wall]\n", encoding="utf-8")
    (project / "compile_commands.json").write_text("[]\n", encoding="utf-8")
    (project / "main.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    (project / "tool.py").write_text("def run():\n    return True\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}" if name == "clangd" else None, factory)
    service.enable(str(project), rebuild=True)

    result = service.start_lsp(timeout_seconds=0.2)
    second_result = service.start_lsp(timeout_seconds=0.2)

    assert result.splitlines()[0] == f"LSP|partial|{project.as_posix()}"
    assert "S:clangd/c-family/ready/files=1/ccdb+/.clangd+" in result
    assert "S:pyright/python/missing/files=1" in result
    assert "S:clangd/c-family/ready/files=1/ccdb+/.clangd+" in second_result
    assert len(factory.processes) == 1
    assert factory.processes[0].command == ["/bin/clangd"]


def test_lsp_shutdown_stops_all_sessions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    result = service.stop_lsp(timeout_seconds=0.1)

    assert result == f"LSP|stopped|{project.as_posix()}\nS:clangd/stopped"
    assert not service.lsp.sessions


def _message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
