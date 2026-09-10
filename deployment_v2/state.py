"""Atomic local journal for deployment v2 runs; it never stores secret values."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fcntl


class StateLockError(RuntimeError):
    """Another controller already owns this local deployment run."""


def run_root(project_root: Path, deployment_id: str) -> Path:
    return project_root / ".generated" / "deployment-v2" / deployment_id


@contextmanager
def deployment_lock(root: Path):
    """Prevent two local controller processes from mutating one run journal.

    Remote host serialization is still delegated to Docker/Compose and remains
    a separate operational concern; this lock protects the controller's
    generated bundle, state file and resume decisions.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".controller.lock"
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateLockError(f"deployment run is already controlled: {root}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record(root: Path, event: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "journal.jsonl"
    sanitized = {key: value for key, value in event.items() if "secret" not in key.lower() and "password" not in key.lower() and "key" not in key.lower()}
    sanitized["at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sanitized, sort_keys=True) + "\n")


def write_status(root: Path, status: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "status.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".status-", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, target)
    finally:
        if Path(temporary_name).exists():
            Path(temporary_name).unlink()
