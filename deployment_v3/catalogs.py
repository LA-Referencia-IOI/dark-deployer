"""Installed, versioned recipes used by operator inventories.

The first catalogue deliberately derives its recipe from the maintained HA
fixture.  This makes the compact format an authoring layer, not a second,
independent definition of dARK services.
"""

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


def get_catalog(catalog_id: str) -> Catalog:
    if catalog_id != "dark-standard-1":
        raise ValueError(f"unknown operator inventory catalog {catalog_id!r}; choose dark-standard-1")
    path = Path(__file__).resolve().parents[1] / "examples" / "deployment-v3" / "production-five-host.json"
    content = path.read_bytes()
    return Catalog(catalog_id, json.loads(content), sha256(content).hexdigest())
