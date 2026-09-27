from pathlib import Path
import pytest
from auto_index_mcp.core.service import AutoIndexService

CODE_PY = "def compute(rows):\n    return sum(rows)\n"


@pytest.fixture
def project(tmp_path: Path, write_file) -> Path:
    root = tmp_path / "proj"
    write_file(root / "src" / "mod.py", CODE_PY)
    return root


def enable_in_place(project: Path, source: str = "mcp-enable") -> AutoIndexService:
    """Enable with the default in-project index location (non-ephemeral)."""
    service = AutoIndexService()
    service.enable(str(project), rebuild=True, refresh_embedder=False, source=source)
    return service
