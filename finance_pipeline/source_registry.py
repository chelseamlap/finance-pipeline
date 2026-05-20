from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from .normalize import load_yaml


def registry() -> dict[str, Any]:
    return load_yaml("source_registry.yaml").get("sources", {})


def reconciliation_config() -> dict[str, Any]:
    return load_yaml("source_registry.yaml").get("reconciliation", {})


def load_source(source: str, path: Path, import_batch_id: str, store: str | None = None):
    sources = registry()
    if source not in sources:
        raise ValueError(f"Unknown source: {source}")
    module = import_module(sources[source]["loader"])
    return module.load(path=path, import_batch_id=import_batch_id, store=store)
