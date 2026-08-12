"""Command-line entry point.

Subcommands:
    build [path]    build (or refresh) the persistent index synchronously,
                    including semantic vectors, then exit. Pre-building means
                    a later MCP session attaches instantly instead of paying
                    the first-build wait.
    status [path]   read-only summary of an existing index, no server started.
    serve ...       run the MCP server (also the default with no subcommand,
                    so existing MCP client configs keep working unchanged).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

_BAR_WIDTH = 20


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:]) if argv is None else list(argv)
    if args and args[0] == "build":
        return _run_build(args[1:])
    if args and args[0] == "status":
        return _run_status(args[1:])
    if args and args[0] == "serve":
        args = args[1:]
    from .mcp_api.server import main as serve_main

    serve_main(args)
    return 0


# ---- build ---------------------------------------------------------------


def _run_build(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp build",
        description="Build the persistent code index for a project and exit.",
    )
    parser.add_argument("path", nargs="?", default=".", help="project root (default: current directory)")
    parser.add_argument("--rebuild", action="store_true", help="force a full rescan even when the index is fresh")
    parser.add_argument("--no-semantic", action="store_true", help="skip semantic embedding vectors")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    from .core.service import AutoIndexService

    service = AutoIndexService()
    printer = _Progress(quiet=args.quiet)
    if args.no_semantic:
        service.semantic_enabled = False
    else:
        service.embedding_progress = printer.embedding_update

    started = time.time()
    try:
        # Wiring the embedder up front routes a post-rebuild embedding pass
        # through the no-skip path, so changed symbols always re-embed
        # (unchanged windows are still reused via their text hash).
        service.enable(str(root), rebuild=False, refresh_embedder=not args.no_semantic)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    printer.line(f"project: {root}")

    try:
        index_ok = _build_index(service, root, args.rebuild, printer, started)
        if not index_ok:
            return 1
        if args.no_semantic:
            printer.line("semantic vectors: skipped (--no-semantic)")
            return 0
        return 0 if _build_embeddings(service, printer) else 1
    finally:
        service.stop_watcher()


def _build_index(
    service: Any,
    root: Path,
    force_rebuild: bool,
    printer: "_Progress",
    started: float,
) -> bool:
    meta = service.store.get_metadata_map() if service.store else {}
    estimated = int(meta.get("file_count") or 0)
    if not force_rebuild and service.can_reuse_index_for(root):
        updated = meta.get("updated_at")
        from .core.timefmt import iso_time

        printer.line(
            f"index: reused {estimated} files (built {iso_time(updated)}); pass --rebuild to force a rescan"
        )
        return True

    stop = threading.Event()
    reporter = threading.Thread(
        target=_poll_index_progress,
        args=(service, printer, stop, estimated, started),
        name="auto-index-cli-progress",
        daemon=True,
    )
    reporter.start()
    try:
        result = service.rebuild_sync()
    finally:
        stop.set()
        reporter.join(timeout=2.0)
    if result.get("status") == "indexing-in-other-process":
        lock = result.get("build_lock") or {}
        holder = lock.get("holder_pid")
        printer.line(
            f"index: another process (pid {holder}) is already rebuilding this project; retry later"
        )
        return False
    printer.line(
        f"index: {result.get('file_count', 0)} files in {result.get('elapsed_seconds', 0)}s"
        f" -> {result.get('index_path', '')}"
    )
    errors = result.get("errors") or []
    for error in errors:
        printer.line(f"  warning: {error}")
    return True


def _build_embeddings(service: Any, printer: "_Progress") -> bool:
    # rebuild_sync dispatches the embedding pass to a background worker; the
    # fresh-reuse path has not, so make sure one is running, then wait on it.
    ensured = service.ensure_embedding_background()
    background = service.embedding_background
    if background is None:
        # Nothing was dispatched: vectors are already complete or semantic
        # support is off; ensure() already told us which.
        return _report_embedding_result(service, printer, ensured or {})
    while not background.wait(0.25):
        printer.tick_embedding()
    status = background.status()
    if status.get("state") == "error":
        printer.line(f"semantic vectors: FAILED - {status.get('error')}")
        return False
    return _report_embedding_result(service, printer, status.get("last_result") or {})


def _report_embedding_result(service: Any, printer: "_Progress", result: dict[str, Any]) -> bool:
    outcome = result.get("status") or result.get("state") or ""
    if outcome in ("embedding-unavailable", "embedding-disabled"):
        reason = result.get("error") or "no usable embedding model"
        printer.line(f"semantic vectors: skipped - {reason}")
        return True
    if outcome == "embedding-in-other-process":
        printer.line("semantic vectors: another process is embedding this project; vectors will appear when it finishes")
        return True
    vector_count = result.get("vector_count")
    if vector_count is None and service.embedding_indexer is not None:
        try:
            vector_count = service.embedding_indexer.count()
        except Exception:
            vector_count = None
    embedded = result.get("embedded", 0)
    reused = result.get("reused", 0)
    model = result.get("model") or "?"
    detail = f"{embedded} embedded, {reused} reused" if (embedded or reused) else "ready"
    total = f", {vector_count} total" if vector_count is not None else ""
    printer.line(f"semantic vectors: {detail}{total} (model {model})")
    return True


def _poll_index_progress(
    service: Any,
    printer: "_Progress",
    stop: threading.Event,
    estimated_total: int,
    started: float,
) -> None:
    while not stop.wait(0.2):
        count = service.tree_progress.count()
        elapsed = time.time() - started
        if estimated_total > 0:
            fraction = min(0.99, count / estimated_total)
            bar = _bar(fraction)
            printer.update(
                f"{bar} indexing {count}/~{estimated_total} files  {elapsed:.1f}s",
                fraction,
            )
        else:
            printer.update(f"indexing... {count} files  {elapsed:.1f}s")


# ---- status ---------------------------------------------------------------


def _run_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp status",
        description="Print a read-only summary of an existing index.",
    )
    parser.add_argument("path", nargs="?", default=".", help="project root (default: current directory)")
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    index_dir = root / ".auto-index-mcp"
    db_path = index_dir / "index.db"
    if not db_path.exists():
        print(f"no index at {db_path}")
        print(f"build one with: auto-index-mcp build {root}")
        return 1

    from .core.timefmt import iso_time
    from .workspace.discovery import read_index_metadata

    meta = read_index_metadata(db_path)
    if not meta:
        print(f"index at {db_path} is unreadable or empty")
        return 1
    file_count = int(meta.get("file_count") or 0)
    total_count = int(meta.get("total_file_count") or file_count)
    files_note = f"{file_count}"
    if total_count > file_count:
        files_note += f" (+{total_count - file_count} in child indexes)"
    print(f"root:        {meta.get('root', root)}")
    print(f"index:       {db_path}")
    print(f"files:       {files_note}")
    print(f"updated:     {iso_time(meta.get('updated_at'))}")
    print(f"version:     {meta.get('version')}")

    vectors, models = _read_vector_summary(index_dir / "embeddings.db")
    if vectors is None:
        print("vectors:     none (run build without --no-semantic)")
    else:
        model_note = f" (model {', '.join(models)})" if models else ""
        print(f"vectors:     {vectors}{model_note}")
    return 0


def _read_vector_summary(db_path: Path) -> tuple[int | None, list[str]]:
    if not db_path.exists():
        return None, []
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbol_embeddings").fetchone()[0]
            models = [
                str(row[0]).split("#", 1)[0]
                for row in conn.execute(
                    "SELECT DISTINCT model_name FROM symbol_embeddings"
                ).fetchall()
            ]
        return int(count), sorted(set(models))
    except sqlite3.DatabaseError:
        return None, []


# ---- progress rendering ----------------------------------------------------


def _bar(fraction: float) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * _BAR_WIDTH)
    return "[" + "#" * filled + "-" * (_BAR_WIDTH - filled) + "]"


class _Progress:
    """Console progress with graceful degradation.

    TTY: single self-overwriting line via carriage returns (plain ASCII, safe
    on Windows GBK consoles). Non-TTY (CI, redirects): milestone lines only,
    one per 10% step, so logs are not flooded. ``--quiet`` silences updates
    but keeps final summary lines.
    """

    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.tty = (not quiet) and sys.stdout.isatty()
        self._lock = threading.Lock()
        self._last_width = 0
        self._milestone = -1
        self._embed_state: tuple[int, int, int] | None = None
        self._embed_started: float | None = None

    def update(self, text: str, fraction: float | None = None) -> None:
        if self.quiet:
            return
        with self._lock:
            if self.tty:
                pad = max(0, self._last_width - len(text))
                sys.stdout.write("\r" + text + " " * pad)
                sys.stdout.flush()
                self._last_width = len(text)
                return
            if fraction is None:
                return
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

    # Called from the embedding worker thread per batch.
    def embedding_update(self, done: int, total: int, reused: int) -> None:
        if self._embed_started is None:
            self._embed_started = time.time()
        self._embed_state = (done, total, reused)
        self._render_embedding()

    # Called from the main thread while waiting, to keep the timer ticking.
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
            self.update(f"embedding: all vectors up to date (reused {reused})  {elapsed:.1f}s", 1.0)
            return
        fraction = done / total
        bar = _bar(fraction)
        self.update(
            f"{bar} embedding {done}/{total} vectors {fraction * 100:.0f}%  "
            f"{elapsed:.1f}s (reused {reused})",
            fraction,
        )


if __name__ == "__main__":
    raise SystemExit(main())
