import json
import os
import threading
import time

from auto_index_mcp.lsp.transport import LspTransport


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


class _Pipes:
    """A transport wired to a fake server over two OS pipes."""

    def __init__(self) -> None:
        c2s_r, c2s_w = os.pipe()
        s2c_r, s2c_w = os.pipe()
        self.transport_in = os.fdopen(c2s_w, "wb", buffering=0)
        self.transport_out = os.fdopen(s2c_r, "rb", buffering=0)
        self.server_in = os.fdopen(c2s_r, "rb", buffering=0)
        self.server_out = os.fdopen(s2c_w, "wb", buffering=0)
        self.transport = LspTransport(self.transport_in, self.transport_out)

    def close(self) -> None:
        # Close the server's write end first so the transport's reader thread
        # gets EOF and unwinds; closing the read end while a readline is in
        # progress can hang on Windows.
        self.transport.close()
        try:
            self.server_out.close()
        except OSError:
            pass
        time.sleep(0.05)
        for handle in (self.transport_out, self.server_in):
            try:
                handle.close()
            except OSError:
                pass


def test_responses_route_by_id_even_out_of_order() -> None:
    pipes = _Pipes()

    def server() -> None:
        while True:
            msg = _read_frame(pipes.server_in)
            if msg is None:
                return
            if "id" in msg:
                if msg["method"] == "slow":
                    time.sleep(0.15)  # respond after the fast call
                _write_frame(pipes.server_out, {"jsonrpc": "2.0", "id": msg["id"], "result": {"echo": msg["method"]}})

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    pipes.transport.start()

    results: dict[str, dict] = {}

    def call(method: str) -> None:
        results[method] = pipes.transport.call(method, {}, timeout=2.0)

    slow = threading.Thread(target=call, args=("slow",))
    fast = threading.Thread(target=call, args=("fast",))
    slow.start()
    time.sleep(0.02)
    fast.start()
    slow.join(3)
    fast.join(3)

    assert results["slow"]["result"] == {"echo": "slow"}
    assert results["fast"]["result"] == {"echo": "fast"}
    pipes.close()


def test_notification_handler_is_invoked() -> None:
    pipes = _Pipes()
    seen: list[dict] = []
    pipes.transport.on_notification("window/logMessage", seen.append)
    pipes.transport.start()

    _write_frame(pipes.server_out, {"jsonrpc": "2.0", "method": "window/logMessage", "params": {"message": "hi"}})
    deadline = time.time() + 2.0
    while not seen and time.time() < deadline:
        time.sleep(0.01)

    assert seen == [{"message": "hi"}]
    pipes.close()


def test_server_request_gets_answered() -> None:
    pipes = _Pipes()
    pipes.transport.on_server_request("workspace/configuration", lambda params: [None])
    pipes.transport.start()

    _write_frame(pipes.server_out, {"jsonrpc": "2.0", "id": 7, "method": "workspace/configuration", "params": {}})
    reply = _read_frame(pipes.server_in)

    assert reply["id"] == 7
    assert reply["result"] == [None]
    pipes.close()
