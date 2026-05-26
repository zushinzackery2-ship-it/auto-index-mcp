from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService


class FakeProcess:
    def __init__(self, command: list[str], cwd: str, stdout: bytes | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout or _message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}))
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
    def __init__(self, stdout: bytes | None = None) -> None:
        self.stdout = stdout
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, kwargs["cwd"], self.stdout)
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
    assert "S:clangd/c-family/ready/files=1/ccdb=project:./.clangd+/cfg=project" in result
    assert "S:pyright/python/missing/files=1" in result
    assert "S:clangd/c-family/ready/files=1/ccdb=project:./.clangd+/cfg=project" in second_result
    assert len(factory.processes) == 1
    assert factory.processes[0].command[0] == "/bin/clangd"
    assert any(arg.startswith("--compile-commands-dir=") for arg in factory.processes[0].command)
    query_driver = next(arg for arg in factory.processes[0].command if arg.startswith("--query-driver="))
    assert ";" not in query_driver


def test_lsp_start_generates_managed_clangd_database_without_project_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    result = service.start_lsp(timeout_seconds=0.2)
    managed_db = project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json"

    assert "S:clangd/c-family/ready/files=1/ccdb=managed/.clangd-/cfg=basic-msvc/std=c++20" in result
    assert managed_db.exists()
    assert "main.cpp" in managed_db.read_text(encoding="utf-8")
    assert factory.processes[0].command[0] == "/bin/clangd"
    assert any(arg.startswith("--compile-commands-dir=") for arg in factory.processes[0].command)


def test_lsp_start_uses_project_compile_commands_before_managed_database(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "compile_commands.json").write_text("[]\n", encoding="utf-8")
    (project / "main.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    result = service.start_lsp(timeout_seconds=0.2)

    assert "S:clangd/c-family/ready/files=1/ccdb=project:./.clangd-/cfg=project" in result
    assert not (project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json").exists()


def test_lsp_start_bootstraps_managed_database_from_vcxproj(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source_dir = project / "UniversalSigBypasser"
    source_dir.mkdir(parents=True)
    (source_dir / "dllmain.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    (source_dir / "UniversalSigBypasser.vcxproj").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Release|x64'">
    <ClCompile>
      <PreprocessorDefinitions>_CRT_SECURE_NO_WARNINGS;NDEBUG;UNIVERSALSIGBYPASSER_EXPORTS;_WINDOWS;_USRDLL;%(PreprocessorDefinitions)</PreprocessorDefinitions>
      <AdditionalIncludeDirectories>include;$(ProjectDir)generated;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>
      <LanguageStandard>stdcpp20</LanguageStandard>
    </ClCompile>
  </ItemDefinitionGroup>
</Project>
""",
        encoding="utf-8",
    )

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    result = service.start_lsp(timeout_seconds=0.2)
    managed_db = project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json"
    payload = managed_db.read_text(encoding="utf-8")

    assert "S:clangd/c-family/ready/files=1/ccdb=managed/.clangd-/cfg=vcxproj/std=c++20" in result
    assert "/DUNIVERSALSIGBYPASSER_EXPORTS" in payload
    assert "/std:c++20" in payload
    assert '"arguments"' in payload


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


def test_lsp_check_reports_not_started(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    assert service.check_lsp(timeout_seconds=0.1) == "CHK|not_started"


def test_lsp_check_returns_compact_diagnostics_for_one_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    Missing value;\n    return 0;\n}\n", encoding="utf-8")
    stdout = _message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + _message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": source.as_uri(),
                "diagnostics": [
                    {
                        "severity": 1,
                        "range": {"start": {"line": 2, "character": 4}},
                        "message": "unknown type name 'Missing'",
                    }
                ],
            },
        }
    )
    factory = FakeProcessFactory(stdout)
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    result = service.check_lsp("main.cpp", timeout_seconds=0.2)

    assert result == "CHK|issues|count=1|files=1|limit=80\nE|main.cpp|3:5|unknown type name 'Missing'"


def test_lsp_check_returns_clean_for_empty_diagnostics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    stdout = _message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + _message(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": source.as_uri(), "diagnostics": []},
        }
    )
    factory = FakeProcessFactory(stdout)
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    result = service.check_lsp("main.cpp", timeout_seconds=0.2)

    assert result == "CHK|clean|files=1"


def _message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
