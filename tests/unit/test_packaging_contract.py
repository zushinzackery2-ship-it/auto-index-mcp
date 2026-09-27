import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from auto_index_mcp.runtime.dependencies import ripgrep_executable


def test_mcp_dependency_excludes_incompatible_major_version():
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    config = tomllib.loads(project.read_text(encoding="utf-8"))
    dependency = next(Requirement(value) for value in config["project"]["dependencies"]
                      if Requirement(value).name == "mcp")
    assert "1.27.1" in dependency.specifier
    assert "2.0.0" not in dependency.specifier


def test_packaged_ripgrep_works_without_path_activation(monkeypatch):
    monkeypatch.setenv("PATH", "")
    executable = ripgrep_executable()
    assert executable is not None
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=True)
    assert result.stdout.startswith("ripgrep ")
