"""Toolkit-neutral inventory editing and safe persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any

from ..inventory import InventoryError, load_inventory


class InventoryDocumentError(ValueError):
    """The editable JSON document cannot be loaded or changed safely."""


@dataclass
class InventoryDocument:
    """An in-memory topology document with validation and atomic saving."""

    path: Path
    raw: dict[str, Any]
    original: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "InventoryDocument":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InventoryDocumentError(f"inventory not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise InventoryDocumentError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise InventoryDocumentError("inventory root must be a JSON object")
        return cls(path=path, raw=raw, original=deepcopy(raw))

    @property
    def changed(self) -> bool:
        return self.raw != self.original

    def section_text(self, section: str) -> str:
        if section not in self.raw:
            raise InventoryDocumentError(f"unknown inventory section: {section}")
        return json.dumps(self.raw[section], indent=2, sort_keys=True) + "\n"

    def replace_section_text(self, section: str, text: str) -> None:
        if section not in self.raw:
            raise InventoryDocumentError(f"unknown inventory section: {section}")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InventoryDocumentError(f"{section}: invalid JSON: {exc.msg} (line {exc.lineno})") from exc
        candidate = deepcopy(self.raw)
        candidate[section] = value
        self.validate(candidate)
        self.raw = candidate

    def validate(self, raw: dict[str, Any] | None = None) -> None:
        """Run the canonical inventory loader without persisting the candidate."""
        candidate = self.raw if raw is None else raw
        try:
            with tempfile.TemporaryDirectory(prefix="dark-inventory-check-") as temporary:
                candidate_path = Path(temporary) / "inventory.json"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                load_inventory(candidate_path)
        except InventoryError as exc:
            raise InventoryDocumentError(str(exc)) from exc

    def diff_sections(self) -> tuple[str, ...]:
        keys = sorted(set(self.original).union(self.raw))
        return tuple(key for key in keys if self.original.get(key) != self.raw.get(key))

    def save(self) -> Path | None:
        """Create a timestamped backup and atomically replace the inventory."""
        self.validate()
        if not self.changed:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if self.path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
            backup = self.path.with_name(f"{self.path.name}.bak-{stamp}")
            backup.write_bytes(self.path.read_bytes())
        rendered = json.dumps(self.raw, indent=2, sort_keys=True) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, prefix=f".{self.path.name}.", delete=False
        ) as temporary:
            temporary.write(rendered)
            temporary_path = Path(temporary.name)
        temporary_path.replace(self.path)
        self.original = deepcopy(self.raw)
        return backup
