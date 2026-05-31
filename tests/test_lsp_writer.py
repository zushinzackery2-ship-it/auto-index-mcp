from __future__ import annotations

import threading
import time

import pytest

from auto_index_mcp.core.lsp_writer import LspWriteTimeout, LspWriter


class BlockingStream:
    def __init__(self) -> None:
        self.started = threading.Event()

    def write(self, data: bytes) -> int:
        self.started.set()
        time.sleep(1.0)
        return len(data)

    def flush(self) -> None:
        pass


def test_lsp_writer_timeout_does_not_block_caller() -> None:
    stream = BlockingStream()
    writer = LspWriter("test")
    writer.start(stream)

    started = time.perf_counter()
    with pytest.raises(LspWriteTimeout):
        writer.send(b"payload", wait=True, timeout_seconds=0.05)
    elapsed = time.perf_counter() - started

    assert stream.started.is_set()
    assert elapsed < 0.3


def test_lsp_writer_async_send_returns_before_stream_write_finishes() -> None:
    stream = BlockingStream()
    writer = LspWriter("test")
    writer.start(stream)

    started = time.perf_counter()
    writer.send(b"payload", wait=False)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05