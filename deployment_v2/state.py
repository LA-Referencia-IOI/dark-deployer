"""Atomic local journal for deployment v2 runs; it never stores secret values."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def run_root(project_root: Path, deployment_id: str) -> Path:
    return project_root / ".generated" / "deployment-v2" / deployment_id


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
