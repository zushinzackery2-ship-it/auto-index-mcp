from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from auto_index_mcp.core import clangd_bootstrap
from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService

from tests.lsp_fixtures import FakeProcessFactory, messages_from_stream, publish_after_document_message


def test_managed_clangd_database_uses_vcxproj_that_owns_source(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = project / "Rei-OS" / "src" / "core" / "debugger" / "veh_debugger.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#include <algorithm>\n"
        "void copy_context(unsigned long long ctx_size)\n"
        "{\n"
        "    unsigned long long current_context = 0;\n"
        "    unsigned long long copy_size = std::min(ctx_size, sizeof(current_context));\n"
        "}\n",
        encoding="utf-8",
    )
    wrong_dir = project / "AAA-Unrelated"
    wrong_dir.mkdir(parents=True)
    (wrong_dir / "unrelated.cpp").write_text("int unrelated;\n", encoding="utf-8")
    (wrong_dir / "AAA-Unrelated.vcxproj").write_text(
        _vcxproj(
            wrong_dir / "unrelated.cpp",
            "WRONG_EXPORTS;%(PreprocessorDefinitions)",
            "wrong_include;%(AdditionalIncludeDirectories)",
        ),
        encoding="utf-8",
    )
    real_dir = project / "Rei-OS" / "build" / "src" / "core"
    real_dir.mkdir(parents=True)
    (real_dir / "rei_core.vcxproj").write_text(
        _vcxproj(
            source,
            "NOMINMAX;WIN32_LEAN_AND_MEAN;REI_CORE_BUILD_DLL=1;REI_HAS_LUA=1;%(PreprocessorDefinitions)",
            "$(SolutionDir)Rei-OS\\src\\core;$(ProjectDir)generated;%(AdditionalIncludeDirectories)",
        ),
        encoding="utf-8",
    )
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    result = service.start_lsp(timeout_seconds=0.2)
    managed_db = project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json"
    rows = json.loads(managed_db.read_text(encoding="utf-8"))
    row = next(item for item in rows if item["file"] == str(source.resolve()))
    arguments = row["arguments"]

    assert "S:clangd/c-family/ready/files=2/ccdb=managed/.clangd-/cfg=vcxproj/std=c++20" in result
    assert "/DNOMINMAX" in arguments
    assert "/DWIN32_LEAN_AND_MEAN" in arguments
    assert "/DREI_CORE_BUILD_DLL=1" in arguments
    assert "/DWRONG_EXPORTS" not in arguments
    assert "/EHsc" in arguments
    assert any(argument.startswith("/I") and "Rei-OS" in argument for argument in arguments)


def test_managed_clangd_database_maps_vcxproj_exception_handling(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = project / "src" / "worker.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "void run()\n"
        "{\n"
        "    throw 1;\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "worker.vcxproj").write_text(
        _vcxproj(source, "WORKER_EXPORTS;%(PreprocessorDefinitions)", "", exception_handling="Async"),
        encoding="utf-8",
    )
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    service.start_lsp(timeout_seconds=0.2)
    managed_db = project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json"
    rows = json.loads(managed_db.read_text(encoding="utf-8"))
    arguments = rows[0]["arguments"]

    assert "/EHa" in arguments
    assert "/EHsc" not in arguments


def test_managed_clangd_database_is_replaced_atomically(tmp_path: Path, monkeypatch: Any) -> None:
    project = tmp_path / "project"
    source = project / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    replaced: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def spy_replace(source_path: str | Path, target_path: str | Path) -> None:
        replaced.append((Path(source_path), Path(target_path)))
        original_replace(source_path, target_path)

    monkeypatch.setattr(clangd_bootstrap.os, "replace", spy_replace)
    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    service.start_lsp(timeout_seconds=0.2)
    managed_db = project / ".auto-index-mcp" / "lsp" / "clangd" / "compile_commands.json"

    assert replaced
    assert replaced[-1][0].name.startswith(".compile_commands.json.")
    assert replaced[-1][1] == managed_db
    assert json.loads(managed_db.read_text(encoding="utf-8"))[0]["file"] == str(source.resolve())


def test_full_lsp_check_skips_c_family_files_outside_managed_clangd_targets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    owned = project / "app" / "owned.cpp"
    platform_noise = project / "reference" / "linux_only.c"
    owned.parent.mkdir(parents=True)
    platform_noise.parent.mkdir(parents=True)
    owned.write_text("int owned()\n{\n    return 1;\n}\n", encoding="utf-8")
    platform_noise.write_text("#include <unistd.h>\nint linux_only;\n", encoding="utf-8")
    (project / "app.vcxproj").write_text(
        _vcxproj(owned, "APP_EXPORTS;%(PreprocessorDefinitions)", ""),
        encoding="utf-8",
    )
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
                    "uri": owned.as_uri(),
                    "version": message["params"]["textDocument"]["version"],
                    "diagnostics": [],
                },
            }
        ),
    )
    result = service.check_lsp(timeout_seconds=0.2)
    opened_uris = [
        message["params"]["textDocument"]["uri"]
        for message in messages_from_stream(factory.processes[0].stdin.getvalue())
        if message.get("method") == "textDocument/didOpen"
    ]

    assert result == "CHK|clean|files=1"
    assert owned.as_uri() in opened_uris
    assert platform_noise.as_uri() not in opened_uris


def _vcxproj(source: Path, definitions: str, includes: str, exception_handling: str = "") -> str:
    exception_node = f"<ExceptionHandling>{exception_handling}</ExceptionHandling>" if exception_handling else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Release|x64'">
    <ClCompile>
      <PreprocessorDefinitions>{definitions}</PreprocessorDefinitions>
      <AdditionalIncludeDirectories>{includes}</AdditionalIncludeDirectories>
      {exception_node}
      <LanguageStandard>stdcpp20</LanguageStandard>
    </ClCompile>
  </ItemDefinitionGroup>
  <ItemGroup>
    <ClCompile Include="{source.as_posix()}" />
  </ItemGroup>
</Project>
"""
