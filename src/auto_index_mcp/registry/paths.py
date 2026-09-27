from __future__ import annotations

import os
from pathlib import Path

REGISTRY_FILE_NAME = "registry.json"
REGISTRY_VERSION = 2
TOOL_NAME = "auto-index-mcp"
MARKER_FILE_NAME = "marker.json"
CACHEDIR_TAG_NAME = "CACHEDIR.TAG"
# Standard cache-directory tag signature; the first 43 bytes are fixed by the
# spec (https://bford.info/cachedir/), everything after the newline is free.
CACHEDIR_TAG_CONTENT = (
    "Signature: 8a477f597d28d172789f06886806cdb5\n"
    "# This directory holds a code index created by auto-index-mcp.\n"
    "# For information about cache directory tags see https://bford.info/cachedir/\n"
)

# Refreshing last_attached_at on every enable would rewrite the registry file
# constantly under agent polling; once an hour is plenty for cleanup decisions.
TOUCH_INTERVAL_SECONDS = 3600.0

# The only files clean() will ever delete. Anything else in the directory is
# not ours, stays put, and keeps the directory from being rmdir'ed.
KNOWN_INDEX_FILES = (
    "index.db",
    "index.db-wal",
    "index.db-shm",
    "index.db-journal",
    "embeddings.db",
    "embeddings.db-wal",
    "embeddings.db-shm",
    "embeddings.db-journal",
    "index.build.lock",
    "embeddings.build.lock",
    "watcher.lock",
    "index.schema.lock",
    "embeddings.schema.lock",
    MARKER_FILE_NAME,
    CACHEDIR_TAG_NAME,
)

def registry_key(root: Path | str) -> str:
    """Stable registry key for a project root.

    Resolved POSIX form; case-folded on Windows so ``D:\\Work`` and
    ``d:/work`` land on the same entry.
    """
    key = Path(root).resolve().as_posix()
    if os.name == "nt":
        key = key.casefold()
    return key
