"""Creation of editable inventories from maintained examples."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..inventory import load_inventory
from .document import InventoryDocumentError


_TEMPLATES = {
    "local-simple": "local-simple.json",
    "local-ha": "local-ha.json",
    "production-five-host": "production-five-host.json",
    "operator-local-simple": "../operator-inventory/local-simple.json",
    "operator-local-ha": "../operator-inventory/local-ha.json",
    "operator-production-five-host": "../operator-inventory/production-five-host.json",
}


def template_names() -> tuple[str, ...]:
    return tuple(_TEMPLATES)


def _examples_root() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "deployment-v3"


def create_from_template(template: str, output: Path, *, overwrite: bool = False) -> Path:
    try:
        source = _examples_root() / _TEMPLATES[template]
    except KeyError as exc:
        raise InventoryDocumentError(
            f"unknown template {template!r}; choose one of: {', '.join(template_names())}"
        ) from exc
    if output.exists() and not overwrite:
        raise InventoryDocumentError(f"refusing to overwrite existing inventory: {output}")
    load_inventory(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    return output
