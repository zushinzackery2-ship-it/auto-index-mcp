from __future__ import annotations

import re
import fnmatch
import shutil
import subprocess
from pathlib import Path


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
    if not regex:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    if file_pattern:
        command.extend(["--glob", file_pattern])
    command.extend(["--", pattern, str(root)])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode not in (0, 1):
        return None
    matches = []
    for line in completed.stdout.splitlines():
        parsed = _parse_rg_line(root, line)
        if parsed and parsed["path"] in allowed:
            matches.append(parsed)
        if len(matches) >= limit:
            break
    return matches


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
