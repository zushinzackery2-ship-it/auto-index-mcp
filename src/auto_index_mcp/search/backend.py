from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_RG_COMMAND_CHARS = 24_000
# Maximum number of files to cache for Python fallback search
_MAX_FILE_CACHE_SIZE = 1000

# LRU cache for file content reads during Python fallback
_FILE_CONTENT_CACHE: dict[str, tuple[int, list[str]]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_ACCESS_ORDER: list[str] = []


@dataclass(frozen=True)
class RipgrepResult:
    status: str
    matches: list[dict]


def search_text(
    root: Path,
    files: list[dict],
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None = None,
) -> tuple[str, list[dict]]:
    if shutil.which("rg"):
        result = _ripgrep(root, pattern, case_sensitive, regex, limit, file_pattern, files)
        if result.status == "ok":
            return "ripgrep-indexed-files", result.matches
        if result.status != "unavailable":
            return f"ripgrep-{result.status}", result.matches
    return "indexed-files", _python_search(root, files, pattern, case_sensitive, regex, limit, file_pattern)


def _ripgrep(
    root: Path,
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None,
    files: list[dict],
) -> RipgrepResult:
    search_targets = _indexed_search_targets(root, files, file_pattern)
    if not search_targets:
        return RipgrepResult("ok", [])
    matches = []
    for batch in _target_batches(_base_rg_command(pattern, case_sensitive, regex), search_targets):
        result = _ripgrep_batch(root, pattern, case_sensitive, regex, max(1, limit) - len(matches), batch)
        matches.extend(result.matches)
        if result.status != "ok":
            return RipgrepResult(result.status, matches[:max(1, limit)])
        if len(matches) >= max(1, limit):
            return RipgrepResult("ok", matches[:max(1, limit)])
    return RipgrepResult("ok", matches)


def _ripgrep_batch(
    root: Path,
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    targets: list[tuple[Path, str]],
) -> RipgrepResult:
    command = _base_rg_command(pattern, case_sensitive, regex)
    command.extend(str(target) for target, _display_path in targets)
    path_map = _path_map(targets)
    if not path_map:
        return RipgrepResult("ok", [])
    process = None
    timeout_timer = None
    timed_out = threading.Event()
    stopped_after_limit = False
    matches = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        timeout_timer = _terminate_after(process, 30.0, timed_out)
        if not process.stdout:
            return RipgrepResult("unavailable", [])
        for line in process.stdout:
            parsed = _parse_rg_json_line(root, line.rstrip("\r\n"), path_map)
            if parsed:
                matches.append(parsed)
            if len(matches) >= max(1, limit):
                stopped_after_limit = True
                process.terminate()
                break
        return_code = process.wait(timeout=1.0)
    except OSError:
        if process is not None:
            process.kill()
            process.wait(timeout=1.0)
        return RipgrepResult("unavailable", matches)
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            process.wait(timeout=1.0)
        return RipgrepResult("timeout", matches)
    finally:
        if timeout_timer is not None:
            timeout_timer.cancel()
        if process is not None and process.stdout:
            stdout_close = getattr(process.stdout, "close", None)
            if stdout_close:
                stdout_close()
    if stopped_after_limit:
        return RipgrepResult("ok", matches)
    if timed_out.is_set():
        return RipgrepResult("timeout", matches)
    if return_code not in (0, 1):
        return RipgrepResult("error", matches)
    return RipgrepResult("ok", matches)


def _base_rg_command(pattern: str, case_sensitive: bool, regex: bool) -> list[str]:
    command = ["rg", "--json", "--line-number", "--with-filename", "--no-heading", "--color", "never"]
    command.extend(["--path-separator", "/", "--no-ignore", "--hidden"])
    if not regex:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    command.extend(["--", pattern])
    return command


def _terminate_after(process: subprocess.Popen, timeout_seconds: float, timed_out: threading.Event) -> threading.Timer:
    def terminate() -> None:
        if process.poll() is None:
            timed_out.set()
            process.terminate()

    timer = threading.Timer(timeout_seconds, terminate)
    timer.daemon = True
    timer.start()
    return timer


def _indexed_search_targets(root: Path, files: list[dict], file_pattern: str | None) -> list[tuple[Path, str]]:
    targets = []
    for item in files:
        if file_pattern and not _matches_file_pattern(item["path"], file_pattern):
            continue
        target = _source_path(root, item).resolve()
        targets.append((target, item["path"]))
    return targets


def _matches_file_pattern(path: str, file_pattern: str) -> bool:
    return fnmatch.fnmatch(path, file_pattern) or fnmatch.fnmatch(Path(path).name, file_pattern)


def _target_batches(base_command: list[str], targets: list[tuple[Path, str]]) -> list[list[tuple[Path, str]]]:
    batches: list[list[tuple[Path, str]]] = []
    current: list[tuple[Path, str]] = []
    # Use UTF-8 encoded bytes length for accurate shell argument size calculation
    base_size = sum(len(part.encode("utf-8")) + 3 for part in base_command)
    current_size = base_size
    for target, _display_path in targets:
        rendered = str(target)
        # Calculate actual bytes needed: quotes + UTF-8 encoded path + quotes + space
        path_bytes = len(rendered.encode("utf-8")) + 3
        if current and current_size + path_bytes > MAX_RG_COMMAND_CHARS:
            batches.append(current)
            current = []
            current_size = base_size
        current.append((target, _display_path))
        current_size += path_bytes
    if current:
        batches.append(current)
    return batches


def _python_search(
    root: Path,
    files: list[dict],
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None,
) -> list[dict]:
    flags = 0 if case_sensitive else re.IGNORECASE
    compiled = re.compile(pattern if regex else re.escape(pattern), flags)
    matches = []
    for item in files:
        if file_pattern and not _matches_file_pattern(item["path"], file_pattern):
            continue
        try:
            lines = _cached_read_lines(root, item)
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if compiled.search(line):
                matches.append({"path": item["path"], "line": line_number, "text": line.strip()})
                if len(matches) >= limit:
                    return matches
    return matches


def _cached_read_lines(root: Path, item: dict[str, Any]) -> list[str]:
    """Read file content with LRU cache to avoid redundant disk I/O."""
    source_path = _source_path(root, item)
    key = str(source_path.resolve())

    # Check cache: (mtime_ns, content_lines)
    with _CACHE_LOCK:
        cached = _FILE_CONTENT_CACHE.get(key)
        if cached is not None:
            try:
                stat = source_path.stat()
                if stat.st_mtime_ns == cached[0]:
                    # Move to end (most recently used)
                    if key in _CACHE_ACCESS_ORDER:
                        _CACHE_ACCESS_ORDER.remove(key)
                    _CACHE_ACCESS_ORDER.append(key)
                    return cached[1]
            except OSError:
                pass

    # Read and cache
    try:
        stat = source_path.stat()
        content = source_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        with _CACHE_LOCK:
            # Evict oldest entries if cache is full
            while len(_FILE_CONTENT_CACHE) >= _MAX_FILE_CACHE_SIZE:
                oldest = _CACHE_ACCESS_ORDER.pop(0)
                _FILE_CONTENT_CACHE.pop(oldest, None)
            _FILE_CONTENT_CACHE[key] = (stat.st_mtime_ns, lines)
            _CACHE_ACCESS_ORDER.append(key)
        return lines
    except (OSError, UnicodeDecodeError):
        return []


def _source_path(root: Path, item: dict) -> Path:
    source_root = Path(item.get("source_root") or root)
    return source_root / item.get("source_path", item["path"])


def _path_map(targets: list[tuple[Path, str]]) -> dict[str, str]:
    mapping = {}
    for target, display_path in targets:
        try:
            mapping[str(target.resolve())] = display_path
        except OSError:
            continue
    return mapping


def _parse_rg_json_line(root: Path, line: str, path_map: dict[str, str] | None = None) -> dict | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if payload.get("type") != "match":
        return None
    data = payload.get("data") or {}
    path_text = (data.get("path") or {}).get("text")
    line_text = (data.get("lines") or {}).get("text", "")
    line_number_value = data.get("line_number")
    if path_text is None or line_number_value is None:
        return None
    try:
        line_number = int(line_number_value)
        source_path = Path(path_text).resolve()
        path = (path_map or {}).get(str(source_path))
        if path is None:
            path = source_path.relative_to(root).as_posix()
    except (TypeError, ValueError, OSError):
        return None
    return {"path": path, "line": line_number, "text": line_text.strip()}
