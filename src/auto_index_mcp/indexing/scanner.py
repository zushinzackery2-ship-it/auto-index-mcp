from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from ..core.config import (
    DEFAULT_MAX_SOURCE_BYTES,
    LANGUAGE_BY_EXTENSION,
    TEXT_EXTENSIONS,
)
from ..core.ignore_config import matches_patterns
from ..core.ignore_rules import IgnoreRules
from ..core.models import FileRecord, ScanResult, SymbolRecord
from ..core.tree_progress import TreeProgress
from ..core._utils import is_relative_to
from ..core.quality_dangling import file_quality_findings
from ..core.text_decode import decode_text
from .analysis import enrich_symbols
from ..languages.c_family import extract_c_family_symbols
from ..languages.python import extract_python_symbols
from ..languages.javascript import extract_javascript_like_symbols
from ..languages.generic import extract_symbols
from ..languages.pascal import extract_pascal_symbols

IMPORT_RE = re.compile(r"^\s*(?:from\s+[\w.]+\s+import|import\s+[\w., ]+|#include\s+[<\"].+[>\"]|using\s+[\w.:]+;)")
class SourceScanner:
    def __init__(
        self,
        root: str,
        extra_excludes: list[str] | None = None,
        auto_excludes: list[str] | None = None,
        privileged_patterns: list[str] | None = None,
        max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        existing_records: dict[str, dict] | None = None,
        boundary_roots: list[Path] | None = None,
        tree_progress: TreeProgress | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.extra_excludes = extra_excludes or []
        self.auto_excludes = auto_excludes or []
        self.privileged_patterns = privileged_patterns or []
        self.max_bytes = max_bytes
        self.existing_records = existing_records or {}
        self.boundary_roots = [path.resolve() for path in boundary_roots or []]
        self.ignore_rules = IgnoreRules.from_root(self.root, self.extra_excludes)
        self.tree_progress = tree_progress
        self.oversized_paths: list[str] = []
        self.privileged_paths: list[str] = []

    def scan(self) -> ScanResult:
        records: list[FileRecord] = []
        errors: list[str] = []
        skipped = 0
        reused = 0
        seen_targets: set[Path] = set()

        for path in self._iter_files():
            try:
                target = path.resolve(strict=True)
                if self._should_skip(target):
                    skipped += 1
                    continue
                if target in seen_targets:
                    skipped += 1
                    continue
                seen_targets.add(target)
                reused_record = self._reuse_record_if_current(target)
                if reused_record:
                    records.append(reused_record)
                    self._note_progress_file(reused_record)
                    reused += 1
                    continue
                record = self._read_record(target)
                records.append(record)
                self._note_progress_file(record)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                errors.append(f"{self._display_path(path)}: {exc}")
                continue

        records.sort(key=lambda item: item.path.lower())
        return ScanResult(
            str(self.root),
            records,
            skipped,
            reused,
            errors,
            sorted(set(self.oversized_paths)),
            sorted(set(self.privileged_paths)),
        )

    def _should_skip(self, path: Path) -> bool:
        resolved = path.resolve(strict=True)
        if not is_relative_to(resolved, self.root):
            return True
        if any(is_relative_to(resolved, root) for root in self.boundary_roots):
            return True
        rel = self._relative(resolved)
        if self.ignore_rules.is_ignored_rel(rel, is_dir=False):
            return True
        if resolved.suffix.lower() not in TEXT_EXTENSIONS:
            return True
        if matches_patterns(self.auto_excludes, rel) and not self._is_privileged(rel):
            return True
        if resolved.stat().st_size > self.max_bytes and not self._is_privileged(rel):
            self.oversized_paths.append(rel)
            return True
        return False

    def _iter_files(self) -> Iterator[Path]:
        for dir_path, dir_names, file_names in os.walk(self.root):
            current = Path(dir_path)
            dir_names[:] = [
                name
                for name in dir_names
                if not self._should_skip_dir(current / name)
            ]
            if self.tree_progress is not None:
                self.tree_progress.note_directory(current, [current / name for name in dir_names])
            for name in file_names:
                yield current / name
            if self.tree_progress is not None:
                self.tree_progress.finish_directory(current)

    def _note_progress_file(self, record: FileRecord) -> None:
        if self.tree_progress is not None:
            self.tree_progress.note_file(record.path, record.name, record.parent, record.language)

    def _should_skip_dir(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return True
        if not is_relative_to(resolved, self.root):
            return True
        if any(is_relative_to(resolved, root) for root in self.boundary_roots):
            return True
        return self.ignore_rules.should_prune_dir(resolved)

    def read_path(self, path: Path) -> FileRecord:
        path = path.resolve(strict=True)
        if self._should_skip(path):
            raise ValueError(f"path is not indexable: {self._relative(path)}")
        return self._read_record(path)

    def _read_record(self, path: Path) -> FileRecord:
        path = path.resolve(strict=True)
        rel = self._relative(path)
        if path.stat().st_size > self.max_bytes and self._is_privileged(rel):
            self.privileged_paths.append(rel)
        data = path.read_bytes()
        text = decode_text(data)
        lines = text.splitlines()
        imports = self._extract_matches(lines, IMPORT_RE, whole_line=True)
        language = LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "text")
        symbols = enrich_symbols(lines, self._extract_symbols(language, text, lines), language)
        quality_findings = file_quality_findings(
            {"path": rel, "language": language, "symbols": [asdict(symbol) for symbol in symbols]},
            text,
        )
        parent = str(Path(rel).parent).replace("\\", "/")
        if parent == ".":
            parent = ""
        return FileRecord(
            path=rel,
            name=path.name,
            parent=parent,
            extension=path.suffix.lower(),
            language=language,
            size=len(data),
            mtime_ns=path.stat().st_mtime_ns,
            sha1=hashlib.sha1(data).hexdigest(),
            line_count=len(lines),
            imports=imports[:80],
            symbols=symbols[:120],
            quality_findings=quality_findings,
            active_source=True,
            snippet="\n".join(lines[:40]),
        )

    def _reuse_record_if_current(self, path: Path) -> FileRecord | None:
        rel = self._relative(path)
        existing = self.existing_records.get(rel)
        if not existing:
            return None
        stat = path.stat()
        if existing["size"] != stat.st_size or existing["mtime_ns"] != stat.st_mtime_ns:
            return None
        return FileRecord(
            path=existing["path"],
            name=existing["name"],
            parent=existing["parent"],
            extension=existing["extension"],
            language=existing["language"],
            size=existing["size"],
            mtime_ns=existing["mtime_ns"],
            sha1=existing["sha1"],
            line_count=existing["line_count"],
            imports=existing["imports"],
            symbols=[SymbolRecord(**symbol) for symbol in existing["symbols"]],
            quality_findings=existing.get("quality_findings", []),
            active_source=existing.get("active_source", True),
            snippet=existing["snippet"],
        )

    def _extract_matches(self, lines: list[str], pattern: re.Pattern[str], whole_line: bool) -> list[str]:
        found: list[str] = []
        for line in lines:
            match = pattern.match(line)
            if match:
                found.append(line.strip() if whole_line else match.group(1))
        return found

    def _extract_symbols(self, language: str, text: str, lines: list[str]) -> list[SymbolRecord]:
        if language == "python":
            return extract_python_symbols(text, lines)
        if language in {"javascript", "typescript"}:
            return extract_javascript_like_symbols(lines)
        if language in {"c", "cpp"}:
            return extract_c_family_symbols(lines)
        if language == "pascal":
            return extract_pascal_symbols(lines)
        return extract_symbols(lines)

    def _relative(self, path: Path) -> str:
        return str(path.resolve(strict=True).relative_to(self.root)).replace("\\", "/")

    def _is_privileged(self, rel: str) -> bool:
        return matches_patterns(self.privileged_patterns, rel)

    def _display_path(self, path: Path) -> str:
        try:
            return self._relative(path)
        except (OSError, ValueError):
            return str(path)
