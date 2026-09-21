from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.text_decode import read_text_file

MAX_BODY_LINES = 256
MAX_BODY_CHARS = 8000
TEXT_SCHEME_VERSION = "txt2"


def read_lines(root: Path, file_path: str) -> list[str]:
    return read_text_file(root / file_path).splitlines()


def symbol_meta(symbol: dict[str, Any]) -> dict[str, Any]:
    start = int(symbol.get("line", 1))
    return dict(
        kind=symbol.get("kind", "") or "",
        end_line=int(symbol.get("end_line", start) or start),
        signature=symbol.get("signature", "") or "",
        complexity=int(symbol.get("complexity", 1) or 1),
    )


def symbol_text(symbol: dict[str, Any], lines: list[str]) -> str:
    kind = symbol.get("kind", "")
    name = symbol.get("name", "")
    signature = symbol.get("signature", "") or ""
    head = f"{kind} {signature}" if signature else f"{kind} {name}".strip()
    start = max(1, int(symbol.get("line", 1))) - 1
    end = int(symbol.get("end_line", start + 1))
    body = "\n".join(lines[start:min(end, start + MAX_BODY_LINES)])[:MAX_BODY_CHARS]
    return f"{head}\n{body}".strip()


def window_texts(backend, text: str) -> list[str]:
    window = getattr(backend, "window_texts", None)
    return window(text) if window is not None else [text]
