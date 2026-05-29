from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable


ProcessFactory = Callable[..., subprocess.Popen]


@dataclass(frozen=True)
class DiagnosticLine:
    severity: str
    path: str
    line: int
    character: int
    message: str


class LspSession:
    def __init__(self, spec, executable: str, process_factory: ProcessFactory) -> None:
        self.spec = spec
        self.executable = executable
        self.process_factory = process_factory
        self.process: subprocess.Popen | None = None
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_lines: queue.Queue[str] = queue.Queue(maxsize=20)
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self.open_versions: dict[str, int] = {}
        self.open_texts: dict[str, str] = {}
        self.open_workspace_signatures: dict[str, str] = {}
        self.root_key: str | None = None
        self.next_id = 1

    def start(self, root: Path, timeout_seconds: float) -> str:
        try:
            self.root_key = str(root.resolve())
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

    def open_document(self, uri: str, language_id: str, version: int, text: str, workspace_signature: str = "") -> None:
        unchanged_text = self.open_texts.get(uri) == text
        unchanged_workspace = self.open_workspace_signatures.get(uri) == workspace_signature
        if unchanged_text and unchanged_workspace and uri in self.diagnostics:
            return
        next_version = max(version, self.open_versions.get(uri, 0) + 1)
        already_open = uri in self.open_versions
        reopen = already_open and unchanged_text and not unchanged_workspace
        self.diagnostics.pop(uri, None)
        previous_version = self.open_versions.get(uri)
        previous_text = self.open_texts.get(uri)
        previous_workspace_signature = self.open_workspace_signatures.get(uri)
        self.open_versions[uri] = next_version
        self.open_texts[uri] = text
        self.open_workspace_signatures[uri] = workspace_signature
        try:
            if reopen:
                self._send_notification("textDocument/didClose", {"textDocument": {"uri": uri}})
                self._send_notification(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": language_id,
                            "version": next_version,
                            "text": text,
                        }
                    },
                )
            elif already_open:
                self._send_notification(
                    "textDocument/didChange",
                    {
                        "textDocument": {
                            "uri": uri,
                            "version": next_version,
                        },
                        "contentChanges": [{"text": text}],
                    },
                )
            else:
                self._send_notification(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": language_id,
                            "version": next_version,
                            "text": text,
                        }
                    },
                )
        except OSError:
            if previous_version is None:
                self.open_versions.pop(uri, None)
            else:
                self.open_versions[uri] = previous_version
            if previous_text is None:
                self.open_texts.pop(uri, None)
            else:
                self.open_texts[uri] = previous_text
            if previous_workspace_signature is None:
                self.open_workspace_signatures.pop(uri, None)
            else:
                self.open_workspace_signatures[uri] = previous_workspace_signature
            raise

    def wait_for_diagnostics(self, uris: set[str], timeout_seconds: float) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if all(uri in self.diagnostics for uri in uris):
                return
            time.sleep(0.01)

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
                self._handle_message(json.loads(body.decode("utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                return

    def _handle_message(self, message: dict[str, Any]) -> None:
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = params.get("uri")
            if isinstance(uri, str):
                expected_version = self.open_versions.get(uri)
                if expected_version is None:
                    return
                diagnostic_version = params.get("version")
                if isinstance(diagnostic_version, int) and diagnostic_version < expected_version:
                    return
                self.diagnostics[uri] = params.get("diagnostics") or []
            return
        if "id" in message:
            self.responses.put(message)

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
