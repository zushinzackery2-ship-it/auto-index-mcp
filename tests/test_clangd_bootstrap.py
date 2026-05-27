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

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class FakeProcessFactory:
    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, kwargs["cwd"])
        self.processes.append(process)
        return process


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
    assert any(argument.startswith("/I") and "Rei-OS" in argument for argument in arguments)


def _vcxproj(source: Path, definitions: str, includes: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <ItemDefinitionGroup Condition="'$(Configuration)|$(Platform)'=='Release|x64'">
    <ClCompile>
      <PreprocessorDefinitions>{definitions}</PreprocessorDefinitions>
      <AdditionalIncludeDirectories>{includes}</AdditionalIncludeDirectories>
      <LanguageStandard>stdcpp20</LanguageStandard>
    </ClCompile>
  </ItemDefinitionGroup>
  <ItemGroup>
    <ClCompile Include="{source.as_posix()}" />
  </ItemGroup>
</Project>
"""


def _message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
