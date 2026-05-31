from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .lsp_protocol import normalize_uri
from .transport import LspTransport


ProcessFactory = Callable[..., subprocess.Popen]
ProcessGuard = Callable[[subprocess.Popen], None]


class LspClient:
    """One running LSP server subprocess plus its JSON-RPC transport.

    Diagnostics are collected from pushed ``publishDiagnostics`` notifications
    into a cache and waited on with a Condition (event-driven, no busy poll).
    Shutdown is a graceful close -> shutdown -> exit -> kill sequence that reaps
    the child so it never lingers as an orphan.
    """

    def __init__(
        self,
        spec,
        executable: str,
        root: Path,
        process_factory: ProcessFactory | None = None,
        process_guard: ProcessGuard | None = None,
    ) -> None:
        self.spec = spec
        self.executable = executable
        self.root = root
        self.process_factory = process_factory or subprocess.Popen
        self.process_guard = process_guard
        self.process: subprocess.Popen | None = None
        self.transport: LspTransport | None = None
        self._cond = threading.Condition()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._open_versions: dict[str, int] = {}
        self._open_texts: dict[str, str] = {}
        self._open_signatures: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeout_seconds: float) -> str:
        try:
            self.process = self.process_factory(
                [self.executable, *self.spec.args],
                cwd=str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, ValueError):
            return "error"
        if self.process_guard is not None:
            self.process_guard(self.process)
        if not self.process.stdin or not self.process.stdout:
            self.shutdown(0.2)
            return "error"
        if self.process.stderr:
            threading.Thread(target=self._drain_stderr, args=(self.process.stderr,), daemon=True).start()
        self.transport = LspTransport(self.process.stdin, self.process.stdout)
        self._register_handlers()
        self.transport.start()
        response = self.transport.call(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root.resolve().as_uri(),
                "capabilities": {"textDocument": {"publishDiagnostics": {}, "diagnostic": {}}},
                "workspaceFolders": [{"uri": self.root.resolve().as_uri(), "name": self.root.name}],
            },
            timeout_seconds,
        )
        if not response or "error" in response:
            self.shutdown(0.2)
            return "error"
        self.transport.notify("initialized", {})
        return "ready"

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def shutdown(self, timeout_seconds: float) -> str:
        process = self.process
        if process is None:
            return "stopped"
        if process.poll() is not None:
            return "stopped"
        transport = self.transport
        try:
            if transport is not None:
                for uri in list(self._open_versions):
                    transport.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
                transport.call("shutdown", None, min(timeout_seconds, 1.0))
                transport.notify("exit", None)
                transport.close()
        except OSError:
            pass
        try:
            process.wait(timeout=timeout_seconds)
            return "stopped"
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                return "killed"
            return "killed"

    # -- documents & diagnostics ------------------------------------------

    def open_document(self, uri: str, language_id: str, text: str, workspace_signature: str = "") -> None:
        if self.transport is None:
            raise OSError("LSP transport is not started")
        uri = normalize_uri(uri)
        already_open = uri in self._open_versions
        unchanged_text = self._open_texts.get(uri) == text
        unchanged_workspace = self._open_signatures.get(uri) == workspace_signature
        if already_open and unchanged_text and unchanged_workspace and uri in self._diagnostics:
            return
        version = self._open_versions.get(uri, 0) + 1
        reopen = already_open and unchanged_text and not unchanged_workspace
        with self._cond:
            self._diagnostics.pop(uri, None)
            self._open_versions[uri] = version
            self._open_texts[uri] = text
            self._open_signatures[uri] = workspace_signature
        if reopen:
            self.transport.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            self._notify_open(uri, language_id, version, text)
        elif already_open:
            self.transport.notify(
                "textDocument/didChange",
                {"textDocument": {"uri": uri, "version": version}, "contentChanges": [{"text": text}]},
            )
        else:
            self._notify_open(uri, language_id, version, text)

    def wait_for_diagnostics(self, uris: set[str], timeout_seconds: float) -> set[str]:
        wanted = {normalize_uri(uri) for uri in uris}
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._cond:
            self._cond.wait_for(
                lambda: all(uri in self._diagnostics for uri in wanted),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            return {uri for uri in wanted if uri not in self._diagnostics}

    def pull_diagnostics(self, uri: str, timeout_seconds: float) -> None:
        if self.transport is None:
            return
        response = self.transport.call(
            "textDocument/diagnostic", {"textDocument": {"uri": normalize_uri(uri)}}, timeout_seconds
        )
        if not response or "result" not in response:
            return
        items = (response["result"] or {}).get("items")
        if isinstance(items, list):
            with self._cond:
                self._diagnostics[normalize_uri(uri)] = items
                self._cond.notify_all()

    def diagnostics_for(self, uri: str) -> list[dict[str, Any]]:
        return self._diagnostics.get(normalize_uri(uri), [])

    # -- internals ---------------------------------------------------------

    def _notify_open(self, uri: str, language_id: str, version: int, text: str) -> None:
        assert self.transport is not None
        self.transport.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": language_id, "version": version, "text": text}},
        )

    def _register_handlers(self) -> None:
        assert self.transport is not None
        self.transport.on_notification("textDocument/publishDiagnostics", self._on_publish_diagnostics)
        self.transport.on_server_request("workspace/configuration", lambda params: [None for _ in (params.get("items") or [])])
        self.transport.on_server_request("client/registerCapability", lambda params: None)
        self.transport.on_server_request("window/workDoneProgress/create", lambda params: None)
        self.transport.on_server_request("workspace/workspaceFolders", lambda params: [])

    def _on_publish_diagnostics(self, params: dict[str, Any]) -> None:
        uri = params.get("uri")
        if not isinstance(uri, str):
            return
        uri = normalize_uri(uri)
        with self._cond:
            expected = self._open_versions.get(uri)
            if expected is None:
                return
            version = params.get("version")
            if isinstance(version, int) and version < expected:
                return
            self._diagnostics[uri] = params.get("diagnostics") or []
            self._cond.notify_all()

    def _drain_stderr(self, stream) -> None:
        try:
            for _ in iter(stream.readline, b""):
                pass
        except OSError:
            return
