from __future__ import annotations

from typing import Any


def nesting_report(
    files: list[dict[str, Any]],
    max_depth: int = 4,
    languages: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    allowed = set(languages or [])
    findings: list[dict[str, Any]] = []
    files_checked = 0
    symbols_checked = 0
    max_symbol_depth = 0
    max_block_depth = 0
    missing_nesting = 0

    for item in files:
        if allowed and item["language"] not in allowed:
            continue
        files_checked += 1
        for symbol in item["symbols"]:
            symbols_checked += 1
            depth = int(symbol.get("depth") or 0)
            block_depth = int(symbol.get("max_block_depth") or 0)
            max_symbol_depth = max(max_symbol_depth, depth)
            max_block_depth = max(max_block_depth, block_depth)
            if not symbol.get("nesting_path"):
                missing_nesting += 1
            reasons = _nesting_reasons(depth, block_depth, max_depth)
            if reasons:
                findings.append(_nesting_finding(item, symbol, depth, block_depth, reasons))

    findings.sort(key=lambda finding: (-finding["depth"], -finding["max_block_depth"], finding["path"], finding["line"]))
    coverage = _coverage(symbols_checked, missing_nesting)
    warnings = _coverage_warnings(symbols_checked, coverage)
    return {
        "format": "auto_index_nesting_check_v1",
        "summary": {
            "files_checked": files_checked,
            "symbols_checked": symbols_checked,
            "findings": min(len(findings), limit),
            "total_findings": len(findings),
            "max_symbol_depth": max_symbol_depth,
            "max_block_depth": max_block_depth,
            "threshold": max_depth,
            "missing_nesting_symbols": missing_nesting,
            "nesting_coverage": coverage,
            "reliable": not warnings,
        },
        "warnings": warnings,
        "findings": findings[:limit],
    }


def _coverage(symbols_checked: int, missing_nesting: int) -> float:
    if symbols_checked == 0:
        return 1.0
    return round((symbols_checked - missing_nesting) / symbols_checked, 4)


def _coverage_warnings(symbols_checked: int, coverage: float) -> list[str]:
    if symbols_checked >= 10 and coverage < 0.8:
        return [f"nesting coverage is {coverage:.1%}; result is unreliable for quality conclusions"]
    if symbols_checked > 0 and coverage == 0:
        return ["nesting metadata is missing for all checked symbols; result is unreliable"]
    return []


def _nesting_reasons(depth: int, block_depth: int, max_depth: int) -> list[str]:
    reasons = []
    if depth > max_depth:
        reasons.append(f"symbol nesting depth {depth} exceeds {max_depth}")
    if block_depth > max_depth:
        reasons.append(f"block nesting depth {block_depth} exceeds {max_depth}")
    return reasons


def _nesting_finding(
    item: dict[str, Any],
    symbol: dict[str, Any],
    depth: int,
    block_depth: int,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "kind": "deep_nesting",
        "path": item["path"],
        "language": item["language"],
        "symbol": symbol["name"],
        "symbol_kind": symbol["kind"],
        "line": symbol["line"],
        "end_line": symbol["end_line"],
        "depth": depth,
        "max_block_depth": block_depth,
        "nesting_path": symbol.get("nesting_path") or symbol["name"],
        "parent": symbol.get("parent_name") or "",
        "reason": "; ".join(reasons),
    }
