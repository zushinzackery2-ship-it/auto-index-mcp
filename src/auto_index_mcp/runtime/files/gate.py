"""Coordinate Windows metadata reads and namespace replacement across processes."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from ..budget import bounded_timeout


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateMutexW
    _create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _create.restype = wintypes.HANDLE
    _wait = _kernel.WaitForSingleObject
    _wait.argtypes, _wait.restype = [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD
    _release = _kernel.ReleaseMutex
    _release.argtypes, _release.restype = [wintypes.HANDLE], wintypes.BOOL
    _close = _kernel.CloseHandle
    _close.argtypes, _close.restype = [wintypes.HANDLE], wintypes.BOOL


@contextmanager
def publication_gate(path: Path):
    if os.name != "nt":
        yield
        return
    # Resolve only the stable parent: resolving a replaced leaf can select an
    # old file object. Global mutexes also coordinate different logon sessions.
    canonical = os.path.normcase(str(path.parent.resolve() / path.name))
    name = "Global\\AutoIndexMetadata-" + hashlib.sha256(os.fsencode(canonical)).hexdigest()
    handle = _create(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        status = _wait(handle, int(bounded_timeout(5.0) * 1000))
        if status == 258:
            raise TimeoutError(f"metadata publication is busy: {path}")
        if status == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        if status not in (0, 0x80):
            raise OSError(f"unexpected metadata mutex status {status}: {path}")
        # WAIT_ABANDONED transfers ownership after a crashed reader/writer.
        # Publication uses os.replace, so the visible file is a whole version.
        acquired = True
        yield
    finally:
        try:
            if acquired and not _release(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _close(handle)
