from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticLine:
    severity: str
    path: str
    line: int
    character: int
    message: str
