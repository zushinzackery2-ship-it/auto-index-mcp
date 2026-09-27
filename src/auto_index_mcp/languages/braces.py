"""One lexical pass supplies all definition brace ranges in a source file."""
from __future__ import annotations

import re

BRACES = re.compile(r"[{}]")


class BracePairs:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.first: dict[int, tuple[int, int]] = dict()
        self.ends: dict[tuple[int, int], int] = dict()
        stack: list[tuple[int, int]] = []
        for row, line in enumerate(lines):
            for match in BRACES.finditer(line):
                if match.group() == "{":
                    opening = (row, match.start())
                    self.first.setdefault(row, opening)
                    stack.append(opening)
                elif stack:
                    self.ends[stack.pop()] = row + 1

    def end_line(self, start: int) -> int:
        for row in range(start, min(start + 16, len(self.lines))):
            opening = self.first.get(row)
            if opening is not None:
                return self.ends.get(opening, start + 1)
            if ";" in self.lines[row]:
                break
        return min(start + 1, len(self.lines))
