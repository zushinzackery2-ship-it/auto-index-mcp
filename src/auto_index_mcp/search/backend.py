from __future__ import annotations

import re
import fnmatch
import shutil
import subprocess
import threading
from pathlib import Path

MAX_RG_COMMAND_CHARS = 24_000


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
        if result is not None:
            return "ripgrep-indexed-files", result
    return "indexed-files", _python_search(root, files, pattern, case_sensitive, regex, limit, file_pattern)


def _ripgrep(
    root: Path,
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None,
    files: list[dict],
) -> list[dict] | None:
    search_targets = _indexed_search_targets(root, files, file_pattern)
    if not search_targets:
        return []
    matches = []
    for batch in _target_batches(_base_rg_command(pattern, case_sensitive, regex), search_targets):
        result = _ripgrep_batch(root, pattern, case_sensitive, regex, max(1, limit) - len(matches), batch)
        if result is None:
            return None
        matches.extend(result)
        if len(matches) >= max(1, limit):
            return matches[:max(1, limit)]
    return matches


def _ripgrep_batch(
    root: Path,
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    targets: list[tuple[Path, str]],
) -> list[dict] | None:
    command = _base_rg_command(pattern, case_sensitive, regex)
    command.extend(str(target) for target, _display_path in targets)
    path_map = _path_map(targets)
    if not path_map:
        return []
    process = None
    timed_out = None
    stopped_after_limit = False
    matches = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        timed_out = _terminate_after(process, 30.0)
        if not process.stdout:
            return None
        for line in process.stdout:
            parsed = _parse_rg_line(root, line.rstrip("\r\n"), path_map)
            if parsed:
                matches.append(parsed)
            if len(matches) >= max(1, limit):
                stopped_after_limit = True
                process.terminate()
                break
        return_code = process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            process.kill()
            process.wait(timeout=1.0)
        return None
    finally:
        if timed_out is not None:
            timed_out.cancel()
        if process is not None and process.stdout:
            stdout_close = getattr(process.stdout, "close", None)
            if stdout_close:
                stdout_close()
    if stopped_after_limit:
        return matches
    if return_code not in (0, 1):
        return None
    return matches


def _base_rg_command(pattern: str, case_sensitive: bool, regex: bool) -> list[str]:
    command = ["rg", "--line-number", "--with-filename", "--no-heading", "--color", "never"]
    command.extend(["--path-separator", "/", "--no-ignore", "--hidden"])
    if not regex:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    command.extend(["--", pattern])
    return command


def _terminate_after(process: subprocess.Popen, timeout_seconds: float) -> threading.Timer:
    def terminate() -> None:
        if process.poll() is None:
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
    command_size = sum(len(part) + 3 for part in base_command)
    current_size = command_size
    for target in targets:
        rendered = str(target[0])
        path_size = len(rendered) + 3
        if current and current_size + path_size > MAX_RG_COMMAND_CHARS:
            batches.append(current)
            current = []
            current_size = command_size
        current.append(target)
        current_size += path_size
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
            lines = _source_path(root, item).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, start=1):
            if compiled.search(line):
                matches.append({"path": item["path"], "line": line_number, "text": line.strip()})
                if len(matches) >= limit:
                    return matches
    return matches


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


def _parse_rg_line(root: Path, line: str, path_map: dict[str, str] | None = None) -> dict | None:
    match = re.match(r"^(.*):(\d+):(.*)$", line)
    if not match:
        return None
    try:
        line_number = int(match.group(2))
        source_path = Path(match.group(1)).resolve()
        path = (path_map or {}).get(str(source_path))
        if path is None:
            path = source_path.relative_to(root).as_posix()
    except (ValueError, OSError):
        return None
    return {"path": path, "line": line_number, "text": match.group(3).strip()}
