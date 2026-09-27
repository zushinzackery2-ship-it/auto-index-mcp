from __future__ import annotations

import subprocess

from auto_index_mcp.domain.ignore_rules import IgnoreRules


def test_directory_globs_match_descendants(tmp_path):
    rules = IgnoreRules(tmp_path, gitignore_patterns=["/src/*/generated/"])
    assert rules.is_ignored_rel("src/a/generated/code.py", False)
    assert not rules.is_ignored_rel("src/a/code.py", False)


def test_nested_rules_are_scoped_to_their_directory(tmp_path):
    child = tmp_path / "lib"
    child.mkdir()
    (child / ".gitignore").write_text("*.py\n!keep.py\n", encoding="utf-8")
    rules = IgnoreRules.from_root(tmp_path)
    assert rules.is_ignored_rel("lib/code.py", False)
    assert not rules.is_ignored_rel("lib/keep.py", False)
    assert not rules.is_ignored_rel("app.py", False)


def test_ignore_rules_agree_with_git_for_parent_pruning(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("closed/\n!closed/keep.py\n/src/*/generated/\n", encoding="utf-8")
    rules = IgnoreRules.from_root(tmp_path)
    for path in ("closed/keep.py", "src/a/generated/code.py", "src/b/code.py"):
        expected = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "--no-index", "-q", path]).returncode == 0
        assert rules.is_ignored_rel(path, False) == expected
