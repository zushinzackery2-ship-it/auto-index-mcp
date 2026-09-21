"""Tie Windows stdio servers to the launcher process that owns their pipes."""
from __future__ import annotations

import logging
import os
import threading

_started = False


def watch_parent() -> None:
    global _started
    if os.name != "nt" or _started:
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    parent = os.getppid()
    handle = kernel.OpenProcess(0x00100000, False, parent)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"cannot monitor stdio launcher pid={parent}")

    def wait():
        result = kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        kernel.CloseHandle(handle)
        if result == 0:
            logging.getLogger(__name__).info("stdio launcher exited pid=%s; releasing worker resources", parent)
            os._exit(0)

    threading.Thread(target=wait, name="auto-index-parent-lifetime", daemon=True).start()
    _started = True
