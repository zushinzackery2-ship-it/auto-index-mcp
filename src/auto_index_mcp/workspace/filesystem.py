from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain.config import DEFAULT_MAX_SOURCE_BYTES
from ..domain.ignore_config import matches_patterns
from ..domain.ignore_rules import IgnoreRules
from ..indexing.snapshot import _iter_source_files


def diff_local(view, root: Path, children):
    indexed = {item["path"]: item for item in view.store.file_fingerprints()}
    current = dict()
    rules = IgnoreRules.from_root(root, view.ignore_patterns)
    for path in _iter_source_files(root, [Path(child["root"]) for child in children], rules):
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(root).as_posix()
            stat = resolved.stat()
        except (OSError, ValueError):
            continue
        privileged = matches_patterns(view.privileged_patterns, rel)
        if not privileged and (stat.st_size > DEFAULT_MAX_SOURCE_BYTES or matches_patterns(view.auto_ignore_patterns, rel)):
            continue
        current[rel] = hashlib.sha1(resolved.read_bytes()).hexdigest()
    added = list(current.keys() - indexed.keys())
    deleted = list(indexed.keys() - current.keys())
    changed = [path for path in current.keys() & indexed.keys() if current[path] != indexed[path]["sha1"]]
    return added, deleted, changed
