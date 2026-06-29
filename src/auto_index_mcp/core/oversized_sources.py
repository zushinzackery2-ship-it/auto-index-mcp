from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ._utils import is_relative_to
from .config import DEFAULT_MAX_SOURCE_BYTES, TEXT_EXTENSIONS
from .ignore_config import IgnoreConfig, exact_path_pattern, matches_patterns
from .ignore_rules import IgnoreRules


@dataclass(frozen=True)
class OversizedSourceScan:
    auto_patterns: list[str] = field(default_factory=list)
    auto_ignored_paths: list[str] = field(default_factory=list)
    privileged_paths: list[str] = field(default_factory=list)


def scan_oversized_sources(
    root: Path,
    config: IgnoreConfig,
    boundary_roots: Iterable[Path] | None = None,
    max_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> OversizedSourceScan:
    resolved_root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    ignore_rules = IgnoreRules.from_root(resolved_root, config.patterns)
    auto_patterns = list(config.auto_patterns)
    auto_ignored_paths: list[str] = []
    privileged_paths: list[str] = []

    for path in _iter_source_candidates(resolved_root, boundaries, ignore_rules):
        rel = path.relative_to(resolved_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= max_bytes:
            continue
        if matches_patterns(config.privileged_patterns, rel):
            privileged_paths.append(rel)
            continue
        auto_ignored_paths.append(rel)
        if not matches_patterns(auto_patterns, rel):
            auto_patterns.append(exact_path_pattern(rel))

    return OversizedSourceScan(
        auto_patterns=auto_patterns,
        auto_ignored_paths=sorted(set(auto_ignored_paths)),
        privileged_paths=sorted(set(privileged_paths)),
    )


def _iter_source_candidates(
    root: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
):
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        dir_names[:] = [
            name
            for name in dir_names
            if not _should_skip_dir(current / name, root, boundary_roots, ignore_rules)
        ]
        for name in file_names:
            path = current / name
            if _should_skip_file(path, root, boundary_roots, ignore_rules):
                continue
            yield path


def _should_skip_dir(
    path: Path,
    root: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return True
    if not is_relative_to(resolved, root):
        return True
    if any(is_relative_to(resolved, boundary) for boundary in boundary_roots):
        return True
    return ignore_rules.should_prune_dir(resolved)


def _should_skip_file(
    path: Path,
    root: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return True
    if not is_relative_to(resolved, root):
        return True
    if any(is_relative_to(resolved, boundary) for boundary in boundary_roots):
        return True
    rel = resolved.relative_to(root).as_posix()
    if ignore_rules.is_ignored_rel(rel, is_dir=False):
        return True
    return resolved.suffix.lower() not in TEXT_EXTENSIONS
