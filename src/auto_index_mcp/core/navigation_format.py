from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def overview_result(files: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    languages = Counter(item["language"] for item in files)
    top_dirs = Counter((item["parent"].split("/")[0] if item["parent"] else ".") for item in files)
    return {
        "format": "auto_index_overview_indexed",
        "file_count": len(files),
        "languages": dict(languages.most_common(limit)),
        "top_directories": dict(top_dirs.most_common(limit)),
        "samples": [compact_file(item) for item in files[:limit]],
    }


def tree_result(files: list[dict[str, Any]], root_path: str, depth: int, limit: int) -> dict[str, Any]:
    folders: dict[str, dict[str, Any]] = defaultdict(lambda: {"file_count": 0, "languages": Counter(), "samples": []})
    for item in files:
        if root_path and not item["path"].startswith(root_path.rstrip("/") + "/"):
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
    return {"format": "auto_index_tree_indexed", "root": root_path, "folders": rows}


def compact_file(item: dict[str, Any]) -> dict[str, Any]:
    symbols = item["symbols"][:12]
    names = [symbol["name"] if isinstance(symbol, dict) else str(symbol) for symbol in symbols]
    return {"path": item["path"], "language": item["language"], "lines": item["line_count"], "symbols": names}
