"""Extract SFC scripts without changing source line or column positions."""
import re

SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def script_source(text: str) -> str:
    pieces = []
    cursor = 0
    for match in SCRIPT.finditer(text):
        start, end = match.span(1)
        pieces.append(_blank(text[cursor:start]))
        pieces.append(text[start:end])
        cursor = end
    pieces.append(_blank(text[cursor:]))
    return "".join(pieces)


def _blank(text: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in text)
