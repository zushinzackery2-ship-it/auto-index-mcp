"""Resolve only the evidence a lexical parser actually established."""
from pathlib import PurePosixPath


def resolve(edge: dict, definitions: list[dict]) -> tuple[list[dict], str, str]:
    local = [row for row in definitions if row["file_path"] == edge["source_file"]]
    qualifier = edge["qualifier"]
    relative = qualifier.startswith(".")
    if relative:
        level = len(qualifier) - len(qualifier.lstrip("."))
        package = list(PurePosixPath(edge["source_file"]).parent.parts)
        if level > len(package):
            return [], "unresolved", "low"
        qualifier = ".".join(package[:len(package) - level + 1] + [qualifier[level:]])
    if edge["evidence"] == "unresolved-receiver":
        methods = [row for row in local or definitions if row["kind"] == "method"]
        return methods or local or definitions, "ambiguous", "low"
    if qualifier:
        exact = [row for row in local if row["nesting_path"] == qualifier]
        if not exact:
            exact = [row for row in definitions if _qualified_match(row, qualifier, relative)]
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


def _qualified_match(symbol: dict, qualifier: str, exact=False) -> bool:
    scope = symbol["nesting_path"] or symbol["name"]
    module = str(PurePosixPath(symbol["file_path"]).with_suffix("")).replace("/", ".")
    module = module.removesuffix(".__init__")
    identity = f"{module}.{scope}"
    return qualifier == identity or (not exact and identity.endswith("." + qualifier))


def symbol_key(symbol: dict) -> tuple:
    return symbol["file_path"], symbol["name"], symbol["line"]


def caller_label(edge: dict, target_path: str) -> str:
    name = edge["source_scope"] or edge["source_name"]
    return name if edge["source_file"] == target_path else f"{edge['source_file']}::{name}"
