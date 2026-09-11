"""Create explicit, host-local runtime secret material for deployment v2.

This module never reads values from an inventory.  The inventory only tells the
runner where an operator has provisioned each file.  `secrets-init` is an
opt-in helper for greenfield environments and writes only to its explicit
output directory.
"""

from __future__ import annotations

import os
import secrets
import hashlib
import shlex
from pathlib import Path

from .model import DeploymentPlan
from .executor import LocalExecutor, SshExecutor, resolve_executor


class SecretError(ValueError):
    """A secure secret output cannot be safely created."""


def _source(project_root: Path, definition: dict, secret_id: str) -> Path:
    value = definition.get("source")
    if not value:
        raise SecretError(f"secret {secret_id} has no controller source")
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribute_secrets(plan: DeploymentPlan, project_root: Path, *, local_roots: dict[str, Path] | None = None) -> dict[str, dict[str, str]]:
    """Copy only declared secret files to the host of each consumer.

    Secrets never enter the public render bundle. A post-copy SHA-256 check is
    performed over SSH; only digests are returned for the public journal.
    """
    result: dict[str, dict[str, str]] = {}
    local_roots = local_roots or {}
    for secret_id, definition in plan.raw["secrets"].items():
        consumers = definition.get("consumers", [])
        if not consumers:
            continue
        if not definition.get("source"):
            if any(plan.machine(machine_id).execution != "local" for machine_id in {plan.service(service_id).machine_id for service_id in consumers}):
                raise SecretError(f"secret {secret_id} needs a controller source for SSH consumers")
            continue
        source = _source(project_root, definition, secret_id)
        if not source.is_file():
            raise SecretError(f"secret source for {secret_id} is not a readable file: {source}")
        if source.stat().st_mode & 0o077:
            raise SecretError(f"secret source for {secret_id} must not be group/world accessible: {source}")
        mode = str(definition.get("mode", "0600"))
        if mode != "0600":
            raise SecretError(f"secret {secret_id} mode must be 0600")
        digest = _digest(source)
        targets = {plan.service(service_id).machine_id for service_id in consumers}
        for machine_id in targets:
            machine = plan.machine(machine_id)
            target_root = local_roots.get(machine_id, Path(machine.secrets_root))
            target = target_root / definition["path"]
            executor = resolve_executor(machine)
            if isinstance(executor, LocalExecutor):
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or _digest(target) != digest:
                    target.write_bytes(source.read_bytes())
                target.chmod(0o600)
            else:
                parent = str(target.parent)
                prepared = executor.run(("mkdir", "-p", parent), timeout=30)
                if prepared.returncode:
                    raise SecretError(f"cannot create secret directory on {machine_id}: {prepared.stderr.strip()}")
                copied = executor.transfer(source, parent, timeout=300)
                if copied.returncode:
                    raise SecretError(f"cannot copy {secret_id} to {machine_id}: {copied.stderr.strip()}")
                staged = Path(parent) / source.name
                quoted_parent, quoted_staged, quoted_target = map(shlex.quote, (parent, str(staged), str(target)))
                command = (
                    f"chmod 700 {quoted_parent} && "
                    f"if test {quoted_staged} != {quoted_target}; then mv {quoted_staged} {quoted_target}; fi && "
                    f"chmod 600 {quoted_target} && sha256sum {quoted_target} | awk '{{print $1}}'"
                )
                remote_hash = executor.run(("sh", "-lc", command), timeout=30)
                if remote_hash.returncode or remote_hash.stdout.strip() != digest:
                    raise SecretError(f"hash verification failed for {secret_id} on {machine_id}")
            result.setdefault(machine_id, {})[secret_id] = digest
    return result


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
            _write_private(runtime, "\n".join((
                f"DATABASE_URL=postgresql://dark:{password}@minter-postgres:5432/minter",
                "POSTGRES_USER=dark",
                "POSTGRES_DB=minter",
                f"POSTGRES_PASSWORD={password}",
                "",
            )), overwrite=overwrite)
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
