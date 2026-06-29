from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .ignore_config import IgnoreConfig
from .ignore_rules import ignore_fingerprint


class RebuildIgnoreState(Protocol):
    def ignore_config(self) -> IgnoreConfig: ...


def service_ignore_fingerprint(service: RebuildIgnoreState, root: Path) -> str:
    return config_ignore_fingerprint(service.ignore_config(), root)


def config_ignore_fingerprint(config: IgnoreConfig, root: Path) -> str:
    return ignore_fingerprint(
        root,
        config.patterns,
        config.auto_patterns,
        config.privileged_patterns,
    )


def config_ignore_metadata(config: IgnoreConfig, root: Path) -> dict[str, Any]:
    return {
        "ignore_fingerprint": config_ignore_fingerprint(config, root),
        **config.to_metadata(),
    }
