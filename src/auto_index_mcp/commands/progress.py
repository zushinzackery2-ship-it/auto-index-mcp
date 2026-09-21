from __future__ import annotations

import sys
import threading
import time


def bar(fraction: float) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * 20)
    return "[" + "#" * filled + "-" * (20 - filled) + "]"


class Progress:
    """TTY progress or bounded milestone output for redirected commands."""

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.tty = (not quiet) and sys.stdout.isatty()
        self._lock = threading.Lock()
        self._last_width = 0
        self._milestone = -1
        self._embed_state = None
        self._embed_started = None

    def update(self, text: str, fraction: float | None = None) -> None:
        if self.quiet:
            return
        with self._lock:
            if self.tty:
                pad = max(0, self._last_width - len(text))
                sys.stdout.write("\r" + text + " " * pad)
                sys.stdout.flush()
                self._last_width = len(text)
            elif fraction is not None:
                bucket = int(max(0.0, min(1.0, fraction)) * 10)
                if bucket > self._milestone:
                    self._milestone = bucket
                    print(text, flush=True)

    def line(self, text: str) -> None:
        with self._lock:
            if self.tty and self._last_width:
                sys.stdout.write("\r" + " " * self._last_width + "\r")
                self._last_width = 0
            self._milestone = -1
            print(text, flush=True)

    def embedding_update(self, done: int, total: int, reused: int) -> None:
        if self._embed_started is None:
            self._embed_started = time.time()
        self._embed_state = (done, total, reused)
        self._render_embedding()

    def tick_embedding(self) -> None:
        if self._embed_state is not None:
            self._render_embedding()

    def _render_embedding(self) -> None:
        state = self._embed_state
        if state is None:
            return
        done, total, reused = state
        elapsed = time.time() - (self._embed_started or time.time())
        if total <= 0:
            self.update(f"embedding: {done} files processed  {elapsed:.1f}s (reused {reused})")
            return
        fraction = done / total
        self.update(f"{bar(fraction)} embedding {done}/{total} files  {elapsed:.1f}s (reused {reused})", fraction)
