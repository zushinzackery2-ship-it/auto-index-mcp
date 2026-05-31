from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


MSBUILD_NS = {"msb": "http://schemas.microsoft.com/developer/msbuild/2003"}


@dataclass(frozen=True)
class CompileProfile:
    defines: tuple[str, ...]
    includes: tuple[str, ...]
    standard: str
    mode: str
    flags: tuple[str, ...]
    target: str = ""


@dataclass(frozen=True)
class VcxprojProfile:
    project: Path
    sources: tuple[Path, ...]
    source_keys: frozenset[str]
    profile: CompileProfile


def load_vcxproj_profiles(root: Path) -> tuple[VcxprojProfile, ...]:
    profiles = []
    for project in sorted(root.rglob("*.vcxproj")):
        tree = _parse_vcxproj(project)
        if tree is None:
            continue
        profile = _read_vcxproj_profile(root, project, tree, "Release", "x64")
        if profile is None:
            profile = _read_vcxproj_profile(root, project, tree, "", "")
        if profile is None:
            continue
        sources = _source_paths_from_vcxproj(root, project, tree)
        profiles.append(VcxprojProfile(project.resolve(), sources, frozenset(_path_key(item) for item in sources), profile))
    return tuple(profiles)


def profile_for_file(file_path: Path, profiles: tuple[VcxprojProfile, ...], fallback: CompileProfile) -> CompileProfile:
    owner = explicit_profile_for_file(file_path, profiles)
    if owner:
        return owner
    if not profiles:
        return fallback
    if len(profiles) == 1:
        return profiles[0].profile
    score, _, owner = max((_ownership_score(file_path, candidate), index, candidate) for index, candidate in enumerate(profiles))
    return owner.profile if score > 0 else fallback


def explicit_profile_for_file(file_path: Path, profiles: tuple[VcxprojProfile, ...]) -> CompileProfile | None:
    file_key = _path_key(file_path)
    for candidate in profiles:
        if file_key in candidate.source_keys:
            return candidate.profile
    return None


def default_profile() -> CompileProfile:
    return CompileProfile(("_CRT_SECURE_NO_WARNINGS", "NDEBUG", "_WINDOWS", "_USRDLL"), (), "c++20", "basic-msvc", ("/EHsc",))


def _parse_vcxproj(path: Path) -> ET.ElementTree | None:
    try:
        return ET.parse(path)
    except (OSError, ET.ParseError):
        return None


def _read_vcxproj_profile(
    root: Path,
    path: Path,
    tree: ET.ElementTree,
    configuration: str,
    platform: str,
) -> CompileProfile | None:
    best = None
    selected_condition = ""
    for group in _children(tree.getroot(), "ItemDefinitionGroup"):
        condition = group.attrib.get("Condition", "")
        if configuration and platform and f"{configuration}|{platform}" not in condition:
            continue
        cl_compile = _child(group, "ClCompile")
        if cl_compile is None:
            continue
        best = cl_compile
        selected_condition = condition
        break
    if best is None:
        best = _descendant(tree.getroot(), "ClCompile")
    if best is None:
        return None
    solution_dir = _solution_dir(root, path)
    defines = _split_msbuild_list(_text(best, "PreprocessorDefinitions"))
    includes = _split_msbuild_paths(_text(best, "AdditionalIncludeDirectories"), path.parent, solution_dir)
    target = ""
    effective_platform = platform or _platform_from_condition(selected_condition) or _project_platform(tree)
    if _is_kernel_driver_project(tree):
        includes = (*includes, *_windows_kernel_include_paths())
        defines = (*defines, *_kernel_arch_defines(effective_platform))
        target = _clang_target(effective_platform)
    standard = _standard(_text(best, "LanguageStandard"))
    flags = _compiler_flags(_text(best, "ExceptionHandling"), _text(best, "AdditionalOptions"))
    clean_defines = tuple(item for item in defines if not item.startswith("%("))
    return CompileProfile(clean_defines, includes, standard, "vcxproj", flags, target)


def _is_kernel_driver_project(tree: ET.ElementTree) -> bool:
    root = tree.getroot()
    return any(
        "windowskernelmodedriver" in _text(group, "PlatformToolset").lower()
        or _text(group, "ConfigurationType").lower() == "driver"
        or bool(_text(group, "DriverType"))
        for group in _children(root, "PropertyGroup")
    )


def _windows_kernel_include_paths() -> tuple[str, ...]:
    for base in _windows_kernel_include_roots():
        paths = _windows_kernel_include_paths_from(base)
        if paths:
            return paths
    return ()


def _windows_kernel_include_roots() -> tuple[Path, ...]:
    candidates = []
    override = os.environ.get("AUTO_INDEX_WDK_INCLUDE_ROOT")
    if override:
        candidates.append(Path(override))
    sdk_dir = os.environ.get("WindowsSdkDir")
    if sdk_dir:
        candidates.append(Path(sdk_dir) / "Include")
    candidates.append(Path("C:/Program Files (x86)/Windows Kits/10/Include"))
    return tuple(dict.fromkeys(path for path in candidates if str(path)))


def _windows_kernel_include_paths_from(base: Path) -> tuple[str, ...]:
    sdk_version = os.environ.get("WindowsSDKVersion", "").strip("\\/")
    try:
        if sdk_version:
            requested = base / sdk_version
            if (requested / "km" / "ntifs.h").exists():
                return _kernel_include_tuple(requested)
        versions = [path for path in base.iterdir() if path.is_dir() and (path / "km" / "ntifs.h").exists()]
    except OSError:
        return ()
    if not versions:
        return ()
    latest = sorted(versions, key=lambda path: _version_key(path.name))[-1]
    return _kernel_include_tuple(latest)


def _kernel_include_tuple(version_root: Path) -> tuple[str, ...]:
    return tuple(str((version_root / name).resolve()) for name in ("km", "shared", "ucrt") if (version_root / name).is_dir())


def _kernel_arch_defines(platform: str) -> tuple[str, ...]:
    cleaned = platform.strip().lower()
    if cleaned == "x64":
        return ("_AMD64_", "AMD64", "_M_AMD64=100", "_WIN64")
    if cleaned == "win32":
        return ("_X86_", "i386", "_M_IX86=600")
    if cleaned == "arm64":
        return ("_ARM64_", "ARM64", "_M_ARM64=1", "_WIN64")
    if cleaned == "arm":
        return ("_ARM_", "ARM", "_M_ARM=7")
    return ()


def _clang_target(platform: str) -> str:
    cleaned = platform.strip().lower()
    return {
        "x64": "x86_64-pc-windows-msvc",
        "win32": "i686-pc-windows-msvc",
        "arm64": "aarch64-pc-windows-msvc",
        "arm": "arm-pc-windows-msvc",
    }.get(cleaned, "")


def _platform_from_condition(condition: str) -> str:
    match = re.search(r"'\$\(Configuration\)\|\$\(Platform\)'\s*==\s*'[^']*\|([^']+)'", condition)
    return match.group(1) if match else ""


def _project_platform(tree: ET.ElementTree) -> str:
    for group in _children(tree.getroot(), "PropertyGroup"):
        platform = _platform_from_condition(group.attrib.get("Condition", ""))
        if platform:
            return platform
    return ""


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in value.split("."))


def _source_paths_from_vcxproj(root: Path, path: Path, tree: ET.ElementTree) -> tuple[Path, ...]:
    solution_dir = _solution_dir(root, path)
    sources = []
    for node in _descendants(tree.getroot(), "ClCompile"):
        include = node.attrib.get("Include", "")
        if not include:
            continue
        expanded = _expand_msbuild_path(include, path.parent, solution_dir)
        if expanded:
            sources.append(Path(expanded).resolve())
    return tuple(dict.fromkeys(sources))


def _solution_dir(root: Path, project: Path) -> Path:
    resolved_root = root.resolve()
    for parent in (project.parent.resolve(), *project.parent.resolve().parents):
        if _is_relative_to(parent, resolved_root) or parent == resolved_root:
            if any(parent.glob("*.sln")):
                return parent
        if parent == resolved_root:
            break
    return resolved_root


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
    replacements = {
        "$(ProjectDir)": str(project_dir) + "\\",
        "$(MSBuildProjectDirectory)": str(project_dir),
        "$(MSBuildThisFileDirectory)": str(project_dir) + "\\",
        "$(SolutionDir)": str(solution_dir) + "\\",
    }
    for macro, replacement in replacements.items():
        value = value.replace(macro, replacement)
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


def _compiler_flags(exception_handling: str, additional_options: str) -> tuple[str, ...]:
    flags = []
    explicit_exception = _exception_handling_flag(exception_handling)
    if explicit_exception:
        flags.append(explicit_exception)
    for option in _split_msbuild_options(additional_options):
        if option.startswith("%(") or option in flags:
            continue
        if option.startswith("/EH"):
            flags.append(option)
    return tuple(flags)


def _exception_handling_flag(value: str) -> str:
    cleaned = value.strip().lower()
    return {
        "": "/EHsc",
        "0": "",
        "1": "/EHsc",
        "true": "/EHsc",
        "sync": "/EHsc",
        "async": "/EHa",
        "synccthrow": "/EHs",
        "false": "",
        "no": "",
        "none": "",
    }.get(cleaned, "/EHsc")


def _split_msbuild_options(text: str) -> tuple[str, ...]:
    values = []
    for group in _split_msbuild_list(text):
        values.extend(item for item in group.split() if item)
    return tuple(dict.fromkeys(values))


def _text(parent: ET.Element, name: str) -> str:
    node = _child(parent, name)
    return (node.text or "").strip() if node is not None else ""


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    node = parent.find(f"msb:{name}", MSBUILD_NS)
    return node if node is not None else parent.find(name)


def _children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [*parent.findall(f"msb:{name}", MSBUILD_NS), *parent.findall(name)]


def _descendant(parent: ET.Element, name: str) -> ET.Element | None:
    node = parent.find(f".//msb:{name}", MSBUILD_NS)
    return node if node is not None else parent.find(f".//{name}")


def _descendants(parent: ET.Element, name: str) -> list[ET.Element]:
    return [*parent.findall(f".//msb:{name}", MSBUILD_NS), *parent.findall(f".//{name}")]


def _ownership_score(file_path: Path, candidate: VcxprojProfile) -> int:
    scores = [_common_path_score(file_path, candidate.project.parent)]
    scores.extend(_common_path_score(file_path, source) for source in candidate.sources[:20])
    return max(scores)


def _common_path_score(left: Path, right: Path) -> int:
    left_parts = [part.lower() for part in left.resolve().parts]
    right_parts = [part.lower() for part in right.resolve().parts]
    score = 0
    for left_part, right_part in zip(left_parts, right_parts):
        if left_part != right_part:
            break
        score += 1
    return score


def _path_key(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").lower()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
