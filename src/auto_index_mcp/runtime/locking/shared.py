"""Shared kernel locks use the same byte as exclusive maintenance locks."""
from __future__ import annotations

import os

from .native import unlock


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.LockFileEx.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(_Overlapped),
    )
    _kernel.LockFileEx.restype = wintypes.BOOL

    def try_shared_lock(fd: int) -> bool:
        overlapped = _Overlapped()
        handle = msvcrt.get_osfhandle(fd)
        if _kernel.LockFileEx(handle, 1, 0, 1, 0, ctypes.byref(overlapped)):
            return True
        error = ctypes.get_last_error()
        if error == 33:
            return False
        raise ctypes.WinError(error)
else:
    import fcntl

    def try_shared_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def release_shared_lock(fd: int) -> None:
    unlock(fd)
