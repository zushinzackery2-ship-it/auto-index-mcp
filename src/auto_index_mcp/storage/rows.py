from __future__ import annotations

import json
import sqlite3
from typing import Any


def file_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["imports"] = json.loads(data["imports"])
    data["symbols"] = []
    data["module_refs"] = json.loads(data.get("module_refs") or "[]")
    data["module_reference_evidence"] = json.loads(data.get("module_reference_evidence") or "[]")
    data["quality_findings"] = json.loads(data.get("quality_findings") or "[]")
    data["active_source"] = bool(data.get("active_source", 1))
    return data


def symbol_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["calls"] = json.loads(data["calls"] or "[]")
    data["refs"] = json.loads(data.get("refs") or "[]")
    data["called_by"] = []
    data["reference_evidence"] = json.loads(data.get("reference_evidence") or "[]")
    data["usage_constraints"] = json.loads(data.get("usage_constraints") or "[]")
    if "source_sha1" in data:
        data["_source_revision"] = dict(path=data["file_path"], sha1=data.pop("source_sha1"),
                                        size=data.pop("source_size"), mtime_ns=data.pop("source_mtime_ns"))
    return data
