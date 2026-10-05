"""Use native Windows counters and the psutil backend on other platforms."""
import os

if os.name == "nt":
    from .windows import resident_bytes
else:
    from .posix import resident_bytes as resident_bytes

__all__ = ["resident_bytes", "cpu_seconds"]


def cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system
