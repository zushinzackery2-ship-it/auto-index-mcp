from __future__ import annotations

from typing import BinaryIO
from urllib.parse import unquote


def normalize_uri(uri: str) -> str:
    if not uri.startswith("file:///"):
        return uri
    decoded = unquote(uri)
    prefix_length = len("file:///")
    if len(decoded) > prefix_length + 1 and decoded[prefix_length].isalpha() and decoded[prefix_length + 1] == ":":
        return f"file:///{decoded[prefix_length].upper()}{decoded[prefix_length + 1:]}"
    return decoded


def read_headers(stream: BinaryIO) -> dict[str, str]:
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return {}
        if line in (b"\r\n", b"\n"):
            return headers
        name, _, value = line.decode("ascii", errors="ignore").partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()