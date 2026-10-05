"""Stop acknowledgements are evidence; exclusive cache leases remain authority."""
import threading

from ...runtime.leases import BuildLock
from ...runtime.control.contracts import check_deadline, remaining
from ...domain.errors import ServiceError
from .discovery import participants
from ...runtime.files.json import read_json


def wait_for_members(store, record, members):
    pending = dict((row["instance_id"], row) for row in members)
    acknowledgements = []
    wake = threading.Event()
    directory = store.operation_path(record["operation_id"]) / "acks" / str(record["generation"])
    while True:
        for member in participants(store, record["root"]):
            if not any(row["instance_id"] == member["instance_id"] for row in acknowledgements):
                pending[member["instance_id"]] = member
        if not pending:
            break
        for identity, member in list(pending.items()):
            ack = directory / f"{identity}.json"
            if ack.exists():
                result = read_json(ack)
                result.update(root=member["root"], registry_dir=member["registry_dir"])
                acknowledgements.append(result)
                if result["status"] != "stopped":
                    raise ServiceError("stop-timeout", f"instance {identity} failed to stop: {result.get('error')}",
                                       stopped=acknowledgements, participants=list(pending.values()))
                del pending[identity]
            elif BuildLock(store.directory / "instances" / identity / "instance.lock",
                           resolve_cache=False).state_info() is None:
                acknowledgements.append(dict(instance_id=identity, pid=member["pid"], status="exited",
                                             root=member["root"], registry_dir=member["registry_dir"],
                                             indexes=member.get("indexes", [])))
                del pending[identity]
        if pending:
            try:
                check_deadline(record["deadline"])
            except TimeoutError as exc:
                raise ServiceError("stop-timeout", str(exc), stopped=acknowledgements,
                                   participants=list(pending.values())) from exc
            wake.wait(min(0.05, remaining(record["deadline"])))
    return acknowledgements
