"""Named compatibility fields expose the owned context without duplicating state."""
from __future__ import annotations


class ContextField:
    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return getattr(instance.project, self.name)

    def __set__(self, instance, value) -> None:
        setattr(instance.project, self.name, value)
