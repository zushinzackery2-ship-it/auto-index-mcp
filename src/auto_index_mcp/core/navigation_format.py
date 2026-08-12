from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

# Tool responses are consumed by LLMs; a symbol referenced by a hundred tests
# would otherwise dump its whole reverse-call list into every reply.
MAX_PRESENTED_CALLERS = 25

# Symbol names shown per file row in overview/files listings. Enough to convey
# what the file is about without flooding the context window.
MAX_LISTED_SYMBOLS = 8

# Directories whose files are informative but rarely the right first read.
DEPRIORITIZED_DIR_TOKENS = (
    "test",
    "tests",
    "oldtest",
    "old",
    "archive",
    "archived",
    "legacy",
    "vendor",
    "third-party",
    "third_party",
    "examples",
    "example",
    "samples",
    "sample",
    "fixtures",
    "docs",
)

# File names that usually anchor a first read of an unknown codebase.
ENTRY_FILE_NAMES = {
    "main",
    "__main__",
    "index",
    "app",
    "server",
    "cli",
    "setup",
}
MANIFEST_FILE_NAMES = {
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "go.mod",
    "cmakelists.txt",
    "setup.py",
    "readme.md",
    "readme.rst",
}


def compact_symbol(symbol: dict[str, Any]) -> dict[str, Any]:
    """Lean symbol row for search/listing responses.

    Call-graph and nesting metadata stay out of the default shape; they are
    served by ``auto_index_symbol_refs`` and ``detail="full"`` lookups.
    """
    shaped: dict[str, Any] = {
        "name": symbol.get("name"),
        "kind": symbol.get("kind"),
        "line": symbol.get("line"),
        "end_line": symbol.get("end_line"),
        "signature": symbol.get("signature"),
    }
    if symbol.get("file_path"):
        shaped["path"] = symbol["file_path"]
    return shaped


def overview_result(files: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    languages = Counter(item["language"] for item in files)
    top_dirs = Counter(_top_dir(item) for item in files)
    return {
        "format": "auto_index_overview_v2",
        "file_count": len(files),
        "languages": dict(languages.most_common(limit)),
        "top_directories": dict(top_dirs.most_common(limit)),
        "samples": [compact_file(item) for item in sample_files(files, limit)],
    }


def sample_files(files: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Representative sample for first-pass orientation.

    Round-robins across top-level directories (main-source dirs before
    test/archive/vendor dirs) instead of truncating the path-sorted list,
    which used to let an ``oldtest/`` archive crowd out ``src/`` entirely.
    Within a directory, entry-point files and symbol-rich files come first.
    """
    if limit <= 0:
        return []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in files:
        grouped[_top_dir(item)].append(item)
    ordered_dirs = sorted(
        grouped,
        key=lambda name: (_is_deprioritized_dir(name), -len(grouped[name]), name),
    )
    for name in ordered_dirs:
        grouped[name].sort(key=_sample_priority)
    picked: list[dict[str, Any]] = []
    cursors = {name: 0 for name in ordered_dirs}
    while len(picked) < limit:
        progressed = False
        for name in ordered_dirs:
            index = cursors[name]
            if index >= len(grouped[name]):
                continue
            picked.append(grouped[name][index])
            cursors[name] = index + 1
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return picked


def _sample_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    name = str(item.get("name") or item["path"].rsplit("/", 1)[-1]).lower()
    stem = name.rsplit(".", 1)[0]
    if name in MANIFEST_FILE_NAMES or stem in ENTRY_FILE_NAMES:
        entry_rank = 0
    else:
        entry_rank = 1
    symbol_count = len(item.get("symbols") or [])
    return (entry_rank, -symbol_count, item["path"])


def _top_dir(item: dict[str, Any]) -> str:
    parent = item.get("parent") or ""
    return parent.split("/")[0] if parent else "."


def _is_deprioritized_dir(name: str) -> bool:
    return name.lower().strip(".") in DEPRIORITIZED_DIR_TOKENS


def tree_result(files: list[dict[str, Any]], dir_path: str, depth: int, limit: int) -> dict[str, Any]:
    folders: dict[str, dict[str, Any]] = defaultdict(lambda: {"file_count": 0, "languages": Counter(), "samples": []})
    for item in files:
        if dir_path and not item["path"].startswith(dir_path.rstrip("/") + "/"):
            continue
        parts = item["parent"].split("/") if item["parent"] else ["."]
        key = "/".join(parts[: max(1, depth)])
        folder = folders[key]
        folder["file_count"] += 1
        folder["languages"][item["language"]] += 1
        if len(folder["samples"]) < 5:
            folder["samples"].append(item["name"])

    rows = []
    for folder, data in sorted(folders.items())[:limit]:
        rows.append(
            {
                "folder": folder,
                "file_count": data["file_count"],
                "languages": dict(data["languages"]),
                "samples": data["samples"],
            }
        )
    return {"format": "auto_index_tree_v2", "dir": dir_path, "folders": rows}


def compact_file(item: dict[str, Any], max_symbols: int = MAX_LISTED_SYMBOLS) -> dict[str, Any]:
    symbols = item.get("symbols") or []
    names = [symbol["name"] if isinstance(symbol, dict) else str(symbol) for symbol in symbols[:max_symbols]]
    shaped = {
        "path": item["path"],
        "language": item["language"],
        "lines": item["line_count"],
        "symbols": names,
    }
    if len(symbols) > max_symbols:
        shaped["symbol_count"] = len(symbols)
    return shaped
