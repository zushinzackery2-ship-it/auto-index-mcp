from __future__ import annotations

from watchdog.observers.api import BaseObserver
from watchdog.observers.read_directory_changes import WindowsApiEmitter
from watchdog.observers.winapi import FILE_ACTION_ADDED, FILE_ACTION_REMOVED, WinAPINativeEvent

from .native_policy import NativeEventPolicy


def policy_observer(should_ignore):
    class PolicyEmitter(WindowsApiEmitter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._path_policy = NativeEventPolicy(self.watch.path, should_ignore,
                lambda path: WinAPINativeEvent(FILE_ACTION_REMOVED, path),
                lambda path: WinAPINativeEvent(FILE_ACTION_ADDED, path))

        def _read_events(self):
            return self._path_policy.admit(super()._read_events())

    return BaseObserver(PolicyEmitter)
