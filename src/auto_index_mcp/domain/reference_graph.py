"""Project reference evidence independently of its storage partition."""
from collections import defaultdict

from .reference_resolution import caller_label, resolve, symbol_key


def project_references(symbols, definitions, edges):
    by_name = defaultdict(list)
    for definition in definitions:
        by_name[definition["name"]].append(definition)
    projected = defaultdict(lambda: dict(called_by=[], ambiguous_called_by=[], imported_by=[], caller_evidence=[]))
    edges = sorted(edges, key=lambda edge: (edge["source_file"].casefold(), edge["source_line"],
                                           edge["source_name"], edge["qualifier"], edge["usage"]))
    for edge in edges:
        candidates, resolution, confidence = resolve(edge, by_name[edge["target"]])
        for symbol in candidates:
            if (edge["source_file"], edge["source_line"]) == (symbol["file_path"], symbol["line"]):
                continue
            result = projected[symbol_key(symbol)]
            label = caller_label(edge, symbol["file_path"])
            field = "imported_by" if edge["usage"] == "import" else (
                "called_by" if resolution == "resolved" else "ambiguous_called_by")
            result[field].append(label)
            result["caller_evidence"].append(dict(caller=label, source_line=edge["source_line"],
                qualifier=edge["qualifier"], usage=edge["usage"], resolution=resolution, confidence=confidence))
    for symbol in symbols:
        result = projected[symbol_key(symbol)]
        symbol.update((field, values if field == "caller_evidence" else list(dict.fromkeys(values)))
                      for field, values in result.items())
    return symbols
