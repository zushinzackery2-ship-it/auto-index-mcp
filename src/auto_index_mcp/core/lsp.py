from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, BinaryIO


ProcessFactory = Callable[..., subprocess.Popen]
ExecutableResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class LspServerSpec:
    key: str
    family: str
    executable: str
    args: tuple[str, ...]
    languages: frozenset[str]
    extensions: frozenset[str]


SERVER_SPECS = (
    LspServerSpec(
        key="clangd",
        family="c-family",
        executable="clangd",
        args=(),
        languages=frozenset({"c", "cpp"}),
        extensions=frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm", ".cu"}),
    ),
    LspServerSpec(
        key="pyright",
        family="python",
        executable="pyright-langserver",
        args=("--stdio",),
        languages=frozenset({"python"}),
        extensions=frozenset({".py"}),
    ),
    LspServerSpec(
        key="tsserver",
        family="js-ts",
        executable="typescript-language-server",
        args=("--stdio",),
        languages=frozenset({"javascript", "typescript"}),
        extensions=frozenset({".js", ".jsx", ".ts", ".tsx"}),
    ),
    LspServerSpec(
        key="rust-analyzer",
        family="rust",
        executable="rust-analyzer",
        args=(),
        languages=frozenset({"rust"}),
        extensions=frozenset({".rs"}),
    ),
    LspServerSpec(
        key="gopls",
        family="go",
        executable="gopls",
        args=(),
        languages=frozenset({"go"}),
        extensions=frozenset({".go"}),
    ),
)


class LspManager:
    def __init__(
        self,
        executable_resolver: ExecutableResolver | None = None,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.executable_resolver = executable_resolver or shutil.which
        self.process_factory = process_factory or subprocess.Popen
        self.sessions: dict[str, LspSession] = {}

    def start(self, root: Path | None, files: list[dict[str, Any]], timeout_seconds: float = 10.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        targets = self._target_specs(files)
        if not targets:
            return f"LSP|no_targets|{root.as_posix()}"

        lines = []
        ready_count = 0
        missing_count = 0
        error_count = 0
        for spec, count in targets:
            session = self.sessions.get(spec.key)
            if session and session.is_running():
                state = "ready"
                ready_count += 1
            else:
                executable = self.executable_resolver(spec.executable)
                if not executable:
                    state = "missing"
                    missing_count += 1
                else:
                    session = LspSession(spec, executable, self.process_factory)
                    self.sessions[spec.key] = session
                    state = session.start(root, timeout_seconds)
                    if state == "ready":
                        ready_count += 1
                    else:
                        error_count += 1
            lines.append(self._server_line(spec, state, count, root))

        status = "ready"
        if ready_count == 0 and (missing_count or error_count):
            status = "unavailable"
        elif missing_count or error_count:
            status = "partial"
        return "\n".join([f"LSP|{status}|{root.as_posix()}"] + lines)

    def shutdown(self, root: Path | None, timeout_seconds: float = 5.0) -> str:
        if root is None:
            return "LSP|not_configured"
        timeout_seconds = max(0.1, timeout_seconds)
        if not self.sessions:
            return f"LSP|stopped|{root.as_posix()}"

        lines = []
        for key in sorted(self.sessions):
            session = self.sessions[key]
            state = session.shutdown(timeout_seconds)
            lines.append(f"S:{key}/{state}")
        self.sessions.clear()
        return "\n".join([f"LSP|stopped|{root.as_posix()}"] + lines)

    def stop_all(self, timeout_seconds: float = 2.0) -> None:
        for session in list(self.sessions.values()):
            session.shutdown(timeout_seconds)
        self.sessions.clear()

    def _target_specs(self, files: list[dict[str, Any]]) -> list[tuple[LspServerSpec, int]]:
        languages = [item.get("language", "") for item in files]
        extensions = [item.get("extension", "") for item in files]
        targets = []
        for spec in SERVER_SPECS:
            count = sum(1 for language in languages if language in spec.languages)
            if count == 0:
                count = sum(1 for extension in extensions if extension in spec.extensions)
            if count > 0:
                targets.append((spec, count))
        return targets

    def _server_line(self, spec: LspServerSpec, state: str, count: int, root: Path) -> str:
        flags = []
        if spec.key == "clangd":
            flags.append(f"ccdb{_presence(root / 'compile_commands.json')}")
            flags.append(f".clangd{_presence(root / '.clangd')}")
        suffix = "/" + "/".join(flags) if flags else ""
        return f"S:{spec.key}/{spec.family}/{state}/files={count}{suffix}"


class LspSession:
    def __init__(self, spec: LspServerSpec, executable: str, process_factory: ProcessFactory) -> None:
        self.spec = spec
        self.executable = executable
        self.process_factory = process_factory
        self.process: subprocess.Popen | None = None
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_lines: queue.Queue[str] = queue.Queue(maxsize=20)
        self.next_id = 1

    def start(self, root: Path, timeout_seconds: float) -> str:
        try:
            self.process = self.process_factory(
                [self.executable, *self.spec.args],
                cwd=str(root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if self.process.stdout:
                threading.Thread(target=self._read_stdout, args=(self.process.stdout,), daemon=True).start()
            if self.process.stderr:
                threading.Thread(target=self._read_stderr, args=(self.process.stderr,), daemon=True).start()
            request_id = self._send_request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": root.as_uri(),
                    "capabilities": {},
                    "workspaceFolders": [{"uri": root.as_uri(), "name": root.name}],
                },
            )
            response = self._wait_for_response(request_id, timeout_seconds)
            if not response or "error" in response:
                self.shutdown(0.2)
                return "error"
            self._send_notification("initialized", {})
            return "ready"
        except (OSError, ValueError):
            self.shutdown(0.2)
            return "error"

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def shutdown(self, timeout_seconds: float) -> str:
        if not self.process:
            return "stopped"
        if self.process.poll() is not None:
            return "stopped"
        try:
            request_id = self._send_request("shutdown", None)
            self._wait_for_response(request_id, timeout_seconds)
            self._send_notification("exit", None)
            self.process.wait(timeout=timeout_seconds)
            return "stopped"
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                return "killed"
            return "killed"

    def _send_request(self, method: str, params: Any) -> int:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id

    def _send_notification(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise OSError("LSP process stdin is not available")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.process.stdin.write(header + body)
        self.process.stdin.flush()

    def _wait_for_response(self, request_id: int, timeout_seconds: float) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                message = self.responses.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                return None
            if message.get("id") == request_id:
                return message
        return None

    def _read_stdout(self, stream: BinaryIO) -> None:
        while True:
            try:
                headers = _read_headers(stream)
                if not headers:
                    return
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                body = stream.read(length)
                if not body:
                    return
                self.responses.put(json.loads(body.decode("utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                return

    def _read_stderr(self, stream: BinaryIO) -> None:
        while True:
            try:
                line = stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    if self.stderr_lines.full():
                        self.stderr_lines.get_nowait()
                    self.stderr_lines.put_nowait(text)
            except OSError:
                return


def _read_headers(stream: BinaryIO) -> dict[str, str]:
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            return {}
        if line in (b"\r\n", b"\n"):
            return headers
        name, _, value = line.decode("ascii", errors="ignore").partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()


def _presence(path: Path) -> str:
    return "+" if path.exists() else "-"
