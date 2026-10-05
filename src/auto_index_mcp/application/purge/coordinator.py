"""Retire a bound workspace only after every selected cache is exclusive."""
from contextlib import ExitStack
from pathlib import Path
import threading
import time
import logging

from ...cache.identity import directory_identity, identity_key
from ...cache.inventory import file_inventory
from ...domain.errors import ServiceError
from ...registry.adoption import adopt_directory
from ...registry.cleanup import clean_entry_locked
from ...registry.store import IndexRegistry
from ...registry.paths import registry_key
from ...runtime.control.contracts import check_deadline, within
from ...runtime.control.journal import OperationJournal, prune_reports
from ...runtime.budget import deadline_scope
from ...runtime.maintenance import MaintenanceLease
from .cooperation import wait_for_members
from .discovery import discover, participants, validate
from .response import build_response, compact


class PurgeCoordinator:
    def __init__(self, project) -> None:
        self.project = project
        self._lock = threading.Lock()

    def run(self, dry_run=False, timeout_seconds=30.0):
        if self.project.root_path is None:
            raise ServiceError("workspace-not-bound", "purge requires a bound workspace",
                               "call auto_index_enable(root_path=...) to select the workspace")
        if not 1.0 <= timeout_seconds <= 120.0:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if not self._lock.acquire(blocking=False):
            raise ServiceError("busy", "this instance is already purging its workspace")
        try:
            started = time.monotonic()
            with deadline_scope(started + timeout_seconds):
                return self._run(dry_run, started, started + timeout_seconds)
        finally:
            self._lock.release()

    def _run(self, dry_run, started, deadline):
        root = self.project.root_path.resolve()
        binding = self.project.binding_generation
        root_identity = directory_identity(root)
        store = self.project.control.store
        targets, members = [], []
        try:
            members = participants(store, root)
            targets = discover(self.project, root, deadline, members)
            self._check_binding(root, binding, root_identity)
        except (OSError, ServiceError, TimeoutError) as exc:
            return self._failure(root, started, targets, exc)
        if dry_run:
            preview = build_response(root, "preview", self.project.enabled, started, targets)
            preview.update(complete=True, targets=targets[:50], participants=members[:50],
                           planned_bytes=sum(row["bytes"] for row in targets),
                           details_truncated=len(targets) > 50 or len(members) > 50)
            return preview
        try:
            record, owner = store.begin(root, deadline, members)
        except (OSError, ServiceError, TimeoutError) as exc:
            return self._failure(root, started, targets, exc)
        journal = None
        results, blockers, acknowledgements = [], [], []
        status = "interrupted"
        try:
            journal = OperationJournal(store, record)
            journal.save("freeze", participants=members)
            journal.save("notify", instances=len(members))
            acknowledgements = wait_for_members(store, record, members)
            journal.save("drain", stopped_instances=len(acknowledgements))
            self._check_binding(root, binding, root_identity)
            reported = [dict(ack, indexes=ack.get("indexes", [])) for ack in acknowledgements]
            targets = discover(self.project, root, deadline, reported)
            targets = self._merge_targets(targets, journal.targets)
            journal.targets = targets
            self._delete(store, record, journal, root, binding, root_identity, deadline, targets, results)
            status = "purged"
            if any(row["status"] not in ("removed", "unregistered") for row in results):
                status = "partial"
            elif not any(row.get("deleted_bytes") for row in results):
                status = "already-purged"
        except (OSError, ServiceError, TimeoutError, ValueError) as exc:
            status = ("partial" if record["phase"] == "deleting" else
                      "refused" if isinstance(exc, ServiceError) and exc.code not in ("busy", "stop-timeout") else
                      "busy" if record["phase"] == "leasing" else "stop-timeout")
            if isinstance(exc, ServiceError):
                acknowledgements = exc.details.get("stopped", acknowledgements)
            blockers.append(dict(error=str(exc), phase=record["phase"],
                                 details=exc.details if isinstance(exc, ServiceError) else dict()))
        finally:
            try:
                result = build_response(root, status, self.project.enabled, started, targets, results, blockers, record)
                result["stopped_instances"] = len(acknowledgements)
                result["participants"] = [dict(instance_id=ack["instance_id"], pid=ack["pid"], root=ack["root"],
                                               status=ack["status"], role="workspace" if within(ack["root"], root)
                                               else "external-mounter") for ack in acknowledgements]
                if status not in ("purged", "already-purged"):
                    result.update(error=status, message=blockers[0]["error"] if blockers else "cache residue retained",
                                  hint="inspect the purge report, resolve blockers, then call manage(action='purge') again")
                if journal is not None:
                    journal.save("final", status=status)
                    result["report_path"] = str(journal.directory / "report.json")
                    journal.report(result)
                store.update(record, phase=status, summary=compact(result))
                self.project.last_purge = compact(result)
            finally:
                owner.release()
        try:
            prune_reports(store)
        except OSError:
            logging.getLogger(__name__).exception("purge report retention failed")
        return compact(result)

    def _delete(self, store, record, journal, root, binding, identity, deadline, targets, results):
        store.update(record, phase="leasing")
        record["phase"] = "leasing"
        journal.save("lease")
        with ExitStack() as leases:
            for target in targets:
                check_deadline(deadline)
                leases.enter_context(MaintenanceLease(Path(target["index_dir"]), "purge",
                    coordination_directory=Path(target["coordination_dir"]), deadline=deadline))
            self._check_binding(root, binding, identity)
            for target in targets:
                validate(target, root)
            store.update(record, phase="deleting")
            record["phase"] = "deleting"
            for target in sorted(targets, key=lambda row: len(Path(row["root"]).parts), reverse=True):
                check_deadline(deadline)
                self._check_binding(root, binding, identity)
                journal.save("delete", index_dir=target["index_dir"])
                row = self._clean(target)
                results.append(row)
                journal.results = results
                journal.save("registry", result=row)

    def _check_binding(self, root, binding, identity):
        if (self.project.root_path != root or self.project.binding_generation != binding
                or identity != directory_identity(root)):
            raise ServiceError("workspace-binding-changed", "bound workspace changed during purge")

    def _failure(self, root, started, targets, exc):
        status = "busy" if isinstance(exc, ServiceError) and exc.code == "busy" else "refused"
        result = build_response(root, status, self.project.enabled, started, targets,
                                blockers=[dict(error=str(exc))])
        result.update(error=exc.code if isinstance(exc, ServiceError) else status, message=str(exc),
                      hint="inspect the blocker and retry purge after it is resolved")
        if isinstance(exc, ServiceError):
            result.update(exc.details)
        return result

    def _merge_targets(self, targets, previous):
        merged = dict((row["index_dir"], row) for row in previous)
        for row in targets:
            old = merged.get(row["index_dir"])
            if old and row["identity"] is None:
                row = dict(row, coordination_dir=old["coordination_dir"])
            merged[row["index_dir"]] = row
        return sorted(merged.values(), key=lambda row: row["coordination_dir"])

    def _clean(self, target):
        directory, root = Path(target["index_dir"]), Path(target["root"])
        results = []
        before = file_inventory(directory)["bytes"]
        try:
            for location in target["registries"]:
                registry = IndexRegistry(Path(location))
                entry = registry.strict_entries().get(registry_key(directory))
                if directory.exists():
                    if (entry is None or registry_key(entry["root"]) != registry_key(root)
                            or entry.get("index_id") != identity_key(directory)
                            or entry.get("root_identity") != target["root_identity"]
                            or registry_key(target["owned_root"]) != registry_key(root)):
                        adopt_directory(registry, directory, root, lease_held=True)
                        entry = registry.get(directory)
                    if entry is None:
                        raise ServiceError("registry-unavailable", f"cache registration failed: {directory}")
                    results.append(clean_entry_locked(registry, entry))
                elif entry is not None:
                    results.append(dict(status="unregistered" if registry.unregister(directory) else "changed", notes=[]))
        except (OSError, ServiceError, ValueError) as exc:
            results.append(dict(status="failed", notes=[str(exc)]))
        status = next((row["status"] for row in results if row["status"] not in ("removed", "unregistered")),
                      "removed" if directory.exists() else "unregistered")
        return dict(root=str(root), index_dir=str(directory), status=status,
                    deleted_bytes=before - file_inventory(directory)["bytes"],
                    error="; ".join(note for row in results for note in row.get("notes", [])) if status in ("failed", "changed", "refused") else None,
                    retained=[note for row in results for note in row.get("notes", [])])
