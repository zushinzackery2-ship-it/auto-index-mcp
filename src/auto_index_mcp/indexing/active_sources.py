from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from ..core._utils import is_relative_to
from ..core.models import FileRecord

C_FAMILY_LANGUAGES = {"c", "cpp"}


def annotate_active_sources(root: Path, records: list[FileRecord]) -> list[FileRecord]:
    active = discover_active_source_paths(root)
    if not active:
        return records
    updated = []
    for record in records:
        if record.language in C_FAMILY_LANGUAGES:
            updated.append(replace(record, active_source=record.path in active))
        else:
            updated.append(replace(record, active_source=True))
    return updated


def discover_active_source_paths(root: Path) -> set[str]:
    root = root.resolve()
    active: set[str] = set()
    for project in root.rglob("*.vcxproj"):
        if ".auto-index-mcp" in project.parts:
            continue
        active.update(_read_vcxproj_sources(root, project))
    return active


def _read_vcxproj_sources(root: Path, project: Path) -> set[str]:
    try:
        tree = ET.parse(project)
    except (ET.ParseError, OSError):
        return set()
    paths: set[str] = set()
    for element in tree.iter():
        if _local_name(element.tag) != "ClCompile":
            continue
        include = element.attrib.get("Include", "")
        if not include or "$(" in include:
            continue
        resolved = (project.parent / include).resolve()
        if is_relative_to(resolved, root):
            paths.add(resolved.relative_to(root).as_posix())
    return paths


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
