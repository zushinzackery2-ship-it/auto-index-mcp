from __future__ import annotations

from pathlib import Path
from typing import Any


def prefix_file(child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    prefixed = dict(item)
    prefixed["source_root"] = item.get("source_root", child["root"])
    prefixed["source_path"] = item.get("source_path", item["path"])
    prefixed["path"] = f"{child['path']}/{item['path']}"
    prefixed["parent"] = str(Path(prefixed["path"]).parent).replace("\\", "/")
    if prefixed["parent"] == ".":
        prefixed["parent"] = ""
    prefixed["symbols"] = [prefix_symbol_refs(child, symbol) for symbol in prefixed["symbols"]]
    return prefixed


def prefix_file_header(child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    prefixed = dict(item)
    prefixed["source_root"] = item.get("source_root", child["root"])
    prefixed["source_path"] = item.get("source_path", item["path"])
    prefixed["path"] = f"{child['path']}/{item['path']}"
    prefixed["parent"] = str(Path(prefixed["path"]).parent).replace("\\", "/")
    if prefixed["parent"] == ".":
        prefixed["parent"] = ""
    return prefixed


def prefix_search_target(child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": f"{child['path']}/{item['path']}",
        "language": item.get("language", ""),
        "active_source": item.get("active_source", True),
        "source_root": item.get("source_root", child["root"]),
        "source_path": item.get("source_path", item["path"]),
    }


def prefixed_symbols(child: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefixed = []
    for row in rows:
        item = prefix_symbol_refs(child, dict(row))
        item["file_path"] = f"{child['path']}/{item['file_path']}"
        prefixed.append(item)
    return prefixed


def prefix_symbol_refs(child: dict[str, Any], symbol: dict[str, Any]) -> dict[str, Any]:
    updated = dict(symbol)
    updated["called_by"] = [prefix_caller(child, value) for value in updated.get("called_by", [])]
    return updated


def prefix_caller(child: dict[str, Any], value: str) -> str:
    if "::" not in value:
        return value
    file_path, symbol = value.split("::", 1)
    if file_path.startswith(child["path"] + "/"):
        return value
    return f"{child['path']}/{file_path}::{symbol}"
