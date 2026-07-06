from __future__ import annotations

import random
import string

from auto_index_mcp.core.multi_match import MultiPatternMatcher
from auto_index_mcp.core.quality_dangling import _incoming_import_counts


def test_basic_and_overlapping_patterns():
    matcher = MultiPatternMatcher(["he", "she", "his", "hers"])
    assert matcher.matched_patterns("ushers") == {"he", "she", "hers"}


def test_pattern_that_is_suffix_of_another():
    matcher = MultiPatternMatcher(["core/utils", "utils"])
    assert matcher.matched_patterns("from core/utils import x") == {"core/utils", "utils"}


def test_empty_patterns_and_empty_text():
    assert MultiPatternMatcher([]).matched_patterns("anything") == set()
    assert MultiPatternMatcher(["a"]).matched_patterns("") == set()


def test_duplicate_patterns_are_deduped():
    matcher = MultiPatternMatcher(["abc", "abc"])
    assert matcher.matched_patterns("xxabcxx") == {"abc"}


def test_random_equivalence_with_bruteforce():
    rng = random.Random(20260702)
    alphabet = string.ascii_lowercase[:6] + "/._"
    for _round in range(30):
        patterns = [
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
            for _ in range(rng.randint(1, 20))
        ]
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
        expected = {pattern for pattern in set(patterns) if pattern in text}
        assert MultiPatternMatcher(patterns).matched_patterns(text) == expected


def _file(path: str, imports: list[str]) -> dict:
    return {"path": path, "language": "python", "imports": imports, "symbols": []}


def test_incoming_import_counts_matches_legacy_semantics():
    files = [
        _file("pkg/util.py", []),
        _file("pkg/app.py", ["from pkg.util import x", "import helpers"]),
        _file("helpers.py", []),
        _file("standalone.py", ["import nothing_known"]),
    ]
    counts = _incoming_import_counts(files)
    # app.py imports mention "helpers" (stem of helpers.py); pkg.util does not
    # contain the slashed key "pkg/util" but "util" stem matches "pkg.util"?
    # Stem "util" appears in "from pkg.util import x" -> counted.
    assert counts["helpers.py"] == 1
    assert counts["pkg/util.py"] == 1
    assert counts["standalone.py"] == 0
    assert counts["pkg/app.py"] == 0


def test_incoming_import_counts_ignores_self_reference():
    files = [_file("loop.py", ["import loop"])]
    assert _incoming_import_counts(files)["loop.py"] == 0


def test_incoming_import_counts_bruteforce_equivalence():
    rng = random.Random(42)
    names = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    files = []
    for index in range(18):
        parent = rng.choice(["", "src", "src/inner", "lib"])
        name = f"{rng.choice(names)}{index}.py"
        path = f"{parent}/{name}".strip("/")
        imports = [
            f"import {rng.choice(names)}{rng.randint(0, 17)}"
            for _ in range(rng.randint(0, 4))
        ]
        files.append(_file(path, imports))

    def legacy(files_list):
        incoming = {item["path"]: 0 for item in files_list}
        text_by_path = {
            item["path"]: "\n".join(item.get("imports", [])).replace("\\", "/")
            for item in files_list
        }
        from pathlib import Path

        def keys_for(path):
            without_ext = str(Path(path).with_suffix("")).replace("\\", "/")
            return {without_ext, Path(path).stem}

        keys = {item["path"]: keys_for(item["path"]) for item in files_list}
        for source, text in text_by_path.items():
            for target, target_keys in keys.items():
                if source != target and any(key and key in text for key in target_keys):
                    incoming[target] += 1
        return incoming

    assert _incoming_import_counts(files) == legacy(files)
