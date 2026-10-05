"""One analysis entrypoint preserves the capabilities of each language."""
from ..domain.coverage import SYMBOL_LANGUAGES
from ..indexing.analysis import enrich_symbols
from .c_family import extract_c_family_symbols
from .generic import extract_symbols
from .go import extract_go_symbols
from .javascript import extract_javascript_like_symbols
from .pascal import extract_pascal_symbols
from .python import extract_python_symbols, parse_python
from .source_clean import clean_source_lines
from .vue import script_source


def analyze_symbols(text: str, lines: list[str], language: str):
    analysis_lines = script_source(text).splitlines() if language == "vue" else lines
    cleaned = clean_source_lines(analysis_lines, "typescript" if language == "vue" else language)
    tree = parse_python(text) if language == "python" else None
    if language == "python":
        symbols = extract_python_symbols(text, lines, tree)
        kind = "python-ast" if tree is not None else "parse-error"
    elif language not in SYMBOL_LANGUAGES:
        return [], cleaned, None, "text-only"
    else:
        symbols = _extractor(language)(lines, cleaned)
        kind = "heuristic"
    return enrich_symbols(lines, symbols, language, cleaned), cleaned, tree, kind


def _extractor(language):
    if language in {"javascript", "typescript", "vue"}:
        return extract_javascript_like_symbols
    if language in {"c", "cpp"}:
        return extract_c_family_symbols
    if language == "pascal":
        return extract_pascal_symbols
    if language == "go":
        return extract_go_symbols
    return extract_symbols
