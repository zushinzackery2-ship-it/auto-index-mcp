from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _LockState:
    token: str
    mtime_ns: int
    age_seconds: float


class BuildLock:
    """Best-effort cross-process advisory lock for full index rebuilds.

    Several MCP processes (one per agent) can point at the same project
    directory and try to build its shared index at the same time. This
    serialises full rebuilds through an ``O_EXCL`` lock file so the heavy scan
    happens once and concurrent SQLite writers do not pile up against the busy
    timeout.

    A lock left behind by a killed process is reclaimed immediately once its
    recorded owner PID is provably dead, and after ``stale_seconds`` without a
    heartbeat as the fallback for unparseable tokens or a reused PID. Callers
    can use ``try_acquire`` to avoid waiting in request-facing code while
    still preventing duplicate full scans.
    """

    def __init__(self, path: Path, stale_seconds: float = 120.0, poll_seconds: float = 0.05) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.poll_seconds = poll_seconds
        self._held = False
        self._token = f"{os.getpid()}:{time.time_ns()}:{id(self)}"
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def acquire(self, wait_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            if self._try_create():
                self._held = True
                self._start_heartbeat()
                return True
            if self._try_reclaim():
                # The dead lock is gone; retry the create immediately so even
                # a zero-wait try_acquire recovers in one call instead of
                # handing the reclaim's benefit to some later caller.
                continue
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_seconds)

    def try_acquire(self) -> bool:
        return self.acquire(0.0)

    def release(self) -> None:
        self._stop_heartbeat()
        if not self._held:
            return
        self._held = False
        if not self._owns_lock():
            return
        try:
            self.path.unlink()
        except OSError:
            pass

    @property
    def held(self) -> bool:
        return self._held

    def _try_create(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except (FileExistsError, OSError):
            return False
        try:
            os.write(fd, f"{self._token}\n{time.time()}".encode("ascii", errors="ignore"))
        finally:
            os.close(fd)
        return True

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        interval = max(self.poll_seconds, min(30.0, self.stale_seconds / 4.0))
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat,
            args=(interval,),
            name="auto-index-build-lock-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def _heartbeat(self, interval: float) -> None:
        while not self._heartbeat_stop.wait(interval):
            if not self._owns_lock():
                return
            try:
                now = time.time()
                os.utime(self.path, (now, now))
            except OSError:
                return

    def state_info(self) -> dict[str, object] | None:
        """Holder diagnostics for status payloads: pid, liveness, heartbeat age."""
        state = self._lock_state()
        if state is None:
            return None
        pid = _token_pid(state.token)
        return {
            "holder_pid": pid,
            "holder_alive": None if pid is None else _pid_alive(pid),
            # File mtime can sit fractionally ahead of time.time() on Windows;
            # a freshly stamped lock must not report a negative age.
            "lock_age_seconds": max(0.0, round(state.age_seconds, 1)),
        }

    def _try_reclaim(self) -> bool:
        """Remove a lock whose owner is gone; True when the file was removed.

        The state is re-read right before unlinking so a lock that was just
        refreshed or replaced by a live process is never deleted.
        """
        state = self._lock_state()
        if state is None or not self._reclaim_reason(state):
            return False
        latest = self._lock_state()
        if latest is None or latest.token != state.token or latest.mtime_ns != state.mtime_ns:
            return False
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def _reclaim_reason(self, state: _LockState) -> str | None:
        if state.age_seconds > self.stale_seconds:
            return "stale"
        pid = _token_pid(state.token)
        if pid is not None and pid != os.getpid() and _pid_alive(pid) is False:
            return "dead-owner"
        return None

    def _lock_state(self) -> _LockState | None:
        try:
            stat = self.path.stat()
            token = self.path.read_text(encoding="ascii", errors="ignore").splitlines()[0]
        except (OSError, IndexError):
            return None
        return _LockState(token=token, mtime_ns=stat.st_mtime_ns, age_seconds=time.time() - stat.st_mtime)

    def _owns_lock(self) -> bool:
        state = self._lock_state()
        return state is not None and state.token == self._token


def _token_pid(token: str) -> int | None:
    head = token.split(":", 1)[0]
    try:
        pid = int(head)
    except ValueError:
        return None
    return pid if pid > 0 else None


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _ERROR_ACCESS_DENIED = 5
    _ERROR_INVALID_PARAMETER = 87
    _STILL_ACTIVE = 259
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _pid_alive(pid: int) -> bool | None:
        """True/False when liveness is provable, None when undecidable.

        ``os.kill(pid, 0)`` is unusable on Windows - any non-console-event
        signal value unconditionally terminates the target - so liveness goes
        through OpenProcess with the narrowest query right. Access-denied
        still proves some process owns the PID; only a provably dead owner
        (invalid PID or a non-STILL_ACTIVE exit code) reports False, so an
        undecidable probe degrades to the age-based stale fallback instead of
        stealing a live builder's lock.
        """
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == _ERROR_ACCESS_DENIED:
                return True
            if error == _ERROR_INVALID_PARAMETER:
                return False
            return None
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)
else:

    def _pid_alive(pid: int) -> bool | None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
