"""Index registry: registration on enable, markers, verify/clean/list surfaces."""

from __future__ import annotations

import os
import time
from pathlib import Path

from auto_index_mcp.cli import main as cli_main
from auto_index_mcp.registry import (
    IndexRegistry,
    registry_key,
    write_index_markers,
)

from tests.fixtures.registry import enable_in_place as _enable_in_place
from tests.fixtures.registry import project as project


def test_cli_list_prints_entries(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["list"]) == 0
    output = capsys.readouterr().out
    assert str(project.resolve()) in output
    assert "last attached" in output


def test_cli_clean_dry_run_deletes_nothing(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", str(project), "--dry-run"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert "dry run" in capsys.readouterr().out


def test_cli_clean_target_removes_index_and_entry(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", str(project), "-y"]) == 0
    assert not (project / ".auto-index-mcp" / "index.db").exists()
    assert IndexRegistry().is_registered(project) is False
    assert "removed" in capsys.readouterr().out


def test_cli_clean_default_selects_orphans_and_ephemeral(
    project: Path,
    tmp_path: Path,
    capsys,
) -> None:
    # Fresh, healthy, non-ephemeral index: the no-argument default must keep it.
    service = _enable_in_place(project)
    service.disable()
    # Orphan entry (project deleted) and an ephemeral one.
    orphan_root = tmp_path / "orphan"
    orphan_root.mkdir()
    registry = IndexRegistry()
    registry.register(orphan_root, orphan_root / ".auto-index-mcp", source="scan")
    import shutil

    shutil.rmtree(orphan_root)
    override = tmp_path / ".idx"
    write_index_markers(override, tmp_path / "ephproj")
    (override / "index.db").write_bytes(b"")
    (tmp_path / "ephproj").mkdir()
    registry.register(tmp_path / "ephproj", override, source="mcp-enable", ephemeral=True)

    assert cli_main(["clean", "-y"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert registry.is_registered(project) is True
    assert registry.is_registered(orphan_root) is False
    assert registry.is_registered(tmp_path / "ephproj") is False
    assert not (override / "index.db").exists()
    capsys.readouterr()


def test_cli_clean_older_than_filters_by_last_attach(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    assert cli_main(["clean", "--older-than", "30d", "-y"]) == 0
    assert (project / ".auto-index-mcp" / "index.db").exists()

    registry = IndexRegistry()
    data = registry._read()
    data["entries"][registry_key(project / ".auto-index-mcp")]["last_attached_at"] = time.time() - 40 * 86400
    registry._write(data)
    assert cli_main(["clean", "--older-than", "30d", "-y"]) == 0
    assert not (project / ".auto-index-mcp" / "index.db").exists()
    capsys.readouterr()


def test_cli_clean_skips_directories_with_active_build_lock(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    lock = project / ".auto-index-mcp" / "index.build.lock"
    lock.write_text(f"{os.getpid()}:1:1\n0", encoding="ascii")
    assert cli_main(["clean", str(project), "-y"]) == 1
    assert (project / ".auto-index-mcp" / "index.db").exists()
    assert IndexRegistry().is_registered(project) is True
    assert "busy" in capsys.readouterr().out


def test_cli_clean_scan_adopts_unregistered_index(project: Path, capsys) -> None:
    service = _enable_in_place(project)
    service.disable()
    registry = IndexRegistry()
    registry.unregister(project)
    assert cli_main(["clean", "--scan", str(project.parent)]) == 0
    assert registry.is_registered(project) is True
    assert "adopted" in capsys.readouterr().out


def test_cli_clean_rejects_bad_older_than(capsys) -> None:
    assert cli_main(["clean", "--older-than", "soon"]) == 2
    assert "invalid --older-than" in capsys.readouterr().err
