from __future__ import annotations

import base64
import json
from pathlib import Path

MAX_MATCH_TEXT_CHARS = 250


def _text(value: dict) -> str:
    if "text" in value:
        return value["text"]
    if "bytes" in value:
        return base64.b64decode(value["bytes"]).decode("utf-8", "replace")
    return ""


def parse_match(root: Path, line: str, path_map: dict[str, str]) -> dict | None:
    try:
        payload = json.loads(line)
        if payload.get("type") != "match":
            return None
        data = payload["data"]
        source = Path(_text(data["path"])).resolve()
        path = path_map.get(str(source)) or source.relative_to(root).as_posix()
        text = _text(data["lines"]).strip()
        truncated = len(text) > MAX_MATCH_TEXT_CHARS
        item = dict(path=path, line=int(data["line_number"]), text=text[:MAX_MATCH_TEXT_CHARS] + ("..." if truncated else ""))
        if truncated:
            item["truncated"] = True
        return item
    except (KeyError, TypeError, ValueError, OSError):
        return None
