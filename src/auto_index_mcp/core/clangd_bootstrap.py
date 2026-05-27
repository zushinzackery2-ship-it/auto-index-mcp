from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .msbuild_profile import CompileProfile, VcxprojProfile, default_profile, load_vcxproj_profiles, profile_for_file


@dataclass(frozen=True)
class ClangdBootstrap:
    args: tuple[str, ...]
    flags: tuple[str, ...]


CPP_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm", ".cu"}


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
    fallback = default_profile()
    profiles = load_vcxproj_profiles(root)
    status_profile = profiles[0].profile if profiles else fallback
    source_files = [item for item in cpp_files if item.get("extension", "").lower() in {".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".cu"}]
    if not source_files:
        source_files = cpp_files
    _write_compile_commands(root, managed_dir / "compile_commands.json", source_files, profiles, fallback)
    return ClangdBootstrap(
        (f"--compile-commands-dir={managed_dir}", *_query_driver_args()),
        ("ccdb=managed", f".clangd{_presence(project_clangd)}", f"cfg={status_profile.mode}", f"std={status_profile.standard}"),
    )


def _find_project_compile_commands(root: Path) -> Path | None:
    candidates = [root / "compile_commands.json"]
    candidates.extend(root.glob("build/**/compile_commands.json"))
    candidates.extend(root.glob("out/**/compile_commands.json"))
    for path in candidates:
        if path.exists():
            return path.resolve()
    return None


def _write_compile_commands(
    root: Path,
    output: Path,
    files: list[dict[str, Any]],
    profiles: tuple[VcxprojProfile, ...],
    fallback: CompileProfile,
) -> None:
    rows = []
    for item in files:
        file_path = (root / item["path"]).resolve()
        profile = profile_for_file(file_path, profiles, fallback)
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


def _presence(path: Path) -> str:
    return "+" if path.exists() else "-"


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix() or "."
    except ValueError:
        return path.as_posix()


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))
