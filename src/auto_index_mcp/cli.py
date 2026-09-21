"""Command-line entry point.

Subcommands:
    build [path]    build (or refresh) the persistent index synchronously,
                    including semantic vectors, then exit. Pre-building means
                    a later MCP session attaches instantly instead of paying
                    the first-build wait.
    status [path]   read-only summary of an existing index, no server started.
    list            print every index recorded in the user-level registry.
    clean ...       delete registered index directories (orphans/ephemeral by
                    default); --scan adopts pre-existing indexes into the
                    registry.
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

from .commands.progress import Progress as _Progress, bar as _bar

_BAR_WIDTH = 20


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:]) if argv is None else list(argv)
    if args and args[0] == "build":
        return _run_build(args[1:])
    if args and args[0] == "status":
        return _run_status(args[1:])
    if args and args[0] == "list":
        return _run_list(args[1:])
    if args and args[0] == "clean":
        return _run_clean(args[1:])
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
        uri = db_path.resolve().as_uri() + "?mode=ro"
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


# ---- list -------------------------------------------------------------------


def _run_list(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp list",
        description="List every index recorded in the user-level registry.",
    )
    parser.parse_args(argv)

    from .core.timefmt import iso_time
    from .registry import IndexRegistry, index_dir_size

    registry = IndexRegistry()
    entries = registry.verify()
    print(f"registry: {registry.path()}")
    if not entries:
        print("no registered indexes")
        return 0
    for entry in entries:
        flags = []
        if entry["orphan"]:
            flags.append("orphan")
        if entry.get("ephemeral"):
            flags.append("ephemeral")
        size = index_dir_size(entry["index_dir"]) if entry["index_exists"] else 0
        note = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{entry['root']}{note}")
        print(f"    index:         {entry['index_dir']}  ({_human_size(size)})")
        print(f"    last attached: {iso_time(entry.get('last_attached_at'))}"
              f"  source: {entry.get('source', '?')}")
    return 0


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{int(size)} B"


# ---- clean ------------------------------------------------------------------


def _run_clean(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="auto-index-mcp clean",
        description=(
            "Delete index directories recorded in the registry. With no "
            "selector this cleans orphaned and ephemeral entries only."
        ),
    )
    parser.add_argument("targets", nargs="*", help="project roots whose indexes should be removed")
    parser.add_argument("--all", action="store_true", help="select every registered index")
    parser.add_argument("--orphans", action="store_true", help="select entries whose project or index is gone")
    parser.add_argument("--ephemeral", action="store_true", help="select entries built at an overridden index location")
    parser.add_argument("--older-than", default=None, metavar="AGE",
                        help="only entries not attached for AGE (e.g. 30d, 12h; bare number = days)")
    parser.add_argument("--scan", default=None, metavar="DIR",
                        help="scan DIR for existing indexes and adopt them into the registry (no deletion)")
    parser.add_argument("--dry-run", action="store_true", help="print what would happen without deleting")
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    from .registry import IndexRegistry

    registry = IndexRegistry()
    if args.scan is not None:
        return _clean_scan(registry, args.scan)

    try:
        max_age = _parse_age(args.older_than) if args.older_than else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    candidates = _clean_candidates(registry, args, max_age)
    if not candidates:
        print("nothing to clean")
        return 0
    for candidate in candidates:
        marker = " (index already gone; registry entry only)" if not candidate["index_exists"] else ""
        print(f"clean: {candidate['root']} -> {candidate['index_dir']}{marker}")
    if args.dry_run:
        print(f"dry run: {len(candidates)} entries selected, nothing deleted")
        return 0
    if not args.yes and not _confirm(f"delete {len(candidates)} index directories? [y/N] "):
        print("aborted")
        return 1
    return _clean_apply(registry, candidates)


def _clean_scan(registry: Any, base: str) -> int:
    base_path = Path(base).resolve()
    if not base_path.is_dir():
        print(f"error: not a directory: {base_path}", file=sys.stderr)
        return 1
    adopted = registry.scan_and_adopt(base_path)
    for entry in adopted:
        print(f"adopted: {entry['root']} -> {entry['index_dir']}")
    print(f"scan: {len(adopted)} new indexes registered under {base_path}")
    return 0


def _clean_candidates(registry: Any, args: Any, max_age: float | None) -> list[dict[str, Any]]:
    from .registry import registry_key

    entries = registry.verify()
    target_keys = {registry_key(Path(target)) for target in args.targets}
    use_default = not (args.targets or args.all or args.orphans or args.ephemeral)
    # An explicit --older-than alone means "everything old enough", not the
    # orphans+ephemeral safety default.
    if use_default and max_age is not None:
        selected = entries
    elif use_default:
        selected = [entry for entry in entries if entry["orphan"] or entry.get("ephemeral")]
    else:
        selected = [
            entry for entry in entries
            if args.all
            or entry["key"] in target_keys
            or (args.orphans and entry["orphan"])
            or (args.ephemeral and entry.get("ephemeral"))
        ]
    if max_age is not None:
        cutoff = time.time() - max_age
        selected = [
            entry for entry in selected
            if float(entry.get("last_attached_at") or 0.0) < cutoff
        ]
    return selected


def _clean_apply(registry: Any, candidates: list[dict[str, Any]]) -> int:
    from .registry import build_lock_active, is_safe_to_delete, remove_index_dir

    failures = 0
    for entry in candidates:
        index_dir = Path(entry["index_dir"])
        if not entry["index_exists"]:
            registry.unregister(entry["root"])
            print(f"unregistered: {entry['root']} (index was already gone)")
            continue
        if build_lock_active(index_dir):
            print(f"skipped: {entry['root']} (a build is currently running there)")
            continue
        if not is_safe_to_delete(index_dir, entry["root"]):
            failures += 1
            print(
                f"refused: {index_dir} does not verify as an auto-index-mcp "
                f"index for {entry['root']}"
            )
            continue
        removed, notes = remove_index_dir(index_dir)
        registry.unregister(entry["root"])
        state = "removed" if removed else "cleaned (directory kept)"
        print(f"{state}: {index_dir}")
        for note in notes:
            print(f"    {note}")
    return 1 if failures else 0


def _parse_age(text: str) -> float:
    """Duration in seconds from '30d' / '12h' / '45m' / '90s' (bare = days)."""
    import re

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhdw]?)", text.strip().lower())
    if match is None:
        raise ValueError(f"invalid --older-than value: {text!r} (expected e.g. 30d, 12h)")
    value = float(match.group(1))
    unit = match.group(2) or "d"
    seconds = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}[unit]
    return value * seconds


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        print("refusing to delete without -y on a non-interactive terminal", file=sys.stderr)
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
