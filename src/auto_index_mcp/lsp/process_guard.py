from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Callable


AliveCheck = Callable[[int], bool]
Terminate = Callable[[int], None]


class ProcessGuard:
    """Keeps spawned LSP subprocesses from leaking as orphans.

    Three layers, stacked:
      1. (caller) graceful shutdown kills children on every catchable exit.
      2. a per-owner pid registry under ``servers/<mcp_pid>.json`` so a *new*
         process can reap children left by a *dead* MCP, without ever touching
         a live peer's servers (multi-agent safe).
      3. OS-level parent/child binding so even a hard kill of the MCP process
         takes its children down: Windows Job Object (KILL_ON_JOB_CLOSE),
         Linux PR_SET_PDEATHSIG.
    """

    def __init__(
        self,
        lsp_dir: Path,
        pid: int | None = None,
        is_alive: AliveCheck | None = None,
        terminate: Terminate | None = None,
    ) -> None:
        self.registry_dir = Path(lsp_dir) / "servers"
        self.pid = pid if pid is not None else os.getpid()
        self._is_alive = is_alive or _process_alive
        self._terminate = terminate or _terminate_process
        self._job = _create_windows_job() if sys.platform == "win32" else None

    @property
    def own_file(self) -> Path:
        return self.registry_dir / f"{self.pid}.json"

    @property
    def spawn_kwargs(self) -> dict:
        # PR_SET_PDEATHSIG must be set in the child before exec; only Linux.
        if sys.platform.startswith("linux"):
            return {"preexec_fn": _set_pdeathsig}
        return {}

    def register(self, process) -> None:
        if self._job is not None:
            _assign_to_job(self._job, process)
        child_pid = getattr(process, "pid", None)
        if child_pid is None:
            return
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        pids = self._read_pids(self.own_file)
        if child_pid not in pids:
            pids.append(child_pid)
            self._write_pids(self.own_file, pids)

    def reap_orphans(self) -> list[int]:
        reaped: list[int] = []
        if not self.registry_dir.is_dir():
            return reaped
        for entry in self.registry_dir.glob("*.json"):
            try:
                owner = int(entry.stem)
            except ValueError:
                continue
            if owner == self.pid or self._is_alive(owner):
                continue  # our own file, or a live peer MCP - leave it alone
            for child_pid in self._read_pids(entry):
                if self._is_alive(child_pid):
                    self._terminate(child_pid)
                    reaped.append(child_pid)
            entry.unlink(missing_ok=True)
        return reaped

    def release(self) -> None:
        self.own_file.unlink(missing_ok=True)

    def _read_pids(self, path: Path) -> list[int]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [int(value) for value in data if isinstance(value, int)]

    def _write_pids(self, path: Path, pids: list[int]) -> None:
        try:
            path.write_text(json.dumps(pids), encoding="utf-8")
        except OSError:
            pass


def _process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_process(pid: int) -> None:
    try:
        os.kill(pid, getattr(signal, "SIGTERM", signal.SIGINT))
    except OSError:
        pass


def _set_pdeathsig() -> None:  # pragma: no cover - Linux-only, runs in child
    try:
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def _windows_process_alive(pid: int) -> bool:  # pragma: no cover - Windows-only
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _create_windows_job():  # pragma: no cover - Windows-only
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _assign_to_job(job, process) -> None:  # pragma: no cover - Windows-only
    try:
        import ctypes

        handle = getattr(process, "_handle", None)
        if handle is None:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).AssignProcessToJobObject(job, int(handle))
    except Exception:
        pass
