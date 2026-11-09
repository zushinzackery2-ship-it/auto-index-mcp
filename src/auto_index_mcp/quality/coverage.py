"""Qualify negative evidence with the analysis that could observe usage."""
from collections import Counter

from ..domain.coverage import SYMBOL_LANGUAGES


def reference_coverage(files):
    code = [item for item in files if item["language"] in SYMBOL_LANGUAGES]
    kinds = Counter(item.get("analysis_kind", "unknown") for item in code)
    return dict(scope="indexed-workspace", external_callers="unknown",
                analysis=dict(kinds), complete_reference_analysis=bool(code) and set(kinds) == {"python-ast"},
                limitations=["dynamic-dispatch", "external-callers", "excluded-files"])


def unused_evidence(item, symbol, ambiguous, coverage):
    limitations = list(symbol.get("usage_constraints", []))
    if ambiguous:
        limitations.append("ambiguous-receiver")
    if not coverage["complete_reference_analysis"]:
        limitations.append("incomplete-reference-analysis")
    private = symbol["name"].startswith("_")
    if limitations:
        confidence = "low"
    elif private and item.get("analysis_kind") == "python-ast":
        confidence = "high"
    else:
        confidence = "medium"
    return dict(confidence=confidence, scope=coverage["scope"], limitations=limitations,
                reason="no resolved references in the indexed workspace" if not ambiguous
                else "references exist but their receiver is ambiguous")
