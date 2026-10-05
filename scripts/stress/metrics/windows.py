"""Read this process's working set through the Windows process API."""
import ctypes
from ctypes import wintypes


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.GetCurrentProcess.argtypes = ()
_kernel.GetCurrentProcess.restype = wintypes.HANDLE
_kernel.K32GetProcessMemoryInfo.argtypes = (
    wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
)
_kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL


def resident_bytes() -> int:
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not _kernel.K32GetProcessMemoryInfo(_kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize
