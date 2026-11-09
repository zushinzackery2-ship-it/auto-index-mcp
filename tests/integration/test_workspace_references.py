"""Storage layout must not change the analysis scope of a workspace."""
from contextlib import ExitStack

import pytest

from auto_index_mcp.core.service import AutoIndexService


@pytest.fixture
def workspace(tmp_path, write_file):
    root = tmp_path / "project"
    write_file(root / "entry.py", "from child.lib import _target\n\ndef entry():\n    return _target()\n")
    write_file(root / "child/lib.py", "def _target():\n    return 1\n")
    write_file(root / "child/nested/caller.py", "from child.lib import _target\n\ndef nested():\n    return _target()\n")
    with ExitStack() as cleanup:
        services = []
        for directory, mount in [(root / "child/nested", False), (root / "child", True),
                                 (root, False), (root, True)]:
            service = AutoIndexService()
            cleanup.callback(service.disable)
            service.enable(str(directory), mount_child_indexes=mount, refresh_embedder=False)
            services.append(service)
        yield root, services


def test_flat_and_nested_mounted_workspaces_have_identical_references(workspace):
    _, (_, _, flat, mounted) = workspace
    expected = flat.symbol_refs("_target")["items"][0]
    actual = mounted.symbol_refs("_target")["items"][0]
    assert actual == expected
    assert actual["callers"] == ["child/nested/caller.py::nested", "entry.py::entry"]
    assert not any(row.get("symbol") == "_target" for row in mounted.dangling_check()["findings"])
    file = mounted.get("child/lib.py")["item"]
    assert file["symbols"][0]["called_by"] == expected["callers"]


def test_reference_projection_tracks_other_process_publications(workspace, write_file):
    root, (nested, _, _, mounted) = workspace
    mounted.symbol_refs("_target")
    write_file(root / "child/nested/caller.py", "def nested():\n    return 2\n")
    nested.sync_index_to_filesystem()
    result = mounted.symbol_refs("_target")["items"][0]
    assert result["callers"] == ["entry.py::entry"]
    assert result["importers"] == ["entry.py::<module>"]


def test_qualified_sibling_reference_does_not_create_ambiguous_edges(workspace, write_file):
    root, (_, child, flat, mounted) = workspace
    write_file(root / "child/other.py", "def _target():\n    return 2\n")
    child.sync_index_to_filesystem()
    flat.sync_index_to_filesystem()
    actual = mounted.symbol_refs("_target")["items"]
    expected = flat.symbol_refs("_target")["items"]
    assert actual == expected
    unused = next(row for row in actual if row["path"] == "child/other.py")
    assert unused["callers"] == unused["ambiguous_callers"] == unused["importers"] == []


def test_references_report_scope_and_source_revisions(workspace):
    _, (_, _, _, mounted) = workspace
    result = mounted.symbol_refs("_target")
    coverage = result["reference_scope"]
    assert coverage["scope"] == "indexed-workspace"
    assert coverage["external_callers"] == "unknown"
    assert {source["path"] for source in coverage["sources"]} == {"", "child", "child/nested"}
    assert all(source["revision"] for source in coverage["sources"])


def test_relative_imports_preserve_package_identity(workspace, write_file):
    root, (nested, _, flat, mounted) = workspace
    write_file(root / "child/nested/caller.py", "from ..lib import _target\n\ndef nested():\n    return _target()\n")
    nested.sync_index_to_filesystem()
    flat.sync_index_to_filesystem()
    actual = mounted.symbol_refs("_target")["items"][0]
    assert actual == flat.symbol_refs("_target")["items"][0]
    assert "child/nested/caller.py::nested" in actual["callers"]


def test_result_and_reference_scope_share_one_publication(workspace, write_file, monkeypatch):
    root, (nested, _, _, mounted) = workspace
    original = mounted.search._locate_symbols

    def locate(*args):
        result = original(*args)
        write_file(root / "child/nested/caller.py", "def nested():\n    return 2\n")
        nested.sync_index_to_filesystem()
        return result

    before = mounted.symbol_refs("_target")["reference_scope"]
    monkeypatch.setattr(mounted.search, "_locate_symbols", locate)
    result = mounted.symbol_refs("_target")
    assert result["reference_scope"] == before
    assert "child/nested/caller.py::nested" in result["items"][0]["callers"]


def test_relative_import_uses_exact_package_and_rejects_escape():
    from auto_index_mcp.domain.reference_resolution import resolve

    definitions = [dict(file_path=path, name="_target", nesting_path="_target", kind="function", line=1)
                   for path in ("child/lib.py", "unrelated/child/lib.py")]
    edge = dict(source_file="child/caller.py", qualifier=".lib._target", evidence="module-qualified")
    assert resolve(edge, definitions)[0] == definitions[:1]
    assert resolve(dict(edge, qualifier="...lib._target"), definitions)[0] == []
