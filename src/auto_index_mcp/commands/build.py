from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import threading
from .progress import Progress as _Progress, bar as _bar


def run(argv: list[str]) -> int:
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

    from ..core.service import AutoIndexService

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
        service.enable(
            str(root),
            rebuild=False,
            refresh_embedder=not args.no_semantic,
            source="cli-build",
        )
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
        service.disable()


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
        result = service.sync_index_to_filesystem()
        if result.get("status") == "indexing-in-other-process":
            printer.line("index: another process is rebuilding; synchronization incomplete")
            return False
        count = service.store.get_metadata_map().get("file_count", 0)
        printer.line(f"index: reused {count} files; filesystem synchronized ({result.get('rewritten', 0)} rewritten)")
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
    return not errors and result.get("status") not in ("cancelled", "failed")


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
        printer.line("semantic vectors: waiting for another process; build is not complete")
        return False
    if outcome in ("embedding-cancelled", "embedding-waiting-for-index", "failed", "cancelled"):
        printer.line(f"semantic vectors: incomplete ({outcome})")
        return False
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
