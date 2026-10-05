"""Read immutable metadata versions and publish durable replacements."""
import json
import os
from pathlib import Path
import tempfile

from .gate import publication_gate


def read_json(path: Path):
    with publication_gate(path):
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)


def atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        with publication_gate(path):
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
