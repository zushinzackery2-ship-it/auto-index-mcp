"""Text search always runs in a terminable ripgrep process."""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from ..runtime.budget import ExecutionBudget
from ..runtime.dependencies import ripgrep_executable
from .rg_json import parse_match
from .targets import indexed_targets, target_batches

SEARCH_TIMEOUT_SECONDS = 30.0
MAX_DIAGNOSTIC_CHARS = 4096
MAX_JSON_LINE_CHARS = 2_000_000


@dataclass(frozen=True)
class RipgrepResult:
    status: str
    matches: list[dict]
    message: str = ""
    complete: bool = True

    @property
    def backend(self) -> str:
        return "ripgrep-indexed-files" if self.status == "ok" else f"ripgrep-{self.status}"


def search_text(
    root: Path, files: list[dict], pattern: str, case_sensitive: bool,
    regex: bool, limit: int, file_pattern: str | None = None,
) -> RipgrepResult:
    executable = ripgrep_executable()
    if executable is None:
        return RipgrepResult("unavailable", [], "ripgrep (rg) is required; install the project dependencies.", False)
    budget = ExecutionBudget.seconds(SEARCH_TIMEOUT_SECONDS)
    command = _base_rg_command(executable, pattern, case_sensitive, regex)
    matches: list[dict] = []
    try:
        budget.check()
        targets = indexed_targets(root, files, file_pattern)
        for batch in target_batches(command, targets):
            budget.check()
            result = _ripgrep_batch(root, command, max(1, limit) - len(matches), batch, budget)
            matches.extend(result.matches)
            if result.status != "ok" or not result.complete:
                return RipgrepResult(result.status, matches, result.message, False)
        return RipgrepResult("ok", matches)
    except TimeoutError as exc:
        return RipgrepResult("timeout", matches, str(exc), False)


def _base_rg_command(executable: str, pattern: str, case_sensitive: bool, regex: bool) -> list[str]:
    command = [executable, "--json", "--line-number", "--with-filename", "--color", "never"]
    command.extend(["--path-separator", "/", "--no-ignore", "--hidden", "--threads", "1"])
    if not regex:
        command.append("-F")
    if not case_sensitive:
        command.append("-i")
    command.extend(["--", pattern])
    return command


def _drain_diagnostics(stream, parts: list[str]) -> None:
    remaining = MAX_DIAGNOSTIC_CHARS
    while chunk := stream.read(1024):
        if remaining:
            parts.append(chunk[:remaining])
            remaining = max(0, remaining - len(chunk))


def _kill(process: subprocess.Popen, expired: threading.Event) -> None:
    if process.poll() is None:
        expired.set()
        try:
            process.kill()
        except OSError:
            pass


def _ripgrep_batch(root, command, limit, targets, budget) -> RipgrepResult:
    budget.check()
    matches: list[dict] = []
    diagnostics: list[str] = []
    expired = threading.Event()
    path_map = dict((str(target), path) for target, path in targets)
    try:
        process = subprocess.Popen(
            command + [str(target) for target, _ in targets],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return RipgrepResult("unavailable", [], str(exc), False)
    reader = threading.Thread(target=_drain_diagnostics, args=(process.stderr, diagnostics), daemon=True)
    timer = threading.Timer(budget.remaining, _kill, args=(process, expired))
    timer.daemon = True
    stopped, oversized = False, False
    try:
        reader.start()
        timer.start()
        while line := process.stdout.readline(MAX_JSON_LINE_CHARS + 1):
            if len(line) > MAX_JSON_LINE_CHARS:
                oversized = True
                break
            match = parse_match(root, line, path_map)
            if match:
                matches.append(match)
            if len(matches) >= limit:
                stopped = True
                break
        if stopped or oversized:
            process.kill()
        return_code = process.wait(timeout=max(0.1, budget.remaining))
    except subprocess.TimeoutExpired:
        expired.set()
        return_code = -1
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join()
        process.stdout.close()
        process.stderr.close()
    message = "".join(diagnostics).strip()
    if expired.is_set():
        return RipgrepResult("timeout", matches, "search deadline exceeded", False)
    if oversized:
        return RipgrepResult("response_limit", matches, "matched source line exceeds the decoding budget", False)
    if stopped:
        return RipgrepResult("ok", matches, "result limit reached", False)
    if return_code not in (0, 1):
        status = "invalid_pattern" if "regex" in message.lower() else "io_error"
        return RipgrepResult(status, matches, message or f"ripgrep exited with code {return_code}", False)
    return RipgrepResult("ok", matches)
