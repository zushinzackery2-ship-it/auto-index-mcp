from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..domain.ignore_config import IgnoreConfig
from ..domain.ignore_rules import IgnoreRules, ignore_fingerprint


if TYPE_CHECKING:
    from .context import ProjectContext


def service_ignore_fingerprint(service: ProjectContext, root: Path) -> str:
    store = service.store
    metadata = store.get_metadata_map() if store is not None else {}
    return config_ignore_fingerprint(service._ignore_config, root, metadata.get("ignore_files", [".gitignore"]))


def config_ignore_fingerprint(config: IgnoreConfig, root: Path, known_paths=None) -> str:
    return ignore_fingerprint(
        root,
        config.patterns,
        config.auto_patterns,
        config.privileged_patterns,
        known_paths,
    )


def config_ignore_metadata(config: IgnoreConfig, root: Path) -> dict[str, Any]:
    paths = IgnoreRules.from_root(root, config.patterns).ignore_files()
    return {
        "ignore_fingerprint": config_ignore_fingerprint(config, root, paths),
        "ignore_files": paths,
        **config.to_metadata(),
    }
