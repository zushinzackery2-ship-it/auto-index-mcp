from __future__ import annotations

from pathlib import Path

TEXT_ENCODINGS = ("utf-8-sig", "utf-16", "gb18030", "cp1252")


def decode_text(data: bytes) -> str:
    last_error: UnicodeDecodeError | None = None
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return data.decode("utf-8")


def read_text_file(path: Path) -> str:
    return decode_text(path.read_bytes())
