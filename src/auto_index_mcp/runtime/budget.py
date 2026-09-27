from __future__ import annotations

import threading
import time
from dataclasses import dataclass


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
