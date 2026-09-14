"""Installed, versioned recipes used by operator inventories."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path


@dataclass(frozen=True)
class Catalog:
    id: str
    document: dict
    digest: str
    service_templates: dict[str, dict]


def get_catalog(catalog_id: str) -> Catalog:
    if catalog_id != "dark-platform-baseline-v1.0":
        raise ValueError(f"unknown operator inventory catalog {catalog_id!r}; choose dark-platform-baseline-v1.0")
    path = Path(__file__).with_name("catalog_data") / f"{catalog_id}.json"
    content = path.read_bytes()
    payload = json.loads(content)
    if payload.get("id") != catalog_id or payload.get("version") != 1:
        raise ValueError(f"invalid installed catalogue {catalog_id!r}")
    document = payload.get("base")
    templates = payload.get("service_templates")
    required = {"besu-validator", "besu-rpc", "besu-observer", "ipfs-kubo", "ipfs-cluster"}
    if not isinstance(document, dict) or not isinstance(templates, dict) or set(templates) != required:
        raise ValueError(f"invalid installed catalogue {catalog_id!r}")
    return Catalog(catalog_id, document, sha256(content).hexdigest(), templates)
