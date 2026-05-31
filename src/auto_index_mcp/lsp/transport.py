from __future__ import annotations

import json
import queue
import threading
from typing import Any, BinaryIO, Callable

from .lsp_protocol import read_headers


NotificationHandler = Callable[[dict[str, Any]], None]
ServerRequestHandler = Callable[[dict[str, Any]], Any]


class LspTransport:
    """JSON-RPC framing over an LSP server's stdio.

    A single reader thread frames incoming messages and routes them:
      - responses go to the per-request inbox keyed by id (never to another
        request's waiter, fixing the old shared-queue response stealing),
      - notifications go to a registered handler,
      - server->client requests are answered via a registered handler.
    """

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any] | None]] = {}
        self._notification_handlers: dict[str, NotificationHandler] = {}
        self._server_request_handlers: dict[str, ServerRequestHandler] = {}
        self._reader: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        self._reader = threading.Thread(target=self._read_loop, name="lsp-transport-reader", daemon=True)
        self._reader.start()

    def on_notification(self, method: str, handler: NotificationHandler) -> None:
        self._notification_handlers[method] = handler

    def on_server_request(self, method: str, handler: ServerRequestHandler) -> None:
        self._server_request_handlers[method] = handler

    def notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call(self, method: str, params: Any, timeout: float) -> dict[str, Any] | None:
        with self._state_lock:
            request_id = self._next_id
            self._next_id += 1
            inbox: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
            self._pending[request_id] = inbox
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            try:
                return inbox.get(timeout=max(0.0, timeout))
            except queue.Empty:
                return None
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def close(self) -> None:
        self._closed = True
        try:
            self._stdin.close()
        except OSError:
            pass

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._send_lock:
            if self._closed:
                raise OSError("LSP transport is closed")
            self._stdin.write(header + body)
            self._stdin.flush()

    def _read_loop(self) -> None:
        while True:
            try:
                headers = read_headers(self._stdout)
                if not headers:
                    break
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                body = self._stdout.read(length)
                if not body:
                    break
                message = json.loads(body.decode("utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                break
            self._dispatch(message)
        self._fail_pending()

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        has_id = "id" in message and message["id"] is not None
        if method and has_id:
            self._handle_server_request(message)
            return
        if method:
            handler = self._notification_handlers.get(method)
            if handler is not None:
                handler(message.get("params") or {})
            return
        if has_id:
            with self._state_lock:
                inbox = self._pending.get(message["id"])
            if inbox is not None:
                try:
                    inbox.put_nowait(message)
                except queue.Full:
                    pass

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        handler = self._server_request_handlers.get(message.get("method", ""))
        if handler is None:
            self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
            return
        try:
            result = handler(message.get("params") or {})
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:  # must always answer the server to avoid hangs
            self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}})

    def _fail_pending(self) -> None:
        with self._state_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for inbox in waiters:
            try:
                inbox.put_nowait(None)
            except queue.Full:
                pass
