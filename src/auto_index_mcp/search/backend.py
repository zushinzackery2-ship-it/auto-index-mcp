from __future__ import annotations

import re
import fnmatch
import shutil
import subprocess
import threading
from pathlib import Path

from ..core.config import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_FILE_PATTERNS


def search_text(
    root: Path,
    files: list[dict],
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None = None,
) -> tuple[str, list[dict]]:
    if any("source_root" in item for item in files):
        return "indexed-files", _python_search(root, files, pattern, case_sensitive, regex, limit, file_pattern)
    allowed = {item["path"] for item in files}
    if shutil.which("rg"):
        result = _ripgrep(root, pattern, case_sensitive, regex, limit, file_pattern, allowed)
        if result is not None:
            return "ripgrep", result
    return "indexed-files", _python_search(root, files, pattern, case_sensitive, regex, limit, file_pattern)


def _ripgrep(
    root: Path,
    pattern: str,
    case_sensitive: bool,
    regex: bool,
    limit: int,
    file_pattern: str | None,
    allowed: set[str],
) -> list[dict] | None:
    command = ["rg", "--line-number", "--with-filename", "--no-heading", "--color", "never"]
    command.extend(["--no-ignore", "--hidden"])
    for glob in _exclude_globs():
        command.extend(["--glob", glob])
    if not regex:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    if file_pattern:
        command.extend(["--glob", file_pattern])
    command.extend(["--", pattern, str(root)])
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return None
    matches = []
    stopped_after_limit = False
    timed_out = _terminate_after(process, 30.0)
    try:
        if not process.stdout:
            return None
        for line in process.stdout:
            parsed = _parse_rg_line(root, line.rstrip("\r\n"))
            if parsed and parsed["path"] in allowed:
                matches.append(parsed)
            if len(matches) >= max(1, limit):
                stopped_after_limit = True
                process.terminate()
                break
        return_code = process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=1.0)
        return None
    finally:
        timed_out.cancel()
        stdout_close = getattr(process.stdout, "close", None)
        if stdout_close:
            stdout_close()
    if stopped_after_limit:
        return matches
    if return_code not in (0, 1):
        return None
    return matches


def _terminate_after(process: subprocess.Popen, timeout_seconds: float) -> threading.Timer:
    def terminate() -> None:
        if process.poll() is None:
            process.terminate()

    timer = threading.Timer(timeout_seconds, terminate)
    timer.daemon = True
    timer.start()
    return timer


def _exclude_globs() -> list[str]:
    globs = []
    for directory in sorted(DEFAULT_EXCLUDE_DIRS):
        globs.append(f"!**/{directory}/**")
    for pattern in sorted(DEFAULT_EXCLUDE_FILE_PATTERNS):
        globs.append(f"!{pattern}")
        globs.append(f"!**/{pattern}")
    return globs


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
        if file_pattern and not (fnmatch.fnmatch(item["path"], file_pattern) or fnmatch.fnmatch(Path(item["path"]).name, file_pattern)):
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


def _parse_rg_line(root: Path, line: str) -> dict | None:
    match = re.match(r"^(.*):(\d+):(.*)$", line)
    if not match:
        return None
    try:
        line_number = int(match.group(2))
        path = Path(match.group(1)).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None
    return {"path": path, "line": line_number, "text": match.group(3).strip()}
