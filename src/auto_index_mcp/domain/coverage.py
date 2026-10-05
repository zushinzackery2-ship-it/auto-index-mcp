"""Text coverage and symbol analysis are separate, explicit capabilities."""
from .config import LANGUAGE_BY_EXTENSION, text_extensions

SYMBOL_LANGUAGES = {"python", "c", "cpp", "csharp", "java", "kotlin", "rust", "go",
                    "javascript", "typescript", "pascal", "vue"}


def capabilities() -> dict:
    return dict(
        text_extensions=sorted(text_extensions()),
        symbol_analysis={language: "python-ast" if language == "python" else "heuristic"
                         for language in sorted(SYMBOL_LANGUAGES)},
        text_only_languages=sorted(set(LANGUAGE_BY_EXTENSION.values()) - SYMBOL_LANGUAGES),
    )
