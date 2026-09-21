from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(index_root: Path) -> str:
    logger = logging.getLogger("auto_index_mcp")
    path = index_root / "logs" / f"server-{os.getpid()}.log"
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            if Path(handler.baseFilename) == path:
                return str(path)
            logger.removeHandler(handler)
            handler.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Per-process rotation avoids Windows rename conflicts between agents.
    handler = RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s"))
    logger.addHandler(handler)
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
    target = Path(path).resolve()
    logger = logging.getLogger("auto_index_mcp")
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == target:
            logger.removeHandler(handler)
            handler.close()
