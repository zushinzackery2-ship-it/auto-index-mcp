from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

_scope = ContextVar("auto_index_log_scope", default=(None, "-"))
_lock = threading.RLock()
_users: dict[str, int] = dict()


@contextmanager
def diagnostic_scope(index_root=None, operation_id=None):
    directory = str(Path(index_root).resolve()) if index_root is not None else None
    token = _scope.set((directory, operation_id or uuid.uuid4().hex[:12]))
    try:
        yield
    finally:
        _scope.reset(token)


class _ProjectFilter(logging.Filter):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = str(directory.resolve())

    def filter(self, record: logging.LogRecord) -> bool:
        directory, operation_id = _scope.get()
        record.operation_id = operation_id
        return directory is None or directory == self.directory


def configure_logging(index_root: Path) -> str:
    with _lock:
        return _configure_logging(index_root.resolve())


def _configure_logging(index_root: Path) -> str:
    logger = logging.getLogger("auto_index_mcp")
    path = index_root / "logs" / f"server-{os.getpid()}.log"
    key = str(path)
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            if Path(handler.baseFilename) == path:
                _users[key] = _users.get(key, 0) + 1
                return key
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per-process rotation avoids Windows rename conflicts between agents.
    handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.addFilter(_ProjectFilter(index_root))
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d op=%(operation_id)s %(threadName)s %(name)s %(message)s"
    ))
    logger.addHandler(handler)
    _users[key] = 1
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logs = sorted(path.parent.glob("server-*.log*"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in logs[32:]:
        try:
            old.unlink()
        except OSError:
            continue
    return str(path)


def close_logging(path: str | None) -> None:
    if not path:
        return
    with _lock:
        target = Path(path).resolve()
        key = str(target)
        users = _users.get(key, 0)
        if users > 1:
            _users[key] = users - 1
            return
        _users.pop(key, None)
        logger = logging.getLogger("auto_index_mcp")
        for handler in list(logger.handlers):
            if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target:
                logger.removeHandler(handler)
                handler.close()
