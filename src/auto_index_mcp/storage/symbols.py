from __future__ import annotations

import sqlite3
from typing import Any

from .rows import symbol_row_to_dict
from .graph import attach_callers

# Match-quality tiers for text-driven symbol queries. Lower ranks first:
# an exact name match outranks a name prefix, then a name substring, then a
# signature-only hit. This mirrors full-text engines' field boosting, where
# name/title fields dominate body matches, and fixes the inversion where a
# subclass whose signature mentions a base class used to shadow the base
# class definition itself.
RANK_EXACT_NAME = 0
RANK_NAME_PREFIX = 1
RANK_NAME_SUBSTRING = 2
RANK_SIGNATURE = 3
# Relaxed subtoken matches always rank below every direct tier; their rank
# additionally grows with the number of subtokens missing from the name.
RANK_SUBTOKEN_BASE = 4

# Bounds the relaxed query size; queries rarely carry more useful subtokens.
MAX_RELAXED_SUBTOKENS = 8

_BASE_ORDER = " ORDER BY symbols.file_path, symbols.line LIMIT ? OFFSET ?"
_RANKED_ORDER = (
    " ORDER BY match_rank, length(symbols.name), symbols.file_path, symbols.line"
    " LIMIT ? OFFSET ?"
)


def query_ranked(
    conn: sqlite3.Connection,
    text: str,
    kind: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Symbols matching ``text`` in name or signature, best matches first.

    Without ``text`` this is the browse path: no ranking, path/line order.
    """
    if not text:
        return _query_browse(conn, kind, limit, offset)
    like = f"%{text}%"
    select = (
        "SELECT symbols.*, files.language, "
        "CASE "
        "WHEN lower(symbols.name) = lower(?) THEN 0 "
        "WHEN symbols.name LIKE ? THEN 1 "
        "WHEN symbols.name LIKE ? THEN 2 "
        "ELSE 3 END AS match_rank "
        "FROM symbols JOIN files ON files.path=symbols.file_path"
    )
    params: list[Any] = [text, f"{text}%", like]
    where = ["(symbols.name LIKE ? OR symbols.signature LIKE ?)"]
    params.extend([like, like])
    if kind:
        where.append("symbols.kind=?")
        params.append(kind)
    sql = select + " WHERE " + " AND ".join(where) + _RANKED_ORDER
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return attach_callers(conn, [symbol_row_to_dict(row) for row in rows])


def query_relaxed(
    conn: sqlite3.Connection,
    subtokens: list[str],
    kind: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Subtoken AND-match: every subtoken must appear in name or signature.

    Runs only when the direct query is empty. ``match_rank`` counts the
    subtokens missing from the name, so symbols whose name carries the whole
    query outrank ones that only match via their signature.
    """
    tokens = [token for token in subtokens if token][:MAX_RELAXED_SUBTOKENS]
    if not tokens:
        return []
    rank_terms = []
    rank_params: list[Any] = [RANK_SUBTOKEN_BASE]
    where = []
    where_params: list[Any] = []
    for token in tokens:
        like = f"%{token}%"
        rank_terms.append("(CASE WHEN symbols.name LIKE ? THEN 0 ELSE 1 END)")
        rank_params.append(like)
        where.append("(symbols.name LIKE ? OR symbols.signature LIKE ?)")
        where_params.extend([like, like])
    if kind:
        where.append("symbols.kind=?")
        where_params.append(kind)
    sql = (
        "SELECT symbols.*, files.language, "
        f"? + {' + '.join(rank_terms)} AS match_rank "
        "FROM symbols JOIN files ON files.path=symbols.file_path "
        "WHERE " + " AND ".join(where) + _RANKED_ORDER
    )
    params = rank_params + where_params + [limit, offset]
    rows = conn.execute(sql, params).fetchall()
    return attach_callers(conn, [symbol_row_to_dict(row) for row in rows])


def _query_browse(
    conn: sqlite3.Connection,
    kind: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT symbols.*, files.language FROM symbols "
        "JOIN files ON files.path=symbols.file_path"
    )
    params: list[Any] = []
    if kind:
        sql += " WHERE symbols.kind=?"
        params.append(kind)
    sql += _BASE_ORDER
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()
    return attach_callers(conn, [symbol_row_to_dict(row) for row in rows])
