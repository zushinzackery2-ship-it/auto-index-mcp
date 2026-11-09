from __future__ import annotations

from pathlib import Path


class NativeEventPolicy:
    """Admit notifications before the Windows emitter probes their paths."""

    def __init__(self, root, should_ignore, removed, created):
        self.root = Path(root)
        self.should_ignore = should_ignore
        self.removed = removed
        self.created = created
        self.rename_source = None

    def included(self, event):
        return not self.should_ignore(self.root / event.src_path)

    def admit(self, events):
        result = []
        for event in events:
            if event.is_renamed_old:
                if self.rename_source is not None:
                    raise RuntimeError("Native rename source was replaced before its destination")
                self.rename_source = event
            elif event.is_renamed_new:
                result.extend(self.rename(event))
            elif self.included(event):
                result.append(event)
        return result

    def rename(self, destination):
        source = self.rename_source
        self.rename_source = None
        if self.included(destination):
            if source is None:
                return [self.created(destination.src_path)]
            return [source, destination]
        if source is not None and self.included(source):
            return [self.removed(source.src_path)]
        return []
