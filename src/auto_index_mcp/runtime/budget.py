from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_ACTIVE_DEADLINE = ContextVar("auto_index_execution_deadline", default=None)


@contextmanager
def deadline_scope(deadline: float):
    previous = _ACTIVE_DEADLINE.get()
    token = _ACTIVE_DEADLINE.set(min(previous, deadline) if previous is not None else deadline)
    try:
        yield
    finally:
        _ACTIVE_DEADLINE.reset(token)


def bounded_timeout(seconds: float) -> float:
    deadline = _ACTIVE_DEADLINE.get()
    return max(0.0, min(seconds, deadline - time.monotonic()) if deadline is not None else seconds)


@dataclass(frozen=True)
class ExecutionBudget:
    deadline: float
    cancelled: threading.Event | None = None

    @classmethod
    def seconds(cls, seconds: float, cancelled: threading.Event | None = None) -> ExecutionBudget:
        return cls(time.monotonic() + max(0, seconds), cancelled)

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self) -> None:
        if self.cancelled is not None and self.cancelled.is_set():
            raise InterruptedError("operation cancelled")
        if self.remaining <= 0:
            raise TimeoutError("execution budget exhausted")
