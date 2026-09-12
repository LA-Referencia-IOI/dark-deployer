"""Apply explicit service instances to their assigned machines."""

from __future__ import annotations

import json
import hashlib
import shlex
import shutil
import time
from dataclasses import replace
from pathlib import Path

from .executor import ExecutionError, LocalExecutor, SshExecutor, resolve_executor, run_preflight
from .model import DeploymentPlan
from .render import render_plan
from .secrets import SecretError, distribute_secrets
from .state import StateLockError, deployment_lock, record, run_root, write_status
from .sources import SourceError, source_evidence
from .artifacts import ArtifactError, verify_artifact_compatibility, verify_artifact_manifest


class ApplyError(RuntimeError): pass


def persistent_data_inventory(plan: DeploymentPlan) -> list[tuple[str, bool]]:
    """Return all managed persistent paths and whether they contain data."""
    inventory = []
    persistent_types = {
        "besu-rpc", "besu-validator", "ipfs-kubo", "ipfs-cluster",
        "minter-postgres", "dashboard-mysql", "dashboard-redis", "contracts-deploy",
    }
    for service in plan.services:
        if service.type not in persistent_types:
            continue
        machine = plan.machine(service.machine_id)
        data = Path(machine.data_root) / plan.deployment_id / service.id
        probe = resolve_executor(machine).run(("sh", "-lc", f"if [ -d {shlex.quote(str(data))} ] && find {shlex.quote(str(data))} -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then printf present; fi"), timeout=20)
        if probe.returncode == 0 and probe.stdout.strip() == "present":
            inventory.append((f"{machine.id}:{service.id}:{service.type}:{data}", probe.returncode == 0 and probe.stdout.strip() == "present"))
    return inventory


def existing_chain_data(plan: DeploymentPlan) -> list[str]:
    """Return non-empty persistent service directories for the deployment."""
    return [item for item, present in persistent_data_inventory(plan) if present]


def _reject_existing_chain_data(plan: DeploymentPlan, *, resume: bool, clean: bool = False) -> None:
    """Prevent a normal install from mixing a new artifact with old Besu data."""
    if resume:
        return
    existing = existing_chain_data(plan)
    if existing:
        if clean:
            for item in existing:
                machine_id, _, _, raw_path = item.split(":", 3)
                machine = plan.machine(machine_id)
                _require(resolve_executor(machine).run(("rm", "-rf", raw_path)), f"clean Besu data for {machine_id}")
            contract = next((item for item in plan.services if item.type == "contracts-deploy"), None)
            if contract:
                machine = plan.machine(contract.machine_id)
                runtime = Path(machine.data_root) / plan.deployment_id / "contracts"
                _require(resolve_executor(machine).run(("rm", "-rf", str(runtime))), f"clean contract runtime for {machine.id}")
            return
        raise ApplyError(
            "persistent deployment data already exists for a normal install: "
            + ", ".join(existing)
            + "; use --resume with the original deployment, or explicitly recreate/clean this deployment"
        )


def _require(result, description):
    if result.returncode: raise ApplyError(f"{description}: {result.stderr.strip() or result.stdout.strip() or 'no command output'}")


def _ignore_source_entries(directory: str, names: list[str]) -> set[str]:
    """Exclude VCS/runtime files and broken framework symlinks from staging."""
    ignored = {name for name in names if name in {".git", ".env", ".env.integration", ".generated", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache"}}
    for name in names:
        candidate = Path(directory) / name
        if candidate.is_symlink() and not candidate.exists():
            ignored.add(name)
    return ignored


def _stage(machine, project_root, root, directory):
    if isinstance(resolve_executor(machine), SshExecutor):
        remote = Path(machine.workspace_root) / ".generated" / "deployment-v3" / root.name / "machines" / machine.id
        executor = resolve_executor(machine); _require(executor.run(("mkdir", "-p", str(remote))), f"prepare {machine.id}")
        for source in (project_root / "components", project_root / "blockchain", project_root / "deployment_v3"):
            if source.exists(): _require(executor.transfer(source, str(Path(machine.workspace_root) / source.name), excludes=(".git", ".env", ".generated", "venv", ".venv")), f"transfer {source.name}")
        _require(executor.transfer(directory, str(remote)), f"transfer deployment for {machine.id}")
        return remote
    destination = root / "local" / machine.id / "machine"; destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(directory, destination, dirs_exist_ok=True)
    source_root = root / "local" / machine.id / "sources"
    for source in (project_root / "components", project_root / "blockchain", project_root / "deployment_v3"):
        if source.exists(): shutil.copytree(source, source_root / source.name, dirs_exist_ok=True, ignore=_ignore_source_entries)
    return destination


def _effective_plan(plan, project_root, root):
    machines=[]
    for machine in plan.machines:
        if isinstance(resolve_executor(machine), LocalExecutor):
            local = root / "local" / machine.id
            machines.append(replace(machine, workspace_root=str(local / "sources"), data_root=str(local / "data"), secrets_root=str(local / "secrets")))
        else: machines.append(machine)
    return replace(plan, machines=tuple(machines))


def _machine_directory(plan, machine, root):
    if isinstance(resolve_executor(machine), SshExecutor): return Path(machine.workspace_root) / ".generated" / "deployment-v3" / root.name / "machines" / machine.id
    return root / "local" / machine.id / "machine"


def _group_for_service(plan, service_id):
    return next((group for group in plan.groups if service_id in group.service_ids), None)


def _compose_directory(plan, machine, root, service_id=None):
    directory = _machine_directory(plan, machine, root)
    group = _group_for_service(plan, service_id) if service_id else None
    return directory / "groups" / group.id if group and group.machine_id == machine.id else directory


def _compose_project(plan, machine, service_id=None):
    group = _group_for_service(plan, service_id) if service_id else None
    return f"{plan.deployment_id}-{machine.id}" + (f"-{group.id}" if group and group.machine_id == machine.id else "")


def _distribute_chain_artifact(plan, project_root: Path) -> dict[str, dict[str, str]]:
    """Deliver only genesis and each node's own private material per host."""
    artifact = plan.raw["blockchain"]["artifact"]
    source_ref = artifact.get("source")
    if not source_ref:
        # Local greenfield installation has already provisioned this tree.
        if all(machine.execution == "local" for machine in plan.machines):
            return {}
        raise ApplyError("blockchain.artifact.source is required for SSH deployment")
    source = Path(source_ref)
    if not source.is_absolute(): source = project_root / source
    if not source.is_dir(): raise ApplyError(f"blockchain artifact source is not a directory: {source}")
    try:
        verify_artifact_manifest(source)
        verify_artifact_compatibility(plan, source)
    except ArtifactError as exc:
        raise ApplyError(f"chain artifact validation failed: {exc}") from exc
    delivered: dict[str, dict[str, str]] = {}
    for service in (item for item in plan.services if item.type in {"besu-rpc", "besu-validator"}):
        machine = plan.machine(service.machine_id)
        node = service.configuration["node_id"]
        destination = Path(machine.secrets_root) / artifact["path"]
        relative = (Path("genesis.json"), Path("nodes") / node / "nodekey", Path("nodes") / node / "static-nodes.json")
        executor = resolve_executor(machine)
        for item in relative:
            origin = source / item
            if not origin.is_file(): raise ApplyError(f"chain artifact source is missing {item}")
            digest = hashlib.sha256(origin.read_bytes()).hexdigest()
            target = destination / item
            if isinstance(executor, LocalExecutor):
                target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(origin, target); target.chmod(0o600 if target.name == "nodekey" else 0o644)
            else:
                _require(executor.run(("mkdir", "-p", str(target.parent))), f"prepare chain artifact directory on {machine.id}")
                _require(executor.transfer(origin, str(target.parent)), f"transfer chain artifact {item} to {machine.id}")
                mode = "600" if target.name == "nodekey" else "644"
                quoted_target = shlex.quote(str(target))
                checked = executor.run(("sh", "-lc", f"chmod {mode} {quoted_target} && sha256sum {quoted_target} | awk '{{print $1}}'"), timeout=30)
                _require(checked, f"secure chain artifact {item} on {machine.id}")
                if checked.stdout.strip() != digest:
                    raise ApplyError(f"chain artifact hash mismatch for {item} on {machine.id}")
            delivered.setdefault(machine.id, {})[str(item)] = digest
    return delivered


def _apply_service(plan, machine, service, root, bundle):
    executor = resolve_executor(machine); directory = _compose_directory(plan, machine, root, service.id)
    if not directory.exists() and not isinstance(executor, SshExecutor): directory = _stage(machine, Path.cwd(), root, bundle / "machines" / machine.id)
    network=f"{plan.deployment_id}-{machine.id}"
    if executor.run(("docker", "network", "inspect", network)).returncode:
        _require(executor.run(("docker", "network", "create", "--driver", "bridge", "--subnet", plan.docker_subnets[machine.id], network)), f"create network {machine.id}")
    _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id), str(Path(machine.secrets_root)))), f"prepare data for {machine.id}")
    _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id / "contracts"))), f"prepare contract runtime for {machine.id}")
    _require(executor.run(("touch", str(Path(machine.data_root) / plan.deployment_id / "contracts" / "contracts.env"))), f"prepare contract environment for {machine.id}")
    for secret_id, definition in plan.raw["secrets"].items():
        if service.id not in definition.get("consumers", []):
            continue
        secret_path = Path(machine.secrets_root) / definition["path"]
        _require(executor.run(("test", "-r", str(secret_path))), f"required secret {secret_id} is readable for {service.id} on {machine.id}")
    if service.type in {"besu-rpc", "besu-validator"}:
        artifact = Path(machine.secrets_root) / plan.raw["blockchain"]["artifact"]["path"]
        node_id = service.configuration["node_id"]
        for relative in ("genesis.json", f"nodes/{node_id}/nodekey", f"nodes/{node_id}/static-nodes.json"):
            _require(executor.run(("test", "-r", str(artifact / relative))), f"chain artifact {relative} is readable for {service.id} on {machine.id}")
    compose=("docker", "compose", "--project-name", _compose_project(plan, machine, service.id), "-f", str(directory / "compose.yaml"))
    if service.type == "minter-postgres":
        data_dir = Path(machine.data_root) / plan.deployment_id / service.id
        _require(executor.run(("mkdir", "-p", str(data_dir))), "prepare PostgreSQL data directory")
        _require(executor.run(("docker", "run", "--rm", "--user", "0:0", "-v", f"{data_dir}:/var/lib/postgresql/data", "postgres:15-alpine", "chown", "-R", "70:70", "/var/lib/postgresql/data"), timeout=120), "fix PostgreSQL data ownership")
    if service.type in {"minter-migrate", "dashboard-migrate", "contracts-deploy", "rpc-probe"}:
        if service.type == "minter-migrate":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                ready = executor.run((*compose, "exec", "-T", service.connections["database"]["service"], "pg_isready", "-U", "dark", "-d", "minter"), timeout=15)
                if ready.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise ApplyError("PostgreSQL did not become ready before Minter migration")
            # Ensure the application database exists even if PostgreSQL was
            # initialized from a pre-created bind mount without POSTGRES_DB.
            # The application role is the superuser provisioned by the
            # generated PostgreSQL environment.  It can create the database
            # on a pre-existing data directory; the command is idempotent for
            # a fresh directory because migration follows immediately.
            executor.run((*compose, "exec", "-T", service.connections["database"]["service"], "psql", "-U", "dark", "-d", "postgres", "-c", "CREATE DATABASE minter"), timeout=120)
        if service.type == "dashboard-migrate":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                ready = executor.run((*compose, "exec", "-T", service.connections["database"]["service"], "sh", "-lc", "mysqladmin ping -h localhost -uroot -p\"$MYSQL_ROOT_PASSWORD\" --silent"), timeout=15)
                if ready.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise ApplyError("MySQL did not become ready before dashboard migration")
        if service.type == "contracts-deploy":
            # A chain can take a few block periods to elect a validator quorum.
            # The deployment job itself validates the expected chain ID and is
            # idempotent once it has written its handoff file.
            deadline = time.monotonic() + 120
            last = None
            while time.monotonic() < deadline:
                last = executor.run((*compose, "run", "--rm", service.id), timeout=1800)
                if last.returncode == 0:
                    return
                time.sleep(2)
            raise ApplyError("RPC or contract deployment did not become ready: " + (last.stderr.strip() if last else "no job result"))
        _require(executor.run((*compose, "run", "--rm", service.id), timeout=1800), f"run {service.id}")
    else:
        _require(executor.run((*compose, "up", "-d", "--build", service.id), timeout=1800), f"start {service.id}")


def apply(plan, project_root: Path, *, resume=False, defer_verification=False, clean_chain_data=False):
    root=run_root(project_root, plan.deployment_id)
    try:
        with deployment_lock(root):
            bundle=root / "bundle"
            effective=_effective_plan(plan, project_root, root)
            # A normal install must reflect the current source and generated
            # runtime env files.  Only an explicit resume reuses the previous
            # immutable bundle.
            if not resume:
                if bundle.exists(): shutil.rmtree(bundle)
                render_plan(effective, bundle)
            elif not bundle.exists():
                render_plan(effective, bundle)
            try:
                sources = source_evidence(effective, project_root)
            except SourceError as exc:
                raise ApplyError(f"source evidence failed: {exc}") from exc
            try:
                secret_hashes = distribute_secrets(effective, project_root)
            except SecretError as exc:
                raise ApplyError(f"secret distribution failed: {exc}") from exc
            previous_status = json.loads((root / "status.json").read_text()) if resume and (root / "status.json").exists() else {}
            status={"deployment_id": plan.deployment_id, "state": "running", "services": previous_status.get("services", {}), "sources": sources, "secret_hashes": secret_hashes, "readiness": previous_status.get("readiness", {})}
            record(root, {"state": "secrets_distributed", "machines": sorted(secret_hashes)})
            artifacts = _distribute_chain_artifact(effective, project_root)
            status["chain_artifacts"] = artifacts
            record(root, {"state": "chain_artifacts_distributed", "machines": sorted(artifacts)})
            _reject_existing_chain_data(effective, resume=resume, clean=clean_chain_data)
            write_status(root, status)
            for machine in effective.machines:
                _stage(machine, project_root, root, bundle / "machines" / machine.id)
            for step in effective.steps:
                if step.action == "preflight":
                    failed=[item["check"] for item in run_preflight(effective.machine(step.machine_id)) if not item["ok"]]
                    if failed: raise ApplyError(f"preflight failed on {step.machine_id}: {', '.join(failed)}")
                    status.setdefault("preflight", {})[step.machine_id] = "passed"
                    record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id})
                elif step.action == "service_apply":
                    service=effective.service(step.service_id)
                    if resume and status["services"].get(service.id) == "applied":
                        record(root,{"step":step.id,"state":"reused","service":service.id})
                    else:
                        _apply_service(effective, effective.machine(service.machine_id), service, root, bundle)
                        status["services"][service.id]="applied"; record(root,{"step":step.id,"state":"succeeded","service":service.id})
                elif step.action == "readiness":
                    from .readiness import ReadinessError, wait_for_phase
                    phase = step.id.split(":", 1)[1]
                    try:
                        evidence = wait_for_phase(effective, project_root, phase)
                    except ReadinessError as exc:
                        raise ApplyError(str(exc)) from exc
                    status.setdefault("readiness", {})[phase] = {"ok": True, "evidence": evidence}
                    record(root, {"step": step.id, "state": "succeeded", "phase": phase, "evidence": evidence})
                write_status(root, status)
            status["state"]="applied"; write_status(root,status); return root
    except (StateLockError, ExecutionError) as exc: raise ApplyError(str(exc)) from exc


def push(plan, project_root: Path):
    root=run_root(project_root, plan.deployment_id); effective=_effective_plan(plan, project_root, root); bundle=root / "bundle"; render_plan(effective,bundle)
    try:
        sources = source_evidence(effective, project_root)
    except SourceError as exc:
        raise ApplyError(f"source evidence failed: {exc}") from exc
    (bundle / "shared" / "source-evidence.json").write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n")
    for machine in effective.machines: _stage(machine, project_root, root, bundle / "machines" / machine.id)
    write_status(root,{"deployment_id":plan.deployment_id,"state":"pushed","services":{},"sources":sources}); return root


def recreate_service(plan, project_root: Path, service: str, *, build=False):
    root=run_root(project_root,plan.deployment_id); effective=_effective_plan(plan,project_root,root); instance=effective.service(service); machine=effective.machine(instance.machine_id); executor=resolve_executor(machine); directory=_compose_directory(effective,machine,root,service); compose=("docker","compose","--project-name",_compose_project(effective,machine,service),"-f",str(directory / "compose.yaml")); _require(executor.run((*compose,"up","-d","--force-recreate",*(('--build',) if build else ()),service),timeout=1800),f"recreate {service}")
