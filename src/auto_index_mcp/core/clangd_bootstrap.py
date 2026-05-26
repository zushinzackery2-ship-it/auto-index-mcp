from __future__ import annotations

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ClangdBootstrap:
    args: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class CompileProfile:
    defines: tuple[str, ...]
    includes: tuple[str, ...]
    standard: str
    mode: str


CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm", ".cu"}
MSBUILD_NS = {"msb": "http://schemas.microsoft.com/developer/msbuild/2003"}


def prepare_clangd(root: Path, files: list[dict[str, Any]]) -> ClangdBootstrap:
    cpp_files = [item for item in files if item.get("extension", "").lower() in CPP_EXTENSIONS or item.get("language") in {"c", "cpp"}]
    if not cpp_files:
        return ClangdBootstrap((), ())

    project_ccdb = _find_project_compile_commands(root)
    project_clangd = root / ".clangd"
    if project_ccdb:
        return ClangdBootstrap((f"--compile-commands-dir={project_ccdb.parent}", *_query_driver_args()), (f"ccdb=project:{_rel(root, project_ccdb.parent)}", f".clangd{_presence(project_clangd)}", "cfg=project"))

    managed_dir = root / ".auto-index-mcp" / "lsp" / "clangd"
    managed_dir.mkdir(parents=True, exist_ok=True)
    profile = _profile_from_vcxproj(root) or _default_profile()
    source_files = [item for item in cpp_files if item.get("extension", "").lower() in {".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".cu"}]
    if not source_files:
        source_files = cpp_files
    _write_compile_commands(root, managed_dir / "compile_commands.json", source_files, profile)
    return ClangdBootstrap(
        (f"--compile-commands-dir={managed_dir}", *_query_driver_args()),
        ("ccdb=managed", f".clangd{_presence(project_clangd)}", f"cfg={profile.mode}", f"std={profile.standard}"),
    )


def _find_project_compile_commands(root: Path) -> Path | None:
    candidates = [root / "compile_commands.json"]
    candidates.extend(root.glob("build/**/compile_commands.json"))
    candidates.extend(root.glob("out/**/compile_commands.json"))
    for path in candidates:
        if path.exists():
            return path.resolve()
    return None


def _profile_from_vcxproj(root: Path) -> CompileProfile | None:
    projects = sorted(root.rglob("*.vcxproj"))
    if not projects:
        return None
    for project in projects:
        profile = _read_vcxproj_profile(root, project, "Release", "x64")
        if profile:
            return profile
    return _read_vcxproj_profile(root, projects[0], "", "") or _default_profile()


def _read_vcxproj_profile(root: Path, path: Path, configuration: str, platform: str) -> CompileProfile | None:
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return None
    best = None
    for group in tree.findall("msb:ItemDefinitionGroup", MSBUILD_NS):
        condition = group.attrib.get("Condition", "")
        if configuration and platform and f"{configuration}|{platform}" not in condition:
            continue
        cl_compile = group.find("msb:ClCompile", MSBUILD_NS)
        if cl_compile is None:
            continue
        best = cl_compile
        break
    if best is None:
        best = tree.find(".//msb:ClCompile", MSBUILD_NS)
    if best is None:
        return None
    defines = _split_msbuild_list(_text(best, "PreprocessorDefinitions"))
    includes = _split_msbuild_paths(_text(best, "AdditionalIncludeDirectories"), path.parent, root)
    standard = _standard(_text(best, "LanguageStandard"))
    clean_defines = tuple(item for item in defines if not item.startswith("%("))
    return CompileProfile(clean_defines, includes, standard, "vcxproj")


def _write_compile_commands(root: Path, output: Path, files: list[dict[str, Any]], profile: CompileProfile) -> None:
    rows = []
    for item in files:
        file_path = (root / item["path"]).resolve()
        command = _command_for_file(file_path, profile)
        rows.append({"directory": str(root), "file": str(file_path), "arguments": command, "command": subprocess.list2cmdline(command)})
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _command_for_file(file_path: Path, profile: CompileProfile) -> list[str]:
    mode = "/TC" if file_path.suffix.lower() == ".c" else "/TP"
    command = [_compiler(), "/nologo", mode, f"/std:{profile.standard}"]
    command.extend(f"/D{define}" for define in _unique(("WIN32", "_WINDOWS", *profile.defines)))
    command.extend(f"/I{include}" for include in _unique(profile.includes))
    command.extend(["/c", str(file_path)])
    return command


def _query_driver_args() -> tuple[str, ...]:
    drivers = [_compiler()]
    cl = shutil.which("cl.exe")
    clang_cl = shutil.which("clang-cl.exe")
    if cl:
        drivers.append(cl)
    if clang_cl:
        drivers.append(clang_cl)
    return (f"--query-driver={','.join(dict.fromkeys(drivers))}",)


def _compiler() -> str:
    return shutil.which("clang-cl.exe") or shutil.which("cl.exe") or "clang-cl.exe"


def _default_profile() -> CompileProfile:
    return CompileProfile(("_CRT_SECURE_NO_WARNINGS", "NDEBUG", "_WINDOWS", "_USRDLL"), (), "c++20", "basic-msvc")


def _split_msbuild_list(text: str) -> tuple[str, ...]:
    values = []
    for item in text.replace("\n", "").split(";"):
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return tuple(dict.fromkeys(values))


def _split_msbuild_paths(text: str, project_dir: Path, solution_dir: Path) -> tuple[str, ...]:
    values = []
    for value in _split_msbuild_list(text):
        if value.startswith("%("):
            continue
        expanded = _expand_msbuild_path(value, project_dir, solution_dir)
        if expanded:
            values.append(expanded)
    return tuple(dict.fromkeys(values))


def _expand_msbuild_path(value: str, project_dir: Path, solution_dir: Path) -> str:
    value = value.replace("$(ProjectDir)", str(project_dir) + "\\")
    value = value.replace("$(SolutionDir)", str(solution_dir) + "\\")
    value = re.sub(r"\$\([^)]+\)", "", value).strip()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = project_dir / value
    return str(path.resolve())


def _standard(value: str) -> str:
    return {
        "stdcpp14": "c++14",
        "stdcpp17": "c++17",
        "stdcpp20": "c++20",
        "stdcpplatest": "c++latest",
    }.get(value.strip(), "c++20")


def _text(parent: ET.Element, name: str) -> str:
    node = parent.find(f"msb:{name}", MSBUILD_NS)
    return (node.text or "").strip() if node is not None else ""


def _presence(path: Path) -> str:
    return "+" if path.exists() else "-"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))
