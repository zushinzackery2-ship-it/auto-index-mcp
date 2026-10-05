"""Qualified unresolved edges are projected with explicit evidence strength."""
from __future__ import annotations

from collections import defaultdict
from .reference_resolution import caller_label, resolve, symbol_key


def insert_edges(conn, records) -> None:
    for record in records:
        rows = _edge_rows(record.path, "<module>", 0, "", record.module_refs, record.module_reference_evidence)
        for symbol in record.symbols:
            rows.extend(_edge_rows(record.path, symbol.name, symbol.line, symbol.nesting_path,
                                   symbol.calls + symbol.refs, symbol.reference_evidence))
        conn.executemany("INSERT OR IGNORE INTO symbol_references VALUES (?,?,?,?,?,?,?,?)", rows)


def _edge_rows(path, name, line, scope, targets, evidence):
    qualified_targets = {edge["target"] for edge in evidence}
    edges = list(evidence) + [dict(target=target, qualifier="", evidence="name-only", usage="reference")
                             for target in dict.fromkeys(targets) if target not in qualified_targets]
    return [(path, name, line, edge["target"], edge["qualifier"], edge["evidence"], scope, edge["usage"])
            for edge in edges]


def attach_callers(conn, symbols: list[dict]) -> list[dict]:
    names = list(dict.fromkeys(symbol["name"] for symbol in symbols))
    resolved = defaultdict(list)
    ambiguous = defaultdict(list)
    evidence = defaultdict(list)
    imported = defaultdict(list)
    for start in range(0, len(names), 400):
        _project_batch(conn, names[start:start + 400], resolved, ambiguous, evidence, imported)
    for symbol in symbols:
        key = symbol_key(symbol)
        symbol["called_by"] = list(dict.fromkeys(resolved[key]))
        symbol["ambiguous_called_by"] = list(dict.fromkeys(ambiguous[key]))
        symbol["imported_by"] = list(dict.fromkeys(imported[key]))
        symbol["caller_evidence"] = evidence[key]
    return symbols


def _project_batch(conn, names, resolved, ambiguous, evidence, imported) -> None:
    marks = ",".join("?" for _ in names)
    definitions = defaultdict(list)
    for row in conn.execute(f"SELECT file_path,name,line,nesting_path,kind FROM symbols WHERE name IN ({marks})", names):
        definitions[row["name"]].append(dict(row))
    edges = conn.execute(f"SELECT * FROM symbol_references WHERE target IN ({marks}) "
                         "ORDER BY source_file COLLATE NOCASE,source_line,source_name,qualifier,usage", names)
    for row in edges:
        edge = dict(row)
        candidates, resolution, confidence = resolve(edge, definitions[edge["target"]])
        _attach_edge(edge, candidates, resolution, confidence, resolved, ambiguous, evidence, imported)


def _attach_edge(edge, candidates, resolution, confidence, resolved, ambiguous, evidence, imported) -> None:
    for symbol in candidates:
        if (edge["source_file"], edge["source_line"]) == (symbol["file_path"], symbol["line"]):
            continue
        key = symbol_key(symbol)
        label = caller_label(edge, symbol["file_path"])
        if edge["usage"] == "import":
            imported[key].append(label)
        else:
            (resolved if resolution == "resolved" else ambiguous)[key].append(label)
        evidence[key].append(dict(caller=label, source_line=edge["source_line"], qualifier=edge["qualifier"],
                                  usage=edge["usage"], resolution=resolution, confidence=confidence))
