from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import BinaryIO


class LspWriteTimeout(OSError):
    pass


@dataclass
class LspWriteRequest:
    data: bytes
    done: threading.Event
    error: BaseException | None = None


class LspWriter:
    def __init__(self, name: str, queue_size: int = 256) -> None:
        self.name = name
        self.queue_size = queue_size
        self.queue: queue.Queue[LspWriteRequest | None] = queue.Queue(maxsize=queue_size)
        self.thread: threading.Thread | None = None
        self.failed = False

    def start(self, stream: BinaryIO) -> None:
        self.queue = queue.Queue(maxsize=self.queue_size)
        self.failed = False
        self.thread = threading.Thread(target=self._run, args=(stream,), name=f"auto-index-lsp-writer-{self.name}", daemon=True)
        self.thread.start()

    def send(self, data: bytes, wait: bool = True, timeout_seconds: float = 0.25) -> None:
        if self.failed or self.thread is None or not self.thread.is_alive():
            raise OSError("LSP writer is not available")
        request = LspWriteRequest(data, threading.Event())
        try:
            self.queue.put_nowait(request)
        except queue.Full as exc:
            self.failed = True
            raise OSError("LSP writer queue is full") from exc
        if not wait:
            return
        if not request.done.wait(max(0.01, timeout_seconds)):
            self.failed = True
            raise LspWriteTimeout("LSP writer timed out")
        if request.error is not None:
            self.failed = True
            raise OSError("LSP writer failed") from request.error

    def stop(self) -> None:
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            self.failed = True

    def _run(self, stream: BinaryIO) -> None:
        while True:
            request = self.queue.get()
            if request is None:
                return
            try:
                stream.write(request.data)
                stream.flush()
            except BaseException as exc:
                self.failed = True
                request.error = exc
            finally:
                request.done.set()