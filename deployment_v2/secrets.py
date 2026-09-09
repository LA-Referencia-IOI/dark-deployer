"""Create explicit, host-local runtime secret material for deployment v2.

This module never reads values from an inventory.  The inventory only tells the
runner where an operator has provisioned each file.  `secrets-init` is an
opt-in helper for greenfield environments and writes only to its explicit
output directory.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from .model import DeploymentPlan


class SecretError(ValueError):
    """A secure secret output cannot be safely created."""


def _write_private(path: Path, value: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise SecretError(f"refusing to overwrite existing secret: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.chmod(path, 0o600)


def initialize_greenfield_secrets(plan: DeploymentPlan, output: Path, *, overwrite: bool = False) -> list[Path]:
    """Create only secrets that can be safely generated without chain identity.

    A master wallet and application signing credentials deliberately remain an
    operator precondition.  Replacing either automatically could orphan a
    running chain or make already-issued ARKs unmanageable.
    """
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise SecretError(f"secret output must be empty or use --overwrite: {output}")
    if not plan.raw["secrets"]:
        raise SecretError("inventory declares no secret references")
    created: list[Path] = []
    known = plan.raw["secrets"]
    db_id = "minter-db-password"
    runtime_id = "minter-runtime-env"
    if db_id in known:
        password = secrets.token_urlsafe(32)
        path = output / known[db_id]["path"]
        _write_private(path, password + "\n", overwrite=overwrite)
        created.append(path)
        if runtime_id in known:
            runtime = output / known[runtime_id]["path"]
            _write_private(runtime, f"DATABASE_URL=postgresql://dark:{password}@postgres/minter\n", overwrite=overwrite)
            created.append(runtime)
    dashboard_id = "dashboard-runtime-env"
    if dashboard_id in known:
        password = secrets.token_urlsafe(32)
        app_key = "base64:" + __import__("base64").b64encode(secrets.token_bytes(32)).decode("ascii")
        dashboard_runtime = output / known[dashboard_id]["path"]
        _write_private(
            dashboard_runtime,
            "\n".join((
                f"APP_KEY={app_key}",
                f"DB_PASSWORD={password}",
                f"MYSQL_PASSWORD={password}",
                f"MYSQL_ROOT_PASSWORD={secrets.token_urlsafe(32)}",
                "REDIS_PASSWORD=null",
                "",
            )),
            overwrite=overwrite,
        )
        created.append(dashboard_runtime)
    if "ipfs-swarm-key" in known:
        path = output / known["ipfs-swarm-key"]["path"]
        _write_private(path, "/key/swarm/psk/1.0.0/\n/base16/\n" + secrets.token_hex(32) + "\n", overwrite=overwrite)
        created.append(path)
    if "ipfs-cluster-secret" in known:
        path = output / known["ipfs-cluster-secret"]["path"]
        _write_private(path, secrets.token_hex(32) + "\n", overwrite=overwrite)
        created.append(path)
    return created
