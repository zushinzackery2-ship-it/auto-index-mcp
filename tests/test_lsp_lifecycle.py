from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_index_mcp.core import lsp_resolver
from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.lsp_resolver import resolve_lsp_executable
from auto_index_mcp.core.service import AutoIndexService

from tests.lsp_fixtures import FakeProcessFactory, publish_after_document_message


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


def test_lsp_resolver_prefers_bundled_clangd() -> None:
    resolved = resolve_lsp_executable("clangd")

    assert resolved is not None
    assert resolved.replace("\\", "/").endswith("third-party/clangd_22.1.0/bin/clangd.exe")


def test_lsp_resolver_prefers_managed_lsp_tools(tmp_path: Path, monkeypatch: Any) -> None:
    tool_root = tmp_path / "tool-root"
    project_root = tmp_path / "project-root"
    scripts_dir = tool_root / ".venv" / "Scripts"
    npm_bin_dir = tool_root / ".auto-index-mcp" / "lsp" / "npm" / "node_modules" / ".bin"
    go_bin_dir = tool_root / ".auto-index-mcp" / "lsp" / "go" / "bin"
    scripts_dir.mkdir(parents=True)
    npm_bin_dir.mkdir(parents=True)
    go_bin_dir.mkdir(parents=True)
    pyright = scripts_dir / "pyright-langserver.exe"
    tsserver = npm_bin_dir / "typescript-language-server.cmd"
    gopls = go_bin_dir / "gopls.exe"
    pyright.write_text("", encoding="utf-8")
    tsserver.write_text("", encoding="utf-8")
    gopls.write_text("", encoding="utf-8")
    monkeypatch.setattr(lsp_resolver, "_repo_root", lambda: tool_root)

    resolved_pyright = lsp_resolver.resolve_lsp_executable("pyright-langserver", project_root)
    resolved_tsserver = lsp_resolver.resolve_lsp_executable("typescript-language-server", project_root)
    resolved_gopls = lsp_resolver.resolve_lsp_executable("gopls", project_root)

    assert resolved_pyright == str(pyright)
    assert resolved_tsserver == str(tsserver)
    assert resolved_gopls == str(gopls)


def test_lsp_check_reports_missing_servers_when_no_session_can_start(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "view.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (project / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: None, FakeProcessFactory())
    service.enable(str(project), rebuild=True)

    started = service.start_lsp(timeout_seconds=0.2)
    checked = service.check_lsp(timeout_seconds=0.2)

    assert started.splitlines()[0] == f"LSP|unavailable|{project.as_posix()}"
    assert "S:tsserver/js-ts/missing/files=1" in started
    assert "S:gopls/go/missing/files=1" in started
    assert checked == "CHK|unavailable|servers=gopls:missing:1,tsserver:missing:1"


def test_lsp_check_keeps_missing_server_details_when_other_sessions_are_ready(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "tool.py").write_text("value = 1\n", encoding="utf-8")
    (project / "view.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (project / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: "/bin/pyright-langserver" if name == "pyright-langserver" else None, factory)
    service.enable(str(project), rebuild=True)

    started = service.start_lsp(timeout_seconds=0.2)
    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["pyright"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": (project / "tool.py").as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    checked = service.check_lsp(timeout_seconds=0.2)

    assert started.splitlines()[0] == f"LSP|partial|{project.as_posix()}"
    assert "S:pyright/python/ready/files=1" in started
    assert "S:tsserver/js-ts/missing/files=1" in started
    assert "S:gopls/go/missing/files=1" in started
    assert checked == "CHK|partial|files=1|unchecked=2|servers=gopls:missing:1,tsserver:missing:1"


def test_lsp_check_reports_server_that_exited_after_start(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "view.ts").write_text("export const value = 1;\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: "/bin/typescript-language-server", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)
    factory.processes[0].returncode = 1

    checked = service.check_lsp(timeout_seconds=0.2)

    assert checked == "CHK|unavailable|servers=tsserver:error:1"


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
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [
                        {
                            "severity": 1,
                            "range": {"start": {"line": 2, "character": 4}},
                            "message": "unknown type name 'Missing'",
                        }
                    ],
                },
            }
        ),
    )
    result = service.check_lsp("main.cpp", timeout_seconds=0.2)

    assert result == "CHK|issues|count=1|files=1|limit=80\nE|main.cpp|3:5|unknown type name 'Missing'"


def test_lsp_check_returns_clean_for_empty_diagnostics(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=0.2)

    publish_after_document_message(
        factory,
        "textDocument/didOpen",
        1,
        lambda message: service.lsp.sessions["clangd"]._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": source.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    result = service.check_lsp("main.cpp", timeout_seconds=0.2)

    assert result == "CHK|clean|files=1"
