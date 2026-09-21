from __future__ import annotations

from typing import Any


def ranked_symbol_key(item: dict[str, Any]) -> tuple[int, int, str, int]:
    """Match tier, name specificity, then stable source position."""
    return (
        int(item.get("match_rank", 99)),
        len(item.get("name", "")),
        item["file_path"].lower(),
        item["line"],
    )
