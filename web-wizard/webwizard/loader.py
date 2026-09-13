"""Read-only access to ``deployment_v3`` for the workbench.

The workbench treats the deployer as a library. This module is the single place
that imports it, so the read-only guarantee is easy to audit: nothing here
imports ``deployment_v3.runner`` and nothing performs an operational action.

It exposes only the resolver and the planner — the deployer's own validation
gate. The workbench's editing model (draft session, operations, undo) lives in
``session.py`` and ``operations.py`` and is its own.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_WEBWIZZARD_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = _WEBWIZZARD_DIR.parent  # web-wizard/
REPO_ROOT = PACKAGE_ROOT.parent  # dark-deployer checkout root


class LoaderError(RuntimeError):
    """The workbench could not read or resolve the inventory."""


@dataclass(frozen=True)
class Core:
    """The deployer's read-only entry points, imported once."""

    build_plan: Callable[[Path], Any]
    analyze: Callable[[Any], Any]
    resolve_inventory: Callable[..., Any]
    validate_inventory: Callable[[dict], Any]


_CORE: Core | None = None


def core() -> Core:
    global _CORE
    if _CORE is None:
        _CORE = _load_core()
    return _CORE


def _load_core() -> Core:
    if (REPO_ROOT / "deployment_v3").is_dir() and str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from deployment_v3.availability import analyze
        from deployment_v3.inventory import validate_inventory
        from deployment_v3.inventory_resolver import resolve_inventory
        from deployment_v3.planner import build_plan
    except ImportError as exc:  # pragma: no cover - environment failure
        raise LoaderError(
            "deployment_v3 is not importable; run web-wizard from inside the "
            f"dark-deployer checkout (looked in {REPO_ROOT})"
        ) from exc
    return Core(build_plan, analyze, resolve_inventory, validate_inventory)


def example_directory() -> Path:
    """Directory holding the maintained operator inventories."""
    return REPO_ROOT / "examples" / "operator-inventory"


def list_inventories() -> list[dict[str, str]]:
    """List the maintained examples, read-only."""
    directory = example_directory()
    if not directory.is_dir():
        return []
    return [
        {"name": path.name, "path": str(path)}
        for path in sorted(directory.glob("*.json"))
    ]


def resolve_inventory_path(path: str | Path) -> Path:
    """Turn a possibly-relative inventory argument into a concrete path."""
    target = Path(path).expanduser()
    if not target.is_absolute():
        # Accept "examples/operator-inventory/foo.json" regardless of where the
        # server was launched from.
        candidates = [Path.cwd() / target, REPO_ROOT / target]
        target = next((item for item in candidates if item.is_file()), candidates[0])
    return target


def read_inventory(path: str | Path) -> tuple[Path, dict]:
    """Read a compact operator inventory without touching the deployer."""
    target = resolve_inventory_path(path)
    if not target.is_file():
        raise LoaderError(f"inventory not found: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderError(f"invalid JSON in {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LoaderError("inventory root must be a JSON object")
    return target, raw


def load_topology(path: str | Path):
    """Resolve an inventory read-only and return its canvas graph."""
    from .graph import build_graph

    target, source = read_inventory(path)
    try:
        plan = core().build_plan(target)
        availability = core().analyze(plan)
    except Exception as exc:  # InventoryError and friends surface as messages
        raise LoaderError(str(exc)) from exc

    warnings = list(availability.warnings)
    if source.get("format") == "dark-operator-inventory":
        try:
            resolution = core().resolve_inventory(source, source_path=target)
        except Exception:
            resolution = None
        if resolution is not None:
            for warning in resolution.warnings:
                if warning not in warnings:
                    warnings.append(warning)

    return build_graph(plan, availability, source, warnings, inventory_path=str(target))
