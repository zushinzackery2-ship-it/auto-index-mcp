"""Go type declarations and methods, including generic receiver types."""
import re

from ..domain.models import SymbolRecord
from .braces import BracePairs

FUNCTION = re.compile(r"^\s*func\s+(?:\(\s*\w+\s+\*?([\w.]+)(?:\[[^]]*\])?\s*\)\s*)?(\w+)\s*(?:\[[^]]*\])?\s*\(")
TYPE = re.compile(r"^\s*type\s+(\w+)\s*(?:\[[^]]*\])?\s+(struct|interface)\b")


def extract_go_symbols(lines: list[str], cleaned: list[str]) -> list[SymbolRecord]:
    pairs = BracePairs(cleaned)
    records = []
    for index, line in enumerate(cleaned):
        function = FUNCTION.match(line)
        declaration = TYPE.match(line)
        if function:
            receiver, name = function.groups()
            kind = "method" if receiver else "function"
        elif declaration:
            name, kind = declaration.groups()
            receiver = ""
        else:
            continue
        records.append(SymbolRecord(name, kind, index + 1, pairs.end_line(index), lines[index].strip(),
                                    parent_name=receiver or "", parent_kind="struct" if receiver else "",
                                    nesting_path=f"{receiver}.{name}" if receiver else name))
    return records
