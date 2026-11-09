"""Named compatibility fields expose the owned context without duplicating state."""
from __future__ import annotations


class ContextField:
    def __init__(self, name: str, *, writable=False) -> None:
        self.name = name
        self.writable = writable

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance.project, self.name)

    def __set__(self, instance, value) -> None:
        if not self.writable:
            raise AttributeError(f"{self.name} is owned by the service lifecycle")
        setattr(instance.project, self.name, value)


class TaskField:
    """Compatibility injection still participates in cancellation and drain."""
    def __init__(self, operation):
        self.operation = operation

    def __get__(self, instance, owner=None):
        return self if instance is None else instance.project.tasks.latest(self.operation)

    def __set__(self, instance, worker):
        if not instance.project.tasks.register(self.operation, worker, start=False):
            raise RuntimeError("cannot register a task while the service is stopping")
