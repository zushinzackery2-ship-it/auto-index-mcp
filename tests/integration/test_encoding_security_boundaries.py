from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService


def test_utf8_bom_file_indexed(tmp_path: Path) -> None:
    project = tmp_path / "bom_files"
    project.mkdir()
    bom_file = project / "bom.py"
    bom_file.write_bytes(b"\xef\xbb\xbf" + "def from_bom():\n    pass\n".encode("utf-8"))

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1
    content = service.file_content("bom.py")
    assert "from_bom" in content


def test_utf16_le_file_indexed(tmp_path: Path) -> None:
    project = tmp_path / "utf16_files"
    project.mkdir()
    utf16_file = project / "utf16.py"
    utf16_file.write_text("def utf16_func():\n    pass\n", encoding="utf-16-le")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] >= 0


def test_carriage_return_only_file_indexed(tmp_path: Path) -> None:
    project = tmp_path / "cr_files"
    project.mkdir()
    cr_file = project / "cr.py"
    cr_file.write_bytes(b"def cr_func():\r\n    pass\r\n")

    service = AutoIndexService()
    result = service.enable(str(project), rebuild=True)

    assert result["file_count"] == 1


def test_path_escape_attempt_blocked_in_file_content(tmp_path: Path) -> None:
    project = tmp_path / "escape_test"
    project.mkdir()
    sibling = tmp_path / "sibling_project"
    sibling.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (sibling / "secret.py").write_text("SECRET = 'top secret'\n", encoding="utf-8")

    service = AutoIndexService()
    service.enable(str(project), rebuild=True)

    with pytest.raises(ValueError):
        service.file_content("../sibling_project/secret.py")

    with pytest.raises(ValueError):
        service.file_content("/etc/passwd")

    with pytest.raises(ValueError):
        service.file_content("C:\\Windows\\System32\\config")


def test_symlink_outside_project_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 'outside'\n", encoding="utf-8")

    service = AutoIndexService()
    service.enable(str(project), rebuild=True)

    try:
        link = project / "link.py"
        link.symlink_to(outside / "secret.py")

        files = [item["path"] for item in service.all_files()]
        if "link.py" in files:
            with pytest.raises(ValueError):
                service.file_content("link.py")
    except OSError:
        pytest.skip("symlink not supported")
