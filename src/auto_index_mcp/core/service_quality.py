from __future__ import annotations

from typing import Any, Protocol, cast

from .quality_dangling import dangling_report
from .quality_nesting import nesting_report
from ..workspace.view import WorkspaceView


class _QualityService(Protocol):
    @property
    def view(self) -> WorkspaceView:
        ...

    def _require_ready(self) -> None:
        ...


class ServiceQualityMixin:
    def nesting_check(
        self,
        max_depth: int = 4,
        languages: list[str] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        service = cast(_QualityService, self)
        service._require_ready()
        _validate_quality_limit(limit)
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        return nesting_report(service.view.all_files(), max_depth, languages, limit)

    def dangling_check(
        self,
        include_low_confidence: bool = False,
        include_tests: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        service = cast(_QualityService, self)
        service._require_ready()
        _validate_quality_limit(limit)
        files = service.view.all_files()
        findings = [finding for item in files for finding in item.get("quality_findings", [])]
        return dangling_report(files, findings, include_low_confidence, include_tests, limit)


def _validate_quality_limit(limit: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
