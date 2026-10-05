"""An explicit activation acknowledges suspension only for one instance."""
from ...domain.errors import ServiceError
from .contracts import ACTIVE_PHASES, ActivationIntent


class Admission:
    def __init__(self, store) -> None:
        self.store = store
        self.permits = dict()

    def check(self, root, intent=ActivationIntent.AUTOMATIC) -> None:
        if root is None:
            return
        records = self.store.suspensions_for(root)
        for record in records:
            if record["phase"] in ACTIVE_PHASES and self.store.owner_alive(record):
                raise ServiceError("busy", "workspace purge is stopping or deleting indexes",
                                   operation_id=record["operation_id"], phase=record["phase"])
            if (intent is not ActivationIntent.EXPLICIT
                    and self.permits.get(record["operation_id"]) != record["generation"]):
                raise ServiceError("workspace-suspended", "workspace indexing was suspended by purge",
                                   "call auto_index_enable() explicitly to resume this instance",
                                   operation_id=record["operation_id"], phase=record["phase"])
        if intent is ActivationIntent.EXPLICIT:
            for record in records:
                self.permits[record["operation_id"]] = record["generation"]

    def pending(self, root):
        records = self.store.suspensions_for(root) if root is not None else []
        outstanding = [row for row in records if self.permits.get(row["operation_id"]) != row["generation"]]
        return max(outstanding, key=lambda row: row["generation"], default=None)
