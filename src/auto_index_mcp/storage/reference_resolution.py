"""Resolve only the evidence a lexical parser actually established."""
from pathlib import PurePosixPath


def resolve(edge: dict, definitions: list[dict]) -> tuple[list[dict], str, str]:
    local = [row for row in definitions if row["file_path"] == edge["source_file"]]
    qualifier = edge["qualifier"]
    if edge["evidence"] == "unresolved-receiver":
        methods = [row for row in local or definitions if row["kind"] == "method"]
        return methods or local or definitions, "ambiguous", "low"
    if qualifier:
        exact = [row for row in local if row["nesting_path"] == qualifier]
        if not exact:
            exact = [row for row in definitions if _qualified_match(row, qualifier)]
        if not exact:
            if edge["evidence"] in ("self-receiver", "module-qualified"):
                methods = [row for row in definitions if row["kind"] == "method"]
                # Imported instances and inherited members require type analysis.
                # Keep potential members as uncertain evidence, never a resolved call.
                if methods:
                    return methods, "ambiguous", "low"
            return [], "unresolved", "low"
        return exact, "resolved" if len(exact) == 1 else "ambiguous", "high"
    candidates = local or definitions
    return candidates, "resolved" if len(candidates) == 1 else "ambiguous", "medium"


def _qualified_match(symbol: dict, qualifier: str) -> bool:
    scope = symbol["nesting_path"] or symbol["name"]
    module = str(PurePosixPath(symbol["file_path"]).with_suffix("")).replace("/", ".")
    module = module.removesuffix(".__init__")
    return qualifier == f"{module}.{scope}" or f"{module}.{scope}".endswith("." + qualifier)


def symbol_key(symbol: dict) -> tuple:
    return symbol["file_path"], symbol["name"], symbol["line"]


def caller_label(edge: dict, target_path: str) -> str:
    name = edge["source_scope"] or edge["source_name"]
    return name if edge["source_file"] == target_path else f"{edge['source_file']}::{name}"
