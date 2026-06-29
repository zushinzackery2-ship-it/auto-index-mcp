from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str
    line: int
    end_line: int
    signature: str
    complexity: int = 1
    calls: list[str] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    parent_name: str = ""
    parent_kind: str = ""
    depth: int = 0
    nesting_path: str = ""
    children_count: int = 0
    max_child_depth: int = 0
    max_block_depth: int = 0


@dataclass(frozen=True)
class FileRecord:
    path: str
    name: str
    parent: str
    extension: str
    language: str
    size: int
    mtime_ns: int
    sha1: str
    line_count: int
    imports: list[str] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    quality_findings: list[dict[str, Any]] = field(default_factory=list)
    active_source: bool = True
    snippet: str = ""


@dataclass(frozen=True)
class ScanResult:
    root: str
    records: list[FileRecord]
    skipped: int
    reused: int
    errors: list[str]
    oversized_paths: list[str] = field(default_factory=list)
    privileged_paths: list[str] = field(default_factory=list)
