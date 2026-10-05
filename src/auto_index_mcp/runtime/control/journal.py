"""Durable operation evidence, including interrupted deletions and reports."""
import json
import os
from pathlib import Path
import time

from ..files.json import atomic_json, read_json
from .reports import report_page


def last_summary(store, root):
    records = store.suspensions_for(root) if root is not None else []
    record = max(records, key=lambda row: row["generation"], default=None)
    if record is None:
        return None
    path = store.operation_path(record["operation_id"]) / "report.json"
    if not path.exists():
        return record.get("summary") or dict(operation_id=record["operation_id"], status=record["phase"], complete=False)
    result = read_json(path)
    return dict((key, value) for key, value in result.items() if key not in ("results", "blockers", "participants"))


class OperationJournal:
    def __init__(self, store, record) -> None:
        self.directory = store.operation_path(record["operation_id"])
        self.record = record
        self.targets = []
        self.results = []
        path = self.directory / "journal.json"
        if path.exists():
            previous = read_json(path)
            self.targets = previous.get("targets", [])
        self.events = []

    def save(self, phase, **details) -> None:
        event = dict(phase=phase, time=time.time(), **details)
        self.events.append(event)
        # Persist the complete manifest at checkpoints. Per-target evidence
        # is appended durably, avoiding quadratic rewrites during deletion.
        if phase in ("freeze", "lease", "final"):
            atomic_json(self.directory / "journal.json", dict(record=self.record, targets=self.targets,
                        results=self.results, events=self.events))
        path = self.directory / "operation.log"
        if path.exists() and path.stat().st_size >= 512 * 1024:
            path.replace(self.directory / "operation.log.1")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def report(self, result) -> str:
        path = self.directory / "report.json"
        atomic_json(path, result)
        return str(path)


def read_report(store, root, operation_id, page: int):
    if page < 0:
        raise ValueError("page must be non-negative")
    directory = store.operation_path(operation_id)
    result = read_json(directory / "report.json")
    if root is None or Path(result["root"]).resolve() != Path(root).resolve():
        raise ValueError("report belongs to another bound workspace")
    return report_page(result, operation_id, page)


def prune_reports(store) -> None:
    scopes = store.snapshot()["scopes"]
    paths = []
    for directory in (store.directory / "operations").iterdir():
        record = scopes.get(directory.name)
        report = directory / "report.json"
        terminal = record["phase"] if record else (
            read_json(report).get("status") if report.exists() else None)
        if terminal in ("purged", "already-purged") and report.exists():
            paths.append(directory)
    for directory in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)[32:]:
        for name in ("report.json", "journal.json", "operation.log", "operation.log.1"):
            (directory / name).unlink(missing_ok=True)
