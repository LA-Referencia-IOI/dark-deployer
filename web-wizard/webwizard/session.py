"""The workbench's draft session: a transactional, in-memory operator inventory.

A change is committed only when the whole compact inventory still resolves into
a valid v3 plan — the deployer's resolver and planner are used read-only as the
gate, and they are the only thing borrowed from the deployer. A rejected change
leaves the previous valid draft untouched and reports why.

Nothing here writes to the inventory file. The draft lives in memory; positions
and the arrangement are view state. Saving is a later phase.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .graph import build_graph
from .loader import LoaderError, core, read_inventory

OPERATOR_FORMAT = "dark-operator-inventory"
SECTION_ORDER = (
    "deployment", "profile", "availability", "defaults", "networks", "routes",
    "machines", "placement", "routing", "networking", "blockchain", "storage",
    "proxies", "secrets", "overrides",
)


class SessionError(RuntimeError):
    """The document cannot be opened or is not editable."""


def _pretty(message: str) -> str:
    return (
        str(message)
        .removeprefix("operator inventory: ")
        .removeprefix("deployment topology v3: ")
    )


@dataclass
class DraftSession:
    """One operator inventory being edited, with its last valid draft retained."""

    path: Path
    original: dict[str, Any]
    raw: dict[str, Any]
    resolution: Any = None
    stage: str = "loaded"
    last_error: str | None = None
    undo_stack: list[dict[str, Any]] = field(default_factory=list)
    saved_path: Path | None = None
    original_bytes: bytes = b""
    _suggestion: Path | None = None

    # -- lifecycle ---------------------------------------------------
    @classmethod
    def open(cls, path: str | Path) -> "DraftSession":
        try:
            target, raw = read_inventory(path)
        except LoaderError as exc:
            raise SessionError(str(exc)) from exc
        if raw.get("format") != OPERATOR_FORMAT:
            raise SessionError(
                "only a compact 'dark-operator-inventory' document can be edited "
                "here; a full v3 inventory is not editable in the workbench"
            )
        session = cls(
            path=target,
            original=deepcopy(raw),
            raw=deepcopy(raw),
            original_bytes=target.read_bytes(),
        )
        try:
            session.resolution = session._resolve(session.raw)
        except Exception as exc:
            raise SessionError(_pretty(exc)) from exc
        return session

    def _resolve(self, raw: dict[str, Any]):
        return core().resolve_inventory(raw, source_path=self.path)

    # -- editing -----------------------------------------------------
    def apply(self, change: Callable[[dict[str, Any]], None], *, stage: str) -> bool:
        """Commit a change only if the whole compact contract stays valid."""
        candidate = deepcopy(self.raw)
        try:
            change(candidate)
            resolution = self._resolve(candidate)
        except Exception as exc:
            self.last_error = _pretty(exc)
            return False
        self.undo_stack.append(deepcopy(self.raw))
        self.raw = candidate
        self.resolution = resolution
        self.stage = stage
        self.last_error = None
        return True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.raw = self.undo_stack.pop()
        self.resolution = self._resolve(self.raw)
        self.last_error = None
        self.stage = "undo"
        return True

    def reset(self) -> bool:
        self.undo_stack.clear()
        self.raw = deepcopy(self.original)
        self.resolution = self._resolve(self.raw)
        self.last_error = None
        self.stage = "reset"
        return True

    # -- introspection -----------------------------------------------
    @property
    def changed(self) -> bool:
        return self.raw != self.original

    @property
    def changed_sections(self) -> list[str]:
        keys = [
            key for key in set(self.original) | set(self.raw)
            if self.original.get(key) != self.raw.get(key)
        ]
        return sorted(keys, key=lambda key: (SECTION_ORDER.index(key) if key in SECTION_ORDER else 99, key))

    @property
    def warnings(self) -> list[str]:
        return list(self.resolution.warnings) if self.resolution else []

    def plan(self):
        """Build a real DeploymentPlan from the resolved draft.

        ``build_plan`` is path-based; the resolved document goes through a
        private temporary file, exactly as an offline preview. The inventory
        file itself is never touched.
        """
        resolved = self.resolution.document
        with tempfile.TemporaryDirectory(prefix="web-wizard-view-") as directory:
            candidate = Path(directory) / "resolved.json"
            candidate.write_text(json.dumps(resolved), encoding="utf-8")
            return core().build_plan(candidate)

    def view(self) -> dict[str, Any]:
        plan = self.plan()
        availability = core().analyze(plan)
        graph = build_graph(
            plan, availability, self.raw, self.warnings, inventory_path=str(self.path)
        )
        return graph.to_dict()

    # -- saving ------------------------------------------------------
    def suggested_destination(self) -> Path:
        """A new sibling file name, stable for the lifetime of the session."""
        if self._suggestion is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            self._suggestion = self.path.with_name(f"{self.path.stem}.wizard-{stamp}.json")
        return self._suggestion

    def save(self, destination: str | Path | None = None) -> Path:
        """Write the draft to a **new** file. Never overwrites anything.

        The draft is the loaded document plus the edits, so every section the
        canvas does not touch is carried over verbatim; the file is written with
        the same formatting convention the deployer uses.
        """
        if destination is None or (isinstance(destination, str) and not destination.strip()):
            target = self.suggested_destination()
        else:
            target = Path(destination).expanduser()
            if not target.is_absolute():
                # A bare name lands next to the inventory being edited.
                target = self.path.parent / target
            target = target.resolve()

        if target == self.path:
            raise SessionError("refusing to overwrite the inventory being edited; choose a new name")
        if target.is_dir():
            raise SessionError(f"that path is a directory: {target}")
        if not target.parent.is_dir():
            raise SessionError(f"the destination directory does not exist: {target.parent}")

        # Never write something the deployer could not read back.
        self.resolution = self._resolve(self.raw)

        if self.changed or not self.original_bytes:
            # Key order is preserved **on purpose** and must never be sorted
            # here. Instance names are positional: validators and observers are
            # numbered `validator01…NN` / `observer01…NN` in the declaration
            # order of `blockchain.{validator,observer}_groups`, so sorting those
            # keys silently moves an instance to another group — and anything
            # referencing it by name (an RPC binding) starts pointing at a
            # different machine.
            data = (json.dumps(self.raw, indent=2) + "\n").encode("utf-8")
        else:
            # An unmodified inventory is copied byte for byte.
            data = self.original_bytes

        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError as exc:
            raise SessionError(f"refusing to overwrite an existing file: {target}") from exc
        except OSError as exc:
            raise SessionError(f"cannot write {target}: {exc}") from exc

        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise SessionError(f"could not finish writing {target}: {exc}") from exc

        self.saved_path = target
        self.last_error = None
        return target

    def state(self, *, ok: bool = True) -> dict[str, Any]:
        return {
            "ok": ok,
            "error": self.last_error,
            "draft": {
                "path": str(self.path),
                "changed": self.changed,
                "sections": self.changed_sections,
                "can_undo": bool(self.undo_stack),
                "stage": self.stage,
                # The loaded inventory is never written to, by construction.
                "written": False,
                "saved_path": str(self.saved_path) if self.saved_path else None,
                "suggested_path": str(self.suggested_destination()),
            },
            "graph": self.view(),
        }
