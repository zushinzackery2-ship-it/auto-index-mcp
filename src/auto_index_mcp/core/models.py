from __future__ import annotations

from dataclasses import dataclass, field


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
    snippet: str = ""


@dataclass(frozen=True)
class ScanResult:
    root: str
    records: list[FileRecord]
    skipped: int
    reused: int
    errors: list[str]
