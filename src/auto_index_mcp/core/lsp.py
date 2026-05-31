from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .clangd_bootstrap import ClangdBootstrap, prepare_clangd
from .lsp_checks import LspCheckMixin, remaining_seconds
from .lsp_resolver import resolve_lsp_executable
from .lsp_session import LspSession, ProcessFactory
from .lsp_specs import SERVER_SPECS, LspServerSpec, effective_spec, is_file_supported, presence


ExecutableResolver = Callable[..., str | None]


class LspManager(LspCheckMixin):
    def __init__(
        self,
        executable_resolver: ExecutableResolver | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or resolve_lsp_executable
        self.process_factory = process_factory or subprocess.Popen
        self.sessions: dict[str, LspSession] = {}
        self.session_signatures: dict[str, tuple[str, str, tuple[str, ...], str]] = {}
        self.unavailable_servers: dict[str, tuple[str, int]] = {}
        self.bootstrap = ClangdBootstrap((), ())
        self.bootstrap_input_signature = ""
        self._start_lock = threading.RLock()
        self._start_thread: threading.Thread | None = None
        self._start_root: Path | None = None
        self._last_start_result = "LSP|not_started"
        self._last_start_updated_at: float | None = None

    def start(self, root: Path | None, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        targets = self._target_specs(files)
        if not targets:
            self.stop_all(0.2)
            return f"LSP|no_targets|{root.as_posix()}"
        target_keys = {spec.key for spec, _ in targets}
        for key in [key for key in self.sessions if key not in target_keys]:
            self.sessions[key].shutdown(0.2)
            self.sessions.pop(key, None)
            self.session_signatures.pop(key, None)

        lines = []
        ready_count = 0
        missing_count = 0
        error_count = 0
        unavailable_servers: dict[str, tuple[str, int]] = {}
        bootstrap = self._prepare_clangd(root, files, target_keys)
        for spec, count in targets:
            effective = effective_spec(spec, bootstrap)
            executable = self._resolve_executable(spec.executable, root)
            signature = (str(root.resolve()), executable or "", effective.args, bootstrap.signature if spec.key == "clangd" else "")
            session = self.sessions.get(spec.key)
            if session and session.is_running() and self.session_signatures.get(spec.key) == signature:
                state = "ready"
                ready_count += 1
            else:
                if session and session.is_running():
                    session.shutdown(0.2)
                self.sessions.pop(spec.key, None)
                self.session_signatures.pop(spec.key, None)
                if not executable:
                    state = "missing"
                    missing_count += 1
                    unavailable_servers[spec.key] = (state, count)
                else:
                    session = LspSession(effective, executable, self.process_factory)
                    state = session.start(root, max(0.1, remaining_seconds(deadline)))
                    if state == "ready":
                        self.sessions[spec.key] = session
                        self.session_signatures[spec.key] = signature
                        ready_count += 1
                    else:
                        error_count += 1
                        unavailable_servers[spec.key] = (state, count)
            lines.append(self._server_line(spec, state, count, root, bootstrap))

        self.unavailable_servers = unavailable_servers
        status = "ready"
        if ready_count == 0 and (missing_count or error_count):
            status = "unavailable"
        elif missing_count or error_count:
            status = "partial"
        result = "\n".join([f"LSP|{status}|{root.as_posix()}"] + lines)
        with self._start_lock:
            self._last_start_result = result
            self._last_start_updated_at = time.time()
        return result

    def start_async(self, root: Path | None, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        if root is None:
            return "LSP|not_configured"
        with self._start_lock:
            if self._is_starting(root):
                return self._starting_result(root)
        cached = self._cached_ready_start(root, files)
        if cached:
            return cached
        with self._start_lock:
            if self._is_starting(root):
                return self._starting_result(root)
            root_snapshot = root
            file_snapshot = [dict(item) for item in files]
            self._start_root = root_snapshot
            self._last_start_result = f"LSP|starting|{root_snapshot.as_posix()}"
            self._last_start_updated_at = time.time()
            self._start_thread = threading.Thread(
                target=self._run_background_start,
                args=(root_snapshot, file_snapshot, timeout_seconds),
                name="auto-index-lsp-start",
                daemon=True,
            )
            self._start_thread.start()
            return self._starting_result(root_snapshot)

    def start_status(self, root: Path | None) -> str:
        if root is None:
            return "LSP|not_configured"
        with self._start_lock:
            if self._is_starting(root):
                return self._starting_result(root)
            return self._last_start_result

    def check_start_status(self, root: Path | None) -> str | None:
        if root is None:
            return None
        with self._start_lock:
            if not self._is_starting(root):
                return None
            elapsed = ""
            if self._last_start_updated_at is not None:
                elapsed = f"|elapsed={round(time.time() - self._last_start_updated_at, 3)}"
            return f"CHK|starting|{root.as_posix()}{elapsed}"

    def _run_background_start(self, root: Path, files: list[dict[str, Any]], timeout_seconds: float) -> None:
        try:
            self.start(root, files, timeout_seconds)
        except Exception as exc:
            with self._start_lock:
                self._last_start_result = f"LSP|error|{root.as_posix()}|{type(exc).__name__}:{exc}"
                self._last_start_updated_at = time.time()
        finally:
            with self._start_lock:
                if self._start_root == root:
                    self._start_thread = None

    def _is_starting(self, root: Path) -> bool:
        return (
            self._start_thread is not None
            and self._start_thread.is_alive()
            and self._start_root == root
        )

    def _starting_result(self, root: Path) -> str:
        suffix = ""
        if self._last_start_updated_at is not None:
            suffix = f"|elapsed={round(time.time() - self._last_start_updated_at, 3)}"
        return f"LSP|starting|{root.as_posix()}{suffix}"

    def _cached_ready_start(self, root: Path, files: list[dict[str, Any]]) -> str | None:
        targets = self._target_specs(files)
        if not targets:
            self.stop_all(0.2)
            return f"LSP|no_targets|{root.as_posix()}"
        target_keys = {spec.key for spec, _ in targets}
        if any(key not in self.sessions for key in target_keys):
            return None
        if any(key not in target_keys for key in self.sessions):
            return None
        if "clangd" in target_keys:
            input_signature = self._clangd_input_signature(root, files)
            if input_signature != self.bootstrap_input_signature:
                return None
            bootstrap = self.bootstrap
        else:
            bootstrap = ClangdBootstrap((), ())
        lines = []
        for spec, count in targets:
            effective = effective_spec(spec, bootstrap)
            executable = self._resolve_executable(spec.executable, root)
            signature = (str(root.resolve()), executable or "", effective.args, bootstrap.signature if spec.key == "clangd" else "")
            session = self.sessions.get(spec.key)
            if not session or not session.is_running() or self.session_signatures.get(spec.key) != signature:
                return None
            lines.append(self._server_line(spec, "ready", count, root, bootstrap))
        result = "\n".join([f"LSP|ready|{root.as_posix()}"] + lines)
        with self._start_lock:
            self._last_start_result = result
            self._last_start_updated_at = time.time()
        return result

    def shutdown(self, root: Path | None, timeout_seconds: float = 5.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        if not self.sessions:
            with self._start_lock:
                self._start_thread = None
                self._start_root = None
                self._last_start_result = f"LSP|stopped|{root.as_posix()}"
                self._last_start_updated_at = time.time()
            return f"LSP|stopped|{root.as_posix()}"

        lines = []
        for key in sorted(self.sessions):
            session = self.sessions[key]
            state = session.shutdown(timeout_seconds)
            lines.append(f"S:{key}/{state}")
        self.sessions.clear()
        self.session_signatures.clear()
        self.unavailable_servers.clear()
        with self._start_lock:
            self._start_thread = None
            self._start_root = None
            self._last_start_result = f"LSP|stopped|{root.as_posix()}"
            self._last_start_updated_at = time.time()
        return "\n".join([f"LSP|stopped|{root.as_posix()}"] + lines)

    def stop_all(self, timeout_seconds: float = 2.0) -> None:
        for session in list(self.sessions.values()):
            session.shutdown(timeout_seconds)
        self.sessions.clear()
        self.session_signatures.clear()
        self.unavailable_servers.clear()
        with self._start_lock:
            self._start_thread = None
            self._start_root = None
            self._last_start_result = "LSP|stopped"
            self._last_start_updated_at = time.time()

    def _target_specs(self, files: list[dict[str, Any]]) -> list[tuple[LspServerSpec, int]]:
        targets = []
        for spec in SERVER_SPECS:
            count = sum(1 for item in files if is_file_supported(spec, item))
            if count > 0:
                targets.append((spec, count))
        return targets

    def _resolve_executable(self, name: str, root: Path) -> str | None:
        try:
            return self.executable_resolver(name, root)
        except TypeError:
            return self.executable_resolver(name)

    def _server_line(self, spec: LspServerSpec, state: str, count: int, root: Path, bootstrap: ClangdBootstrap) -> str:
        flags = []
        if spec.key == "clangd":
            flags.extend(bootstrap.flags or (f"ccdb{presence(root / 'compile_commands.json')}", f".clangd{presence(root / '.clangd')}", "cfg=none"))
        suffix = "/" + "/".join(flags) if flags else ""
        return f"S:{spec.key}/{spec.family}/{state}/files={count}{suffix}"

    def _prepare_clangd(self, root: Path, files: list[dict[str, Any]], target_keys: set[str]) -> ClangdBootstrap:
        if "clangd" not in target_keys:
            self.bootstrap = ClangdBootstrap((), ())
            self.bootstrap_input_signature = ""
            return self.bootstrap
        input_signature = self._clangd_input_signature(root, files)
        if input_signature == self.bootstrap_input_signature:
            return self.bootstrap
        self.bootstrap = prepare_clangd(root, files)
        self.bootstrap_input_signature = input_signature
        return self.bootstrap

    def _clangd_input_signature(self, root: Path, files: list[dict[str, Any]]) -> str:
        digest = hashlib.sha1()
        digest.update(str(root.resolve()).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        for item in sorted(files, key=lambda value: value["path"]):
            if not _is_clangd_input_item(item):
                continue
            _add_item_signature(digest, item)
        _add_path_signature(digest, root / ".clangd")
        _add_path_signature(digest, root / "compile_commands.json")
        return digest.hexdigest()


def _is_clangd_input_item(item: dict[str, Any]) -> bool:
    path = item.get("path", "").lower()
    return (
        is_file_supported(SERVER_SPECS[0], item)
        or path.endswith(".vcxproj")
        or path.endswith("compile_commands.json")
    )


def _add_item_signature(digest: Any, item: dict[str, Any]) -> None:
    digest.update(item["path"].encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    digest.update(str(item.get("sha1", "")).encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(item.get("size", 0)).encode("ascii", errors="ignore"))
    digest.update(b"\0")
    digest.update(str(item.get("mtime_ns", 0)).encode("ascii", errors="ignore"))
    digest.update(b"\0")


def _add_path_signature(digest: Any, path: Path) -> None:
    digest.update(str(path.resolve()).encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")
    try:
        stat = path.stat()
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    except OSError:
        digest.update(b"missing")
    digest.update(b"\0")
