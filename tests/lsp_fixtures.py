from __future__ import annotations

import io
import json
import threading
import time
from typing import Any, Callable


class FakeProcess:
    def __init__(self, command: list[str], cwd: str, stdout: bytes | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout or lsp_message({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}))
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeProcessFactory:
    def __init__(self, stdout: bytes | None = None) -> None:
        self.stdout = stdout
        self.processes: list[FakeProcess] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        process = FakeProcess(command, kwargs["cwd"], self.stdout)
        self.processes.append(process)
        return process


def lsp_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def messages_from_stream(stream: bytes) -> list[dict[str, Any]]:
    messages = []
    offset = 0
    while offset < len(stream):
        header_end = stream.find(b"\r\n\r\n", offset)
        if header_end < 0:
            break
        header = stream[offset:header_end].decode("ascii")
        length = int(next(line.split(":", 1)[1].strip() for line in header.splitlines() if line.lower().startswith("content-length:")))
        body_start = header_end + 4
        body = stream[body_start:body_start + length]
        if len(body) < length:
            break
        messages.append(json.loads(body.decode("utf-8")))
        offset = body_start + length
    return messages


def publish_after_document_message(
    factory: FakeProcessFactory,
    method: str,
    ordinal: int,
    publisher: Callable[[dict[str, Any]], None],
) -> threading.Thread:
    def worker() -> None:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if not factory.processes:
                time.sleep(0.005)
                continue
            matches = [
                message
                for message in messages_from_stream(factory.processes[0].stdin.getvalue())
                if message.get("method") == method
            ]
            if len(matches) >= ordinal:
                publisher(matches[ordinal - 1])
                return
            time.sleep(0.005)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
