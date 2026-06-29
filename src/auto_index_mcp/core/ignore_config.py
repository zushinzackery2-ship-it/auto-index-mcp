from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .ignore_rules import IgnoreRule

RUNTIME_IGNORE_METADATA_KEY = "runtime_ignore_patterns"
AUTO_IGNORE_METADATA_KEY = "auto_ignore_patterns"
PRIVILEGED_IGNORE_METADATA_KEY = "privileged_ignore_patterns"
AUTO_IGNORE_DONE_METADATA_KEY = "auto_ignore_done"


@dataclass(frozen=True)
class IgnoreConfig:
    patterns: list[str] = field(default_factory=list)
    auto_patterns: list[str] = field(default_factory=list)
    privileged_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "IgnoreConfig":
        return cls(
            patterns=clean_patterns(_metadata_list(metadata, RUNTIME_IGNORE_METADATA_KEY)),
            auto_patterns=clean_patterns(_metadata_list(metadata, AUTO_IGNORE_METADATA_KEY)),
            privileged_patterns=clean_patterns(_metadata_list(metadata, PRIVILEGED_IGNORE_METADATA_KEY)),
        )

    @staticmethod
    def has_metadata(metadata: dict[str, Any]) -> bool:
        return any(
            key in metadata
            for key in (
                RUNTIME_IGNORE_METADATA_KEY,
                AUTO_IGNORE_METADATA_KEY,
                PRIVILEGED_IGNORE_METADATA_KEY,
            )
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            RUNTIME_IGNORE_METADATA_KEY: self.patterns,
            AUTO_IGNORE_METADATA_KEY: self.auto_patterns,
            PRIVILEGED_IGNORE_METADATA_KEY: self.privileged_patterns,
        }

    def with_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return replace(self, patterns=clean_patterns(patterns))

    def with_auto_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return replace(self, auto_patterns=clean_patterns(patterns))

    def with_privileged_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return replace(self, privileged_patterns=clean_patterns(patterns))

    def with_added_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return self.with_patterns(merge_patterns(self.patterns, patterns))

    def with_added_auto_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return self.with_auto_patterns(merge_patterns(self.auto_patterns, patterns))

    def with_added_privileged_patterns(self, patterns: list[str]) -> "IgnoreConfig":
        return self.with_privileged_patterns(merge_patterns(self.privileged_patterns, patterns))

    def without_auto_patterns(self) -> "IgnoreConfig":
        return replace(self, auto_patterns=[])


def clean_patterns(patterns: list[str] | None) -> list[str]:
    return [pattern.strip() for pattern in patterns or [] if pattern and pattern.strip()]


def merge_patterns(existing: list[str], additions: list[str] | None) -> list[str]:
    merged = list(existing)
    for pattern in clean_patterns(additions):
        if pattern not in merged:
            merged.append(pattern)
    return merged


def matches_patterns(patterns: list[str], rel_path: str, is_dir: bool = False) -> bool:
    matched = False
    for pattern in patterns:
        rule = IgnoreRule.parse(pattern)
        if rule is not None and rule.matches(rel_path, is_dir):
            matched = not rule.negated
    return matched


def exact_path_pattern(rel_path: str) -> str:
    return "/" + _escape_glob_path(rel_path.replace("\\", "/").strip("/"))


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _escape_glob_path(rel_path: str) -> str:
    return "".join(_escape_glob_char(char) for char in rel_path)


def _escape_glob_char(char: str) -> str:
    if char == "[":
        return "[[]"
    if char == "]":
        return "[]]"
    if char == "*":
        return "[*]"
    if char == "?":
        return "[?]"
    return char
