"""Aho-Corasick multi-pattern substring matching.

Replaces the O(sources x targets) nested substring scan in the orphan-file
check with a single automaton pass per text: build once over all patterns,
then each ``matched_patterns`` call costs O(len(text) + hits) regardless of
how many patterns exist. Semantics are identical to
``any(pattern in text for pattern in patterns)`` per pattern.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable


class MultiPatternMatcher:
    """Finds which of a fixed pattern set occur as substrings of a text."""

    def __init__(self, patterns: Iterable[str]) -> None:
        # State 0 is the root. goto maps state -> {char: state}; outputs maps
        # state -> pattern indexes ending there (suffix outputs merged in).
        self._patterns: list[str] = []
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._outputs: list[list[int]] = [[]]
        seen: set[str] = set()
        for pattern in patterns:
            if not pattern or pattern in seen:
                continue
            seen.add(pattern)
            self._insert(pattern)
        self._build_failure_links()

    def _insert(self, pattern: str) -> None:
        state = 0
        for char in pattern:
            nxt = self._goto[state].get(char)
            if nxt is None:
                nxt = len(self._goto)
                self._goto[state][char] = nxt
                self._goto.append({})
                self._fail.append(0)
                self._outputs.append([])
            state = nxt
        self._outputs[state].append(len(self._patterns))
        self._patterns.append(pattern)

    def _build_failure_links(self) -> None:
        queue: deque[int] = deque()
        for state in self._goto[0].values():
            queue.append(state)
        while queue:
            state = queue.popleft()
            for char, child in self._goto[state].items():
                queue.append(child)
                fallback = self._fail[state]
                while fallback and char not in self._goto[fallback]:
                    fallback = self._fail[fallback]
                self._fail[child] = self._goto[fallback].get(char, 0)
                # Merge suffix outputs so a hit at ``child`` also reports every
                # shorter pattern ending at the same position.
                self._outputs[child].extend(self._outputs[self._fail[child]])

    def matched_patterns(self, text: str) -> set[str]:
        """Return every pattern that occurs at least once in ``text``."""
        matched: set[int] = set()
        state = 0
        total = len(self._patterns)
        for char in text:
            while state and char not in self._goto[state]:
                state = self._fail[state]
            state = self._goto[state].get(char, 0)
            outputs = self._outputs[state]
            if outputs:
                matched.update(outputs)
                if len(matched) == total:
                    break
        return {self._patterns[index] for index in matched}
