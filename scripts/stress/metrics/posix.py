"""psutil supplies current RSS on POSIX benchmark hosts."""
import psutil

_process = psutil.Process()


def resident_bytes() -> int:
    return _process.memory_info().rss
