"""Disappearing cache entries are absent; denied entries remain incomplete."""
from pathlib import Path

import pytest

from auto_index_mcp.cache import inventory


@pytest.mark.parametrize("name", ["index.db-wal", "index.db-shm"])
@pytest.mark.parametrize("stage", ["ownership", "size"])
def test_inventory_accepts_an_entry_deleted_after_enumeration(tmp_path, monkeypatch, name, stage):
    (tmp_path / "stable.bin").write_bytes(b"123")
    artifact = tmp_path / name
    artifact.write_bytes(b"temporary")
    original_link, original_stat = inventory.is_link, Path.stat

    def check_link(path):
        if path == artifact and stage == "ownership":
            artifact.unlink()
        return original_link(path)

    def stat(path, *args, **kwargs):
        if path == artifact and stage == "size":
            artifact.unlink()
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(inventory, "is_link", check_link)
    monkeypatch.setattr(Path, "stat", stat)
    result = inventory.file_inventory(tmp_path)
    assert result == dict(bytes=3, partial=False, errors=[])


def test_inventory_keeps_permission_errors_visible(tmp_path, monkeypatch):
    artifact = tmp_path / "index.db-wal"
    artifact.write_bytes(b"temporary")
    original = inventory.is_link

    def denied(path):
        if path == artifact:
            raise PermissionError("metadata access denied")
        return original(path)

    monkeypatch.setattr(inventory, "is_link", denied)
    result = inventory.file_inventory(tmp_path)
    assert result["partial"] and "access denied" in result["errors"][0]


def test_inventory_keeps_directory_loss_visible(tmp_path, monkeypatch):
    removed = tmp_path / "removed"
    removed.mkdir()

    def changed_tree(*args, **kwargs):
        removed.rmdir()
        yield str(tmp_path), ["removed"], []

    monkeypatch.setattr(inventory.os, "walk", changed_tree)
    result = inventory.file_inventory(tmp_path)
    assert result["partial"] and result["errors"]
