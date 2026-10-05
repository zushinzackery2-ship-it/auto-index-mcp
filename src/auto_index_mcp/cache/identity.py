"""Filesystem directory identity survives a rename without source markers."""
import hashlib
import os
from pathlib import Path


def directory_identity(path: Path) -> str | None:
    try:
        stat = Path(path).stat()
    except FileNotFoundError:
        return None
    return f"{stat.st_dev}:{stat.st_ino}"


def identity_key(path: Path) -> str:
    identity = directory_identity(path)
    if identity is None:
        identity = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
