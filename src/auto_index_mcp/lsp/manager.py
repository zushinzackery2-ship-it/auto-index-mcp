from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .clangd_bootstrap import ClangdBootstrap, prepare_clangd
from .client import LspClient
from .lsp_resolver import resolve_lsp_executable
from .lsp_specs import SERVER_SPECS, LspServerSpec, effective_spec, is_file_supported, presence
from .process_guard import ProcessGuard


ClientFactory = Callable[..., LspClient]
ExecutableResolver = Callable[..., "str | None"]


def remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


class LspManager:
    """Per-process registry of running LSP servers.

    Lazily starts only the server families that have files in the project,
    reuses running clients, guards the sessions map with a lock, and ties every
    spawned child to a ProcessGuard so nothing leaks as an orphan. start() is
    bounded/synchronous and testable; start_async() runs it on a background
    thread so the MCP tool never blocks the request loop.
    """

    def __init__(
        self,
        lsp_dir: Path,
        executable_resolver: ExecutableResolver | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.lsp_dir = Path(lsp_dir)
        self.guard = ProcessGuard(self.lsp_dir)
        self.executable_resolver = executable_resolver or resolve_lsp_executable
        self.client_factory = client_factory or self._default_client_factory
        self.clients: dict[str, LspClient] = {}
        self.unavailable: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        self._reaped = False
        self._start_thread: threading.Thread | None = None
        self._start_root: Path | None = None
        self._last_start = "LSP|not_started"

    # -- start -------------------------------------------------------------

    def start(self, root: Path, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        self._reap_once()
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        targets = target_specs(files)
        if not targets:
            self.shutdown(0.2)
            return self._remember(f"LSP|no_targets|{root.as_posix()}")
        bootstrap = self._clangd_bootstrap(root, files, {spec.key for spec, _ in targets})
        lines: list[str] = []
        ready = missing = error = 0
        with self._lock:
            target_keys = {spec.key for spec, _ in targets}
            for key in [key for key in self.clients if key not in target_keys]:
                self.clients.pop(key).shutdown(0.2)
            self.unavailable = {key: value for key, value in self.unavailable.items() if key in target_keys}
            for spec, count in targets:
                state = self._ensure_client(spec, root, bootstrap, deadline)
                ready += state == "ready"
                missing += state == "missing"
                error += state not in {"ready", "missing"}
                lines.append(self._server_line(spec, state, count, root, bootstrap))
        status = "ready" if ready and not (missing or error) else ("unavailable" if not ready else "partial")
        return self._remember("\n".join([f"LSP|{status}|{root.as_posix()}"] + lines))

    def start_async(self, root: Path, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        with self._lock:
            if self._is_starting(root):
                return f"LSP|starting|{root.as_posix()}"
            snapshot = [dict(item) for item in files]
            self._start_root = root
            self._last_start = f"LSP|starting|{root.as_posix()}"
            self._start_thread = threading.Thread(
                target=self._run_background_start, args=(root, snapshot, timeout_seconds), daemon=True
            )
            self._start_thread.start()
        return f"LSP|starting|{root.as_posix()}"

    def start_status(self, root: Path) -> str:
        with self._lock:
            if self._is_starting(root):
                return f"LSP|starting|{root.as_posix()}"
            return self._last_start

    def check_start_status(self, root: Path) -> str | None:
        with self._lock:
            return f"CHK|starting|{root.as_posix()}" if self._is_starting(root) else None

    # -- accessors / lifecycle --------------------------------------------

    def running_clients(self) -> dict[str, LspClient]:
        with self._lock:
            return {key: client for key, client in self.clients.items() if client.is_running()}

    def ensure_running(self, root: Path, files: list[dict[str, Any]], deadline: float) -> dict[str, LspClient]:
        targets = target_specs(files)
        if not targets:
            return self.running_clients()
        bootstrap = self._clangd_bootstrap(root, files, {spec.key for spec, _ in targets})
        with self._lock:
            for spec, _ in targets:
                client = self.clients.get(spec.key)
                if client is None or not client.is_running():
                    self._ensure_client(spec, root, bootstrap, deadline)
        return self.running_clients()

    def shutdown(self, timeout_seconds: float = 5.0) -> str:
        with self._lock:
            clients = list(self.clients.items())
            self.clients.clear()
            self.unavailable.clear()
            self._start_thread = None
            self._start_root = None
        lines = []
        for key, client in sorted(clients):
            lines.append(f"S:{key}/{client.shutdown(timeout_seconds)}")
        self.guard.release()
        self._last_start = "LSP|stopped"
        return "\n".join(["LSP|stopped"] + lines) if lines else "LSP|stopped"

    # -- internals ---------------------------------------------------------

    def _ensure_client(self, spec: LspServerSpec, root: Path, bootstrap: ClangdBootstrap, deadline: float) -> str:
        client = self.clients.get(spec.key)
        if client is not None and client.is_running():
            return "ready"
        executable = self._resolve(spec.executable, root)
        if not executable:
            self.unavailable[spec.key] = ("missing", 0)
            return "missing"
        effective = effective_spec(spec, bootstrap)
        client = self.client_factory(effective, executable, root)
        state = client.start(max(0.1, remaining_seconds(deadline)))
        if state == "ready":
            self.clients[spec.key] = client
            self.unavailable.pop(spec.key, None)
        else:
            self.unavailable[spec.key] = (state, 0)
        return state

    def _run_background_start(self, root: Path, files: list[dict[str, Any]], timeout_seconds: float) -> None:
        try:
            self.start(root, files, timeout_seconds)
        except Exception as exc:  # surface, never crash the daemon thread
            self._last_start = f"LSP|error|{root.as_posix()}|{type(exc).__name__}"
        finally:
            with self._lock:
                if self._start_root == root:
                    self._start_thread = None

    def _is_starting(self, root: Path) -> bool:
        return self._start_thread is not None and self._start_thread.is_alive() and self._start_root == root

    def _reap_once(self) -> None:
        if not self._reaped:
            self._reaped = True
            self.guard.reap_orphans()

    def _clangd_bootstrap(self, root: Path, files: list[dict[str, Any]], target_keys: set[str]) -> ClangdBootstrap:
        if "clangd" not in target_keys:
            return ClangdBootstrap((), ())
        return prepare_clangd(root, files)

    def _resolve(self, name: str, root: Path) -> str | None:
        try:
            return self.executable_resolver(name, root)
        except TypeError:
            return self.executable_resolver(name)

    def _server_line(self, spec: LspServerSpec, state: str, count: int, root: Path, bootstrap: ClangdBootstrap) -> str:
        suffix = ""
        if spec.key == "clangd":
            flags = bootstrap.flags or (f"ccdb{presence(root / 'compile_commands.json')}",)
            suffix = "/" + "/".join(flags)
        return f"S:{spec.key}/{spec.family}/{state}/files={count}{suffix}"

    def _remember(self, result: str) -> str:
        self._last_start = result
        return result

    def _default_client_factory(self, spec: LspServerSpec, executable: str, root: Path) -> LspClient:
        return LspClient(spec, executable, root, process_factory=self._guarded_popen, process_guard=self.guard.register)

    def _guarded_popen(self, cmd, **kwargs) -> subprocess.Popen:
        return subprocess.Popen(cmd, **kwargs, **self.guard.spawn_kwargs)


def target_specs(files: list[dict[str, Any]]) -> list[tuple[LspServerSpec, int]]:
    targets = []
    for spec in SERVER_SPECS:
        count = sum(1 for item in files if is_file_supported(spec, item))
        if count > 0:
            targets.append((spec, count))
    return targets
