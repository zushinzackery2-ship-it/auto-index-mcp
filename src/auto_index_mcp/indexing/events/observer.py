from __future__ import annotations

from pathlib import Path
import os
from typing import Callable

from watchdog.observers import Observer


def create_observer(should_ignore: Callable[[Path], bool] | None):
    if os.name != "nt" or should_ignore is None:
        return Observer()
    from .windows import policy_observer
    return policy_observer(should_ignore)
