"""Human/AI-readable timestamp formatting for tool responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def iso_time(value: Any) -> str | None:
    """Local ISO-8601 string for an epoch value; passthrough for the rest.

    Tool responses previously exposed raw ``time.time()`` floats, which an
    LLM cannot read at a glance. ``None`` and non-numeric values are returned
    unchanged so callers can feed metadata fields straight through.
    """
    if value is None:
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return str(value)
    try:
        return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return str(value)
