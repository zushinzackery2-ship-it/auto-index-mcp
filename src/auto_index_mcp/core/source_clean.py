"""Stateful source-line cleaning shared by symbol extraction and analysis.

The per-line helpers in ``_utils`` (``strip_comments`` / ``strip_string_literals``)
are stateless: they cannot see a ``/* ... */`` block spanning lines, an escaped
quote, a Python triple-quoted string, or a JS template literal. Every consumer
that counts braces or matches definition keywords on such lines mis-fires inside
comment/string regions - the root cause of mis-detected symbol starts, phantom
nesting depth, and dangling-code false positives in comment-dense C++ sources.

``clean_source_lines`` walks a whole file once with a language-aware state
machine and returns lines of identical count and length where every comment and
string-literal character is replaced by a space. Line numbers, indentation and
column positions all survive, so downstream brace counting, indent tracking and
regex matching operate on code only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class CleanRules:
    """Comment/string syntax for one language family."""

    line_comments: tuple[str, ...] = ()
    block_comments: tuple[tuple[str, str], ...] = ()
    string_delims: tuple[str, ...] = ()
    # Delimiters that may legally span lines (Python triple quotes, JS backtick).
    multiline_strings: tuple[str, ...] = ()
    backslash_escapes: bool = True
    # Pascal doubles the quote character inside strings ('it''s').
    doubled_quote_escape: bool = False
    _token_re: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tokens = sorted(
            set(self.line_comments)
            | {start for start, _end in self.block_comments}
            | set(self.string_delims)
            | set(self.multiline_strings),
            key=len,
            reverse=True,
        )
        pattern = "|".join(re.escape(token) for token in tokens) or r"(?!)"
        object.__setattr__(self, "_token_re", re.compile(pattern))

    def block_end_for(self, start: str) -> str | None:
        for begin, end in self.block_comments:
            if begin == start:
                return end
        return None


_BRACE_RULES = CleanRules(
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_delims=('"', "'"),
)
_JS_RULES = CleanRules(
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_delims=('"', "'", "`"),
    multiline_strings=("`",),
)
_PYTHON_RULES = CleanRules(
    line_comments=("#",),
    string_delims=('"""', "'''", '"', "'"),
    multiline_strings=('"""', "'''"),
)
_PASCAL_RULES = CleanRules(
    line_comments=("//",),
    block_comments=(("{", "}"), ("(*", "*)")),
    string_delims=("'",),
    backslash_escapes=False,
    doubled_quote_escape=True,
)
# Matches the legacy stateless behavior (# and // comments, three quote kinds)
# for files whose language is unknown, plus block-comment awareness.
_DEFAULT_RULES = CleanRules(
    line_comments=("#", "//"),
    block_comments=(("/*", "*/"),),
    string_delims=('"', "'", "`"),
)

_RULES_BY_LANGUAGE = {
    "c": _BRACE_RULES,
    "cpp": _BRACE_RULES,
    "csharp": _BRACE_RULES,
    "go": _BRACE_RULES,
    "java": _BRACE_RULES,
    "kotlin": _JS_RULES,
    "rust": _BRACE_RULES,
    "php": _JS_RULES,
    "javascript": _JS_RULES,
    "typescript": _JS_RULES,
    "python": _PYTHON_RULES,
    "pascal": _PASCAL_RULES,
}


@lru_cache(maxsize=None)
def rules_for(language: str) -> CleanRules:
    return _RULES_BY_LANGUAGE.get(language, _DEFAULT_RULES)


# Cross-line scanner state: nothing pending, inside a block comment (waiting for
# its end token), or inside a multiline string (waiting for its delimiter).
_STATE_NONE = 0
_STATE_BLOCK = 1
_STATE_STRING = 2


def clean_source_lines(lines: list[str], language: str) -> list[str]:
    """Blank out comments and string literals across a whole file.

    Returns one output line per input line with identical length; every
    character inside a comment or string literal (delimiters included) becomes
    a space. An unterminated single-line string blanks to end of line and does
    not leak state into the next line.
    """
    rules = rules_for(language)
    cleaned: list[str] = []
    state = _STATE_NONE
    closer = ""
    for line in lines:
        text, state, closer = _clean_line(line, rules, state, closer)
        cleaned.append(text)
    return cleaned


def _clean_line(
    line: str,
    rules: CleanRules,
    state: int,
    closer: str,
) -> tuple[str, int, str]:
    parts: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        if state == _STATE_BLOCK:
            end = line.find(closer, index)
            if end < 0:
                parts.append(" " * (length - index))
                index = length
                break
            end += len(closer)
            parts.append(" " * (end - index))
            index = end
            state = _STATE_NONE
            continue
        if state == _STATE_STRING:
            end = _find_string_end(line, index, closer, rules)
            if end < 0:
                parts.append(" " * (length - index))
                index = length
                break
            parts.append(" " * (end - index))
            index = end
            state = _STATE_NONE
            continue
        match = rules._token_re.search(line, index)
        if match is None:
            parts.append(line[index:])
            break
        start = match.start()
        token = match.group()
        parts.append(line[index:start])
        if token in rules.line_comments:
            parts.append(" " * (length - start))
            index = length
            break
        block_end = rules.block_end_for(token)
        if block_end is not None:
            state = _STATE_BLOCK
            closer = block_end
            index = start + len(token)
            parts.append(" " * len(token))
            continue
        # String delimiter: blank the literal, delimiters included.
        content = start + len(token)
        end = _find_string_end(line, content, token, rules)
        if end >= 0:
            parts.append(" " * (end - start))
            index = end
            continue
        parts.append(" " * (length - start))
        index = length
        if token in rules.multiline_strings:
            state = _STATE_STRING
            closer = token
        break
    return "".join(parts), state, closer


def _find_string_end(line: str, index: int, delim: str, rules: CleanRules) -> int:
    """Position just past the closing delimiter, or -1 when it never closes."""
    length = len(line)
    while index <= length:
        end = line.find(delim, index)
        if end < 0:
            return -1
        if rules.backslash_escapes and _is_backslash_escaped(line, end):
            index = end + 1
            continue
        after = end + len(delim)
        if rules.doubled_quote_escape and line.startswith(delim, after):
            index = after + len(delim)
            continue
        return after
    return -1


def _is_backslash_escaped(line: str, position: int) -> bool:
    backslashes = 0
    cursor = position - 1
    while cursor >= 0 and line[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1
