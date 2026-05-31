import io
import json
import os
import subprocess
import threading
from pathlib import Path

from auto_index_mcp.lsp.client import LspClient
from auto_index_mcp.lsp.lsp_specs import SERVER_SPECS


PY_SPEC = next(spec for spec in SERVER_SPECS if spec.key == "pyright")


def _write_frame(stream, obj) -> None:
    body = json.dumps(obj).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def _read_frame(stream):
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        text = line.decode("ascii").strip()
        if text == "":
            break
        if text.lower().startswith("content-length:"):
            length = int(text.split(":", 1)[1])
    return json.loads(stream.read(length).decode("utf-8")) if length else None


def _fake_server(read_stream, write_stream) -> None:
    while True:
        msg = _read_frame(read_stream)
        if msg is None:
            return
        method = msg.get("method")
        if method == "initialize":
            _write_frame(write_stream, {"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}})
        elif method == "textDocument/didOpen":
            uri = msg["params"]["textDocument"]["uri"]
            _write_frame(write_stream, {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "version": 1, "diagnostics": [
                    {"severity": 1, "range": {"start": {"line": 0, "character": 0}}, "message": "boom"}
                ]},
            })
        elif method == "shutdown":
            _write_frame(write_stream, {"jsonrpc": "2.0", "id": msg["id"], "result": None})
        elif method == "exit":
            return


class FakePopen:
    def __init__(self, *args, **kwargs) -> None:
        c2s_r, c2s_w = os.pipe()
        s2c_r, s2c_w = os.pipe()
        self.stdin = os.fdopen(c2s_w, "wb")
        self.stdout = os.fdopen(s2c_r, "rb")
        self.stderr = io.BytesIO(b"")
        self._server_in = os.fdopen(c2s_r, "rb")
        self._server_out = os.fdopen(s2c_w, "wb")
        self._returncode: int | None = None
        self._thread = threading.Thread(target=_fake_server, args=(self._server_in, self._server_out), daemon=True)
        self._thread.start()

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("fake", timeout)
        self._returncode = 0
        for handle in (self._server_out, self._server_in):
            try:
                handle.close()
            except OSError:
                pass
        return 0

    def kill(self):
        self._returncode = -9


def test_client_handshake_diagnostics_and_graceful_shutdown(tmp_path: Path) -> None:
    client = LspClient(PY_SPEC, "pyright-langserver", tmp_path, process_factory=FakePopen)

    assert client.start(2.0) == "ready"
    assert client.is_running()

    uri = (tmp_path / "a.py").resolve().as_uri()
    client.open_document(uri, "python", "x = 1\n")
    missing = client.wait_for_diagnostics({uri}, 2.0)

    assert missing == set()
    diagnostics = client.diagnostics_for(uri)
    assert diagnostics and diagnostics[0]["message"] == "boom"

    assert client.shutdown(2.0) == "stopped"
    assert not client.is_running()


def test_client_start_returns_error_when_spawn_fails(tmp_path: Path) -> None:
    def boom(*args, **kwargs):
        raise OSError("no such executable")

    client = LspClient(PY_SPEC, "missing", tmp_path, process_factory=boom)
    assert client.start(1.0) == "error"
    assert not client.is_running()
