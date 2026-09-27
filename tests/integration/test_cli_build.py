"""CLI build/status subcommands: one-shot pre-build, progress, lock safety."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from auto_index_mcp import cli
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.runtime.leases import BuildLock

CODE_PY = "def compute(rows):\n    return sum(rows)\n\n\ndef render():\n    return 'ok'\n"


@pytest.fixture
def project(tmp_path: Path, write_file) -> Path:
    root = tmp_path / "proj"
    write_file(root / "src" / "mod.py", CODE_PY)
    return root


def test_build_creates_persistent_index(project: Path, capsys) -> None:
    assert cli.main(["build", str(project), "--no-semantic"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    out = capsys.readouterr().out
    assert "index:" in out
    assert "skipped (--no-semantic)" in out


def test_second_build_reuses_fresh_index(project: Path, capsys) -> None:
    assert cli.main(["build", str(project), "--no-semantic"]) == 0
    capsys.readouterr()
    assert cli.main(["build", str(project), "--no-semantic"]) == 0
    assert "reused" in capsys.readouterr().out


def test_rebuild_flag_forces_rescan(project: Path, capsys) -> None:
    assert cli.main(["build", str(project), "--no-semantic"]) == 0
    capsys.readouterr()
    assert cli.main(["build", str(project), "--no-semantic", "--rebuild"]) == 0
    out = capsys.readouterr().out
    assert "reused" not in out
    assert "files in" in out


def test_build_with_embedder_reports_vectors(project: Path, install_embedder, capsys) -> None:
    install_embedder(BagHashEmbedder(dim=32))
    assert cli.main(["build", str(project)]) == 0
    out = capsys.readouterr().out
    assert "semantic vectors:" in out
    assert (project / ".auto-index-mcp" / "embeddings.db").exists()


def test_build_without_model_skips_semantic_gracefully(project: Path, capsys) -> None:
    # conftest disables the default embedding backend, so the semantic phase
    # must degrade to an explicit skip instead of failing the build.
    assert cli.main(["build", str(project)]) == 0
    assert "semantic vectors: skipped" in capsys.readouterr().out


def test_build_rejects_missing_directory(tmp_path: Path, capsys) -> None:
    assert cli.main(["build", str(tmp_path / "nope")]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_build_reports_concurrent_builder(project: Path, capsys) -> None:
    lock_dir = project / ".auto-index-mcp"
    lock_dir.mkdir(parents=True, exist_ok=True)
    holder = BuildLock(lock_dir / "index.build.lock")
    assert holder.try_acquire() is True
    try:
        assert cli.main(["build", str(project), "--no-semantic"]) == 1
        out = capsys.readouterr().out
        assert "another process" in out
        assert str(os.getpid()) in out
    finally:
        holder.release()


def test_status_before_and_after_build(project: Path, capsys) -> None:
    assert cli.main(["status", str(project)]) == 1
    assert "no index" in capsys.readouterr().out
    assert cli.main(["build", str(project), "--no-semantic", "--quiet"]) == 0
    capsys.readouterr()
    assert cli.main(["status", str(project)]) == 0
    out = capsys.readouterr().out
    assert "files:" in out
    assert "updated:" in out


def test_prebuilt_index_is_reused_by_service_enable(project: Path) -> None:
    assert cli.main(["build", str(project), "--no-semantic", "--quiet"]) == 0
    service = AutoIndexService()
    try:
        result = service.enable_reusing_index(str(project))
        assert result["enabled"] is True
        assert result["file_count"] == 1
        # The reuse fast path must not have dispatched a fresh full build.
        assert result["index_build"]["running"] is False
    finally:
        service.disable()


def test_embedding_progress_callback_fires(tmp_path: Path, write_file, install_embedder) -> None:
    project = tmp_path / "proj"
    write_file(project / "mod.py", CODE_PY)
    install_embedder(BagHashEmbedder(dim=32))
    calls: list[tuple[int, int, int]] = []
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.embedding_progress = lambda done, total, reused: calls.append((done, total, reused))
    try:
        service.enable(str(project), rebuild=True)
        assert service.embedding_background is not None
        assert service.embedding_background.wait(10.0) is True
        assert calls, "progress callback never fired"
        done, total, _reused = calls[-1]
        assert done == total > 0
    finally:
        service.disable()
