"""Shared protocol identities, activation intent and deadline handling."""
from enum import Enum
from pathlib import Path
import time

PROTOCOL_VERSION = 1
ACTIVE_PHASES = frozenset(("quiescing", "leasing", "deleting"))
SUCCESS_STATES = frozenset(("purged", "already-purged"))


class ActivationIntent(Enum):
    AUTOMATIC = "automatic"
    EXPLICIT = "explicit"


def within(path, root) -> bool:
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def intersects(first, second) -> bool:
    return within(first, second) or within(second, first)


def remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def check_deadline(deadline: float) -> None:
    if remaining(deadline) <= 0:
        raise TimeoutError("workspace purge deadline expired")
