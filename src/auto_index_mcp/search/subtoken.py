from __future__ import annotations

import re

# Identifier-level tokenization shared by symbol-search ranking and semantic
# rerank. The scheme mirrors RagFlow's fine-grained tokenization adapted to
# code: identifiers split on separators, camelCase humps, acronym->word
# boundaries (HTTPServer -> http/server) and letter<->digit boundaries
# (utf8Decode -> utf/8/decode), all lowercased.
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CAMEL_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])"  # acronym run followed by a capitalized word
    r"|[A-Z]?[a-z]+"         # capitalized or lowercase word
    r"|[A-Z]+"               # trailing acronym run
    r"|[0-9]+"               # digit run
)

# Query weights follow RagFlow's token_similarity: adjacent-pair matches carry
# more evidence than isolated unigram matches, because a preserved pair means
# the query's word order survives inside the identifier.
UNIGRAM_WEIGHT = 0.4
BIGRAM_WEIGHT = 0.6


def split_identifier(text: str) -> list[str]:
    """Ordered lowercase subtokens of an identifier or free-text query."""
    tokens: list[str] = []
    for word in _WORD_RE.findall(text):
        for part in _CAMEL_RE.findall(word):
            tokens.append(part.lower())
    return tokens


def query_weights(text: str) -> dict[str, float]:
    """Weighted query terms: unigrams plus adjacent-bigram concatenations."""
    tokens = split_identifier(text)
    weights: dict[str, float] = {}
    for index, token in enumerate(tokens):
        weights[token] = weights.get(token, 0.0) + UNIGRAM_WEIGHT
        if index + 1 < len(tokens):
            pair = token + tokens[index + 1]
            weights[pair] = weights.get(pair, 0.0) + BIGRAM_WEIGHT
    return weights


def target_token_set(text: str) -> set[str]:
    """Match-target terms: unigrams plus adjacent-bigram concatenations."""
    tokens = split_identifier(text)
    target = set(tokens)
    for index in range(len(tokens) - 1):
        target.add(tokens[index] + tokens[index + 1])
    return target


def overlap_score(
    weights: dict[str, float],
    primary: set[str],
    secondary: set[str],
    secondary_factor: float = 0.6,
) -> float:
    """Fraction of query weight found in the target, in [0, 1].

    A term matched in ``primary`` (the symbol name) counts at full weight; a
    term matched only in ``secondary`` (the signature) is discounted, mirroring
    RagFlow's field boosting where name-ish fields dominate body matches.
    """
    if not weights:
        return 0.0
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    matched = 0.0
    for token, weight in weights.items():
        if token in primary:
            matched += weight
        elif token in secondary:
            matched += weight * secondary_factor
    return matched / total
