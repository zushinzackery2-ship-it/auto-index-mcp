from __future__ import annotations

import re

from ..domain.models import SymbolRecord
from ..languages.source_clean import clean_source_lines
from .nesting import annotate_symbol_nesting

CALL_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*\(")
CONTROL_NAMES = {"if", "for", "while", "switch", "return", "raise", "catch", "with"}
COMPLEXITY_RE = re.compile(r"\b(if|elif|else if|for|while|case|catch|except|and|or|\?|&&|\|\|)\b")

TOKEN_RE = re.compile(r"[A-Za-z_]\w*|[^\s\w]")
# Definition lines are skipped for reference extraction so parameter names in
# single-line signatures are never mistaken for value references.
DEF_LINE_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|procedure|constructor|interface|struct|enum)\b"
)
REF_STOP_NAMES = CONTROL_NAMES | {
    "False", "None", "True", "and", "as", "assert", "async", "await", "bool",
    "break", "case", "char", "class", "cls", "const", "continue", "def",
    "default", "del", "delete", "do", "double", "elif", "else", "except",
    "false", "finally", "float", "from", "function", "global", "import", "in",
    "int", "is", "lambda", "let", "long", "new", "nonlocal", "not", "null",
    "nullptr", "or", "pass", "self", "short", "signed", "sizeof", "static",
    "struct", "super", "this", "true", "try", "typeof", "undefined",
    "unsigned", "var", "void", "yield",
}
MAX_REFS_PER_SYMBOL = 64


def enrich_symbols(
    lines: list[str],
    symbols: list[SymbolRecord],
    language: str = "",
    cleaned_lines: list[str] | None = None,
) -> list[SymbolRecord]:
    # Comment/string regions are blanked once per file so complexity, call and
    # nesting analysis only ever see executable code.
    cleaned = cleaned_lines if cleaned_lines is not None else clean_source_lines(lines, language)
    if language == "python":
        return annotate_symbol_nesting(cleaned, symbols, language)
    enriched = []
    for symbol in symbols:
        body = cleaned[symbol.line - 1:symbol.end_line]
        calls = _calls(body, symbol.name)
        enriched.append(
            SymbolRecord(
                name=symbol.name,
                kind=symbol.kind,
                line=symbol.line,
                end_line=symbol.end_line,
                signature=symbol.signature,
                complexity=_complexity(body),
                calls=calls,
                called_by=[],
                refs=_value_refs(body, symbol.name, calls),
            )
        )
    return annotate_symbol_nesting(cleaned, enriched, language)


def _complexity(cleaned_body: list[str]) -> int:
    score = 1
    for line in cleaned_body:
        score += len(COMPLEXITY_RE.findall(line))
    return score


def _calls(cleaned_body: list[str], own_name: str) -> list[str]:
    calls: list[str] = []
    seen: set[str] = set()
    for line in cleaned_body:
        for name in CALL_RE.findall(line):
            if name == own_name or name in CONTROL_NAMES:
                continue
            if name not in seen:
                seen.add(name)
                calls.append(name)
    return calls


def _value_refs(cleaned_body: list[str], own_name: str, calls: list[str]) -> list[str]:
    called = set(calls)
    refs: list[str] = []
    for line in cleaned_body:
        if DEF_LINE_RE.match(line):
            continue
        tokens = TOKEN_RE.findall(line)
        for index, name in enumerate(tokens):
            previous = tokens[index - 1] if index else ""
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if previous not in {"=", "(", ",", "[", "{", ":", "@", "&", "return", "yield"}:
                continue
            if following in {"(", "=", ":"} or not name.isidentifier():
                continue
            if name == own_name or name in REF_STOP_NAMES or name in called:
                continue
            if name not in refs:
                refs.append(name)
                if len(refs) >= MAX_REFS_PER_SYMBOL:
                    return refs
    return refs
