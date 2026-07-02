from __future__ import annotations

from typing import Any

from .quality_dangling import dangling_report
from .quality_nesting import nesting_report
from .path_filters import filter_indexed_files
from .service_state import ServiceBase


class ServiceQualityMixin(ServiceBase):
    def nesting_check(
        self,
        max_depth: int = 4,
        languages: list[str] | None = None,
        limit: int = 200,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        self._require_ready()
        _validate_quality_limit(limit)
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        files = filter_indexed_files(self.view.all_files(), exclude_paths, active_only)
        return self._with_index_status(nesting_report(files, max_depth, languages, limit))

    def dangling_check(
        self,
        include_low_confidence: bool = False,
        include_tests: bool = False,
        limit: int = 200,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        self._require_ready()
        _validate_quality_limit(limit)
        files = filter_indexed_files(self.view.all_files(), exclude_paths, active_only)
        findings = [finding for item in files for finding in item.get("quality_findings", [])]
        return self._with_index_status(dangling_report(files, findings, include_low_confidence, include_tests, limit))


def _validate_quality_limit(limit: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
