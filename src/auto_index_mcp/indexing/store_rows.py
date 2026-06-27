from __future__ import annotations

import json
import sqlite3
from typing import Any


def file_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["imports"] = json.loads(data["imports"])
    data["symbols"] = json.loads(data["symbols"])
    data["quality_findings"] = json.loads(data.get("quality_findings") or "[]")
    data["active_source"] = bool(data.get("active_source", 1))
    return data


def symbol_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["calls"] = json.loads(data["calls"] or "[]")
    data["called_by"] = json.loads(data["called_by"] or "[]")
    return data
