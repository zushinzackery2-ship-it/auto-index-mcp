from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PageRequest:
    offset: int
    limit: int

    @classmethod
    def from_values(cls, offset: int = 0, limit: int | None = 10) -> "PageRequest":
        page_limit = 10 if limit is None else int(limit)
        page_offset = int(offset)
        if page_offset < 0:
            raise ValueError("offset must be >= 0")
        if page_limit < 1:
            raise ValueError("limit must be >= 1")
        return cls(offset=page_offset, limit=page_limit)

    @classmethod
    def from_cursor(cls, cursor: str | None = None, limit: int = 80) -> "PageRequest":
        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise ValueError("cursor must be a non-negative integer") from exc
        return cls.from_values(offset, limit)

    @property
    def probe_limit(self) -> int:
        return self.offset + self.limit + 1

    @property
    def fetch_limit(self) -> int:
        return self.limit + 1

    @property
    def next_cursor(self) -> str:
        return str(self.offset + self.limit)

    def slice(self, rows: list[dict[str, Any]]) -> "PageResult":
        page = rows[self.offset:self.offset + self.limit]
        return PageResult(items=page, has_more=len(rows) > self.offset + self.limit, scanned=len(rows))


@dataclass(frozen=True)
class PageResult:
    items: list[dict[str, Any]]
    has_more: bool
    scanned: int
