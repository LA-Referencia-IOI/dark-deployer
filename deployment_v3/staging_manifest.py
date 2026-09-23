"""Resolve and materialize the public inputs required by one machine."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from .model import DeploymentPlan, Machine
from .sources import COMPONENT_PATHS


class StagingManifestError(RuntimeError):
    """A machine bundle cannot be assembled from the declared inputs."""


# Components are declared once per service type.  Dependencies shared by
# several APIs remain in COMPONENT_DEPENDENCIES rather than being repeated.
SERVICE_COMPONENTS = {
    "admin-api": frozenset({"dark-core-admin-api"}),
    "besu-observer": frozenset(),
    "besu-rpc": frozenset(),
    "besu-validator": frozenset(),
    "contracts-deploy": frozenset(),
    "dashboard": frozenset({"dashboard-web"}),
    "dashboard-migrate": frozenset({"dashboard-web"}),
    "dashboard-mysql": frozenset(),
    "edge-proxy": frozenset(),
    "explorer": frozenset({"dark-explorador"}),
    "ipfs-cluster": frozenset({"dark-ipfs"}),
    "ipfs-kubo": frozenset({"dark-ipfs"}),
    "minter-api": frozenset({"dark-core-minter-api"}),
    "minter-migrate": frozenset({"dark-core-minter-api"}),
    "minter-postgres": frozenset(),
    "minter-worker": frozenset({"dark-core-minter-api"}),
    "resolver-api": frozenset({"dark-core-resolver-api"}),
    "rpc-probe": frozenset(),
    "store-api": frozenset({"dark-store-api"}),
}

COMPONENT_DEPENDENCIES = {
    "dark-core-admin-api": frozenset({"dark-core-lib"}),
    "dark-core-minter-api": frozenset({"dark-core-lib"}),
    "dark-core-resolver-api": frozenset({"dark-core-lib"}),
}

SERVICE_RUNTIME_ASSETS = {
    "besu-observer": frozenset({"blockchain/config/besu-config.toml", "blockchain/scripts/besu-entrypoint.sh"}),
    "besu-rpc": frozenset({"blockchain/config/besu-config.toml", "blockchain/scripts/besu-entrypoint.sh"}),
    "besu-validator": frozenset({"blockchain/config/besu-config.toml", "blockchain/scripts/besu-entrypoint.sh"}),
    "contracts-deploy": frozenset({"deployment_v3/Dockerfile.contracts", "deployment_v3/contracts.py"}),
    "rpc-probe": frozenset({"deployment_v3/Dockerfile.contracts", "deployment_v3/contracts.py"}),
}

_IGNORED = frozenset({".git", ".env", ".env.integration", ".generated", ".DS_Store", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache"})


def _closure(component_id: str, stack: tuple[str, ...] = ()) -> frozenset[str]:
    if component_id in stack:
        raise StagingManifestError("component dependency cycle: " + " -> ".join((*stack, component_id)))
    dependencies = COMPONENT_DEPENDENCIES.get(component_id, frozenset())
    result = {component_id}
    for dependency in dependencies:
        result.update(_closure(dependency, (*stack, component_id)))
    return frozenset(result)


def machine_components(plan: DeploymentPlan, machine: Machine) -> frozenset[str]:
    result: set[str] = set()
    for service in (item for item in plan.services if item.machine_id == machine.id):
        try:
            direct = SERVICE_COMPONENTS[service.type]
        except KeyError as exc:
            raise StagingManifestError(f"service {service.id} has no component declaration for type {service.type}") from exc
        for component_id in direct:
            result.update(_closure(component_id))
    missing = sorted(component_id for component_id in result if component_id not in plan.raw["components"])
    if missing:
        raise StagingManifestError(f"machine {machine.id} requires undeclared components: {', '.join(missing)}")
    return frozenset(result)


def service_components(service_type: str) -> frozenset[str]:
    """Return the complete component closure required by one service type."""
    try:
        direct = SERVICE_COMPONENTS[service_type]
    except KeyError as exc:
        raise StagingManifestError(f"service type has no component declaration: {service_type}") from exc
    result: set[str] = set()
    for component_id in direct:
        result.update(_closure(component_id))
    return frozenset(result)


def machine_runtime_assets(plan: DeploymentPlan, machine: Machine) -> frozenset[str]:
    result: set[str] = set()
    for service in (item for item in plan.services if item.machine_id == machine.id):
        result.update(SERVICE_RUNTIME_ASSETS.get(service.type, frozenset()))
    return frozenset(result)


def service_runtime_assets(service_type: str) -> frozenset[str]:
    return SERVICE_RUNTIME_ASSETS.get(service_type, frozenset())


def _copy_component(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise StagingManifestError(f"component source is missing: {source}")
    for candidate in source.rglob("*"):
        if candidate.is_symlink():
            if not candidate.exists():
                continue
            try:
                candidate.resolve(strict=True).relative_to(source.resolve())
            except ValueError as exc:
                raise StagingManifestError(f"component contains an unsafe symlink: {candidate}") from exc
    def ignored(directory: str, names: list[str]) -> set[str]:
        result = {name for name in names if name in _IGNORED}
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_symlink() and not candidate.exists():
                result.add(name)
        return result
    shutil.copytree(source, destination, ignore=ignored)


def materialize_machine_inputs(plan: DeploymentPlan, machine: Machine, project_root: Path, directory: Path) -> None:
    """Copy the selected sources and runtime assets below one rendered bundle."""
    components_root = directory / "sources" / "components"
    for component_id in sorted(machine_components(plan, machine)):
        relative = COMPONENT_PATHS.get(component_id)
        if relative is None:
            raise StagingManifestError(f"component {component_id} has no registered source path")
        _copy_component(project_root / relative, components_root / component_id)
    if "dark-explorador" in machine_components(plan, machine) and not (components_root / "dark-explorador" / "dist").is_dir():
        raise StagingManifestError("explorer requires dark-explorador/dist in the selected source")
    for relative in sorted(machine_runtime_assets(plan, machine)):
        source = project_root / relative
        if not source.is_file():
            raise StagingManifestError(f"runtime asset is missing: {relative}")
        destination = directory / "runtime" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entries(directory: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for item in sorted(directory.rglob("*"), key=lambda candidate: candidate.relative_to(directory).as_posix()):
        if item.name in {"manifest.json", ".DS_Store"}:
            continue
        relative = item.relative_to(directory).as_posix()
        mode = stat.S_IMODE(item.lstat().st_mode)
        if item.is_symlink():
            entries.append({"path": relative, "type": "symlink", "mode": mode, "target": os.readlink(item)})
        elif item.is_file():
            entries.append({"path": relative, "type": "file", "mode": mode, "sha256": _sha256(item)})
        elif item.is_dir():
            entries.append({"path": relative, "type": "directory", "mode": mode})
        else:
            raise StagingManifestError(f"unsupported bundle entry: {item}")
    return entries


def write_machine_manifest(plan: DeploymentPlan, machine: Machine, directory: Path) -> dict:
    """Write a deterministic inventory of all public files in one bundle."""
    entries = _manifest_entries(directory)
    manifest = {"version": 1, "deployment_id": plan.deployment_id, "machine": machine.id, "files": entries}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_machine_manifest(plan: DeploymentPlan, machine: Machine, directory: Path) -> None:
    """Reject a missing, altered, or extra public file before distribution."""
    path = directory / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagingManifestError(f"invalid machine manifest: {path}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise StagingManifestError(f"unsupported machine manifest: {path}")
    if manifest.get("deployment_id") != plan.deployment_id or manifest.get("machine") != machine.id:
        raise StagingManifestError(f"machine manifest does not identify {plan.deployment_id}/{machine.id}")
    expected = manifest.get("files")
    if not isinstance(expected, list):
        raise StagingManifestError(f"machine manifest files are invalid: {path}")
    actual = _manifest_entries(directory)
    if expected != actual:
        raise StagingManifestError(f"machine bundle does not match its manifest: {directory}")
