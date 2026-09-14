"""Apply explicit service instances to their assigned machines."""

from __future__ import annotations

import json
import hashlib
import ipaddress
import base64
import shlex
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from .executor import ExecutionError, LocalExecutor, SshExecutor, resolve_executor, run_network_preflight, run_preflight
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


def _distribute_contract_runtime(plan, producer) -> list[str]:
    """Copy the generated public contract environment to remote consumers.

    ``contracts-deploy`` is a one-shot producer rather than an HTTP service.
    Consumers can be on another host (notably a dedicated Resolver), so the
    runtime handoff must be explicit and occur before their application phase.
    """
    source_machine = plan.machine(producer.machine_id)
    source_path = Path(source_machine.data_root) / plan.deployment_id / "contracts" / "contracts.env"
    source = resolve_executor(source_machine).run(("cat", str(source_path)), timeout=20)
    _require(source, f"read generated contract environment from {source_machine.id}")
    payload = base64.b64encode(source.stdout.encode()).decode()
    recipients = {
        service.machine_id
        for service in plan.services
        if service.machine_id != producer.machine_id
        and any(connection.get("service") == producer.id for connection in service.connections.values())
    }
    for machine_id in sorted(recipients):
        machine = plan.machine(machine_id)
        target = Path(machine.data_root) / plan.deployment_id / "contracts" / "contracts.env"
        command = (
            "umask 077; mkdir -p " + shlex.quote(str(target.parent)) + "; "
            "printf %s " + shlex.quote(payload) + " | "
            "(base64 -d 2>/dev/null || base64 -D) > " + shlex.quote(str(target)) + "; chmod 600 " + shlex.quote(str(target))
        )
        _require(resolve_executor(machine).run(("sh", "-lc", command), timeout=30), f"distribute contract environment to {machine_id}")
    return sorted(recipients)


def _network_conflicts(executor, subnet: str) -> list[tuple[str, str, int]]:
    """Return bridge networks whose subnet overlaps ``subnet`` on one host."""
    requested = ipaddress.ip_network(subnet)
    listed = executor.run(("docker", "network", "ls", "--filter", "driver=bridge", "--format", "{{.Name}}"))
    if listed.returncode:
        return []
    conflicts: list[tuple[str, str, int]] = []
    for name in filter(None, listed.stdout.splitlines()):
        inspected = executor.run(("docker", "network", "inspect", name, "--format", "{{range .IPAM.Config}}{{.Subnet}}{{end}}"))
        if inspected.returncode:
            continue
        for candidate in filter(None, inspected.stdout.split()):
            try:
                overlaps = requested.overlaps(ipaddress.ip_network(candidate))
            except ValueError:
                continue
            if not overlaps:
                continue
            containers = executor.run(("docker", "network", "inspect", name, "--format", "{{len .Containers}}"))
            count = int(containers.stdout.strip()) if containers.returncode == 0 and containers.stdout.strip().isdigit() else -1
            conflicts.append((name, candidate, count))
    return conflicts


def _ensure_machine_network(plan, machine, executor, *, clean_empty_conflicts: bool, prompt_cleanup_empty_conflicts: bool) -> None:
    """Reuse the expected network or create it without hiding subnet conflicts."""
    name = f"{plan.deployment_id}-{machine.id}"
    subnet = plan.docker_subnets[machine.id]
    existing = executor.run(("docker", "network", "inspect", name, "--format", "{{range .IPAM.Config}}{{.Subnet}}{{end}}"))
    if existing.returncode == 0:
        actual = existing.stdout.strip()
        if actual != subnet:
            raise ApplyError(
                f"Docker network {name!r} already exists with subnet {actual or 'unknown'}, "
                f"but this inventory requires {subnet}; remove or reconcile that deployment explicitly"
            )
        return

    created = executor.run(("docker", "network", "create", "--driver", "bridge", "--subnet", subnet, name))
    if created.returncode == 0:
        return
    conflicts = _network_conflicts(executor, subnet)
    removable = [item for item in conflicts if item[2] == 0]
    occupied = [item for item in conflicts if item[2] != 0]
    should_clean = clean_empty_conflicts
    if not should_clean and prompt_cleanup_empty_conflicts and removable and not occupied:
        names = ", ".join(item[0] for item in removable)
        answer = input(
            f"Docker subnet {subnet} is blocked by empty network(s): {names}. "
            "Remove them and continue? [y/N]: "
        ).strip().lower()
        should_clean = answer in {"y", "yes"}
    if should_clean and removable and not occupied:
        for conflict_name, _, _ in removable:
            _require(executor.run(("docker", "network", "rm", conflict_name)), f"remove empty conflicting network {conflict_name}")
        _require(executor.run(("docker", "network", "create", "--driver", "bridge", "--subnet", subnet, name)), f"create network {machine.id}")
        return
    details = ", ".join(f"{conflict_name} ({conflict_subnet}, containers={count if count >= 0 else 'unknown'})" for conflict_name, conflict_subnet, count in conflicts)
    hint = ""
    if removable and not occupied:
        hint = " Re-run with --clean-empty-network-conflicts to remove only these empty conflicting networks."
    raise ApplyError(
        f"create network {machine.id}: Docker subnet {subnet} conflicts with "
        f"{details or 'an existing Docker network'}.{hint}"
    )


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


def _compile_contract_artifacts(project_root: Path, root: Path) -> Path:
    """Compile pinned Solidity sources through architecture-neutral solc-js."""
    project_root = project_root.resolve()
    output = (root / "contract-artifacts").resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    image = f"dark-solc-js:{hashlib.sha256(b'0.8.17').hexdigest()[:12]}"
    try:
        subprocess.run(
            ("docker", "build", "--tag", image, "--file", str(project_root / "deployment_v3" / "Dockerfile.solc-js"), str(project_root / "deployment_v3")),
            check=True,
            timeout=600,
        )
        subprocess.run(
            ("docker", "run", "--rm", "--mount", f"type=bind,src={project_root / 'components' / 'dark-dapp' / 'dARK_dapp' / 'contracts'},dst=/src,readonly", "--mount", f"type=bind,src={output},dst=/out", image),
            check=True,
            timeout=600,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ApplyError(f"compile Solidity contracts with portable solc-js: {exc}") from exc
    required = ("AuthorityABI.json", "AuthorityBytecode.txt", "dARKABI.json", "dARKBytecode.txt")
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise ApplyError(f"portable Solidity compiler did not produce: {', '.join(missing)}")
    return output


def _attach_contract_artifacts(plan, bundle: Path, artifacts: Path) -> None:
    """Place read-only compiler output beside each Compose project that needs it."""
    for group in plan.groups:
        if not any(plan.service(identifier).type in {"contracts-deploy", "rpc-probe"} for identifier in group.service_ids):
            continue
        destination = bundle / "machines" / group.machine_id / "groups" / group.id / "artifacts" / "contracts"
        destination.mkdir(parents=True, exist_ok=True)
        for source in artifacts.iterdir():
            shutil.copy2(source, destination / source.name)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _service_fingerprint(plan, service, bundle: Path, project_root: Path) -> str:
    """Hash rendered input and mounted/build sources that affect one service."""
    group = _group_for_service(plan, service.id)
    if group is None:
        raise ApplyError(f"service {service.id} is not assigned to a deployment group")
    directory = bundle / "machines" / service.machine_id / "groups" / group.id
    inputs = {
        "compose": _file_digest(directory / "compose.yaml"),
        "environment": _file_digest(directory / "env" / f"{service.id}.env"),
    }
    mounted_entrypoints = {
        "ipfs-kubo": project_root / "components" / "dark-ipfs" / "scripts" / "ipfs-entrypoint.sh",
        "ipfs-cluster": project_root / "components" / "dark-ipfs" / "scripts" / "cluster-entrypoint.sh",
        "besu-rpc": project_root / "blockchain" / "scripts" / "besu-entrypoint.sh",
        "besu-validator": project_root / "blockchain" / "scripts" / "besu-entrypoint.sh",
        "besu-observer": project_root / "blockchain" / "scripts" / "besu-entrypoint.sh",
    }
    source = mounted_entrypoints.get(service.type)
    if source and source.is_file():
        inputs["entrypoint"] = _file_digest(source)
    artifacts = directory / "artifacts" / "contracts"
    if service.type == "contracts-deploy" and artifacts.is_dir():
        inputs["artifacts"] = {item.name: _file_digest(item) for item in sorted(artifacts.iterdir()) if item.is_file()}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


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
    for service in (item for item in plan.services if item.type in {"besu-rpc", "besu-validator", "besu-observer"}):
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


def _apply_service(plan, machine, service, root, bundle, *, verbose=False, force_recreate=False, clean_empty_network_conflicts=False, prompt_cleanup_empty_network_conflicts=False):
    executor = resolve_executor(machine); directory = _compose_directory(plan, machine, root, service.id)
    if not directory.exists() and not isinstance(executor, SshExecutor): directory = _stage(machine, Path.cwd(), root, bundle / "machines" / machine.id)
    _ensure_machine_network(
        plan,
        machine,
        executor,
        clean_empty_conflicts=clean_empty_network_conflicts,
        prompt_cleanup_empty_conflicts=prompt_cleanup_empty_network_conflicts,
    )
    _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id), str(Path(machine.secrets_root)))), f"prepare data for {machine.id}")
    _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id / "contracts"))), f"prepare contract runtime for {machine.id}")
    if service.type in {"besu-rpc", "besu-validator", "besu-observer"}:
        # Besu's transaction-log Bloom cacher writes below /data/caches. A
        # partially initialized bind mount may have a valid chain database
        # but no cache directory, which otherwise produces a recurring
        # FileNotFoundException after the node starts producing blocks.
        _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id / service.id / "caches"))), f"prepare Besu cache for {service.id}")
    _require(executor.run(("touch", str(Path(machine.data_root) / plan.deployment_id / "contracts" / "contracts.env"))), f"prepare contract environment for {machine.id}")
    for secret_id, definition in plan.raw["secrets"].items():
        if service.id not in definition.get("consumers", []):
            continue
        secret_path = Path(machine.secrets_root) / definition["path"]
        _require(executor.run(("test", "-r", str(secret_path))), f"required secret {secret_id} is readable for {service.id} on {machine.id}")
    if service.type in {"besu-rpc", "besu-validator", "besu-observer"}:
        artifact = Path(machine.secrets_root) / plan.raw["blockchain"]["artifact"]["path"]
        node_id = service.configuration["node_id"]
        for relative in ("genesis.json", f"nodes/{node_id}/nodekey", f"nodes/{node_id}/static-nodes.json"):
            _require(executor.run(("test", "-r", str(artifact / relative))), f"chain artifact {relative} is readable for {service.id} on {machine.id}")
    compose=("docker", "compose", "--project-name", _compose_project(plan, machine, service.id), "-f", str(directory / "compose.yaml"))
    if service.type == "minter-postgres":
        data_dir = Path(machine.data_root) / plan.deployment_id / service.id
        _require(executor.run(("mkdir", "-p", str(data_dir))), "prepare PostgreSQL data directory")
        _require(executor.run(("docker", "run", "--rm", "--user", "0:0", "-v", f"{data_dir}:/var/lib/postgresql/data", plan.raw["images"]["postgres"], "chown", "-R", "70:70", "/var/lib/postgresql/data"), timeout=120), "fix PostgreSQL data ownership")
    if service.type in {"dashboard", "dashboard-migrate"}:
        # The checkout is staged by the SSH user, while Laravel and Composer
        # inside ambientum/php run as UID/GID 1000. Keep every generated tree
        # outside the checkout: otherwise stale config/view caches or a
        # host-owned development vendor tree survive into a deployment.
        dashboard_runtime = Path(machine.data_root) / plan.deployment_id / "dashboard"
        runtime_directories = (
            dashboard_runtime / "vendor",
            dashboard_runtime / "bootstrap-cache",
            dashboard_runtime / "storage" / "app" / "public",
            dashboard_runtime / "storage" / "framework" / "cache" / "data",
            dashboard_runtime / "storage" / "framework" / "sessions",
            dashboard_runtime / "storage" / "framework" / "testing",
            dashboard_runtime / "storage" / "framework" / "views",
            dashboard_runtime / "storage" / "logs",
        )
        _require(executor.run(("mkdir", "-p", *(str(path) for path in runtime_directories))), "prepare Dashboard runtime directories")
        _require(
            executor.run(
                ("docker", "run", "--rm", "--user", "0:0", "-v", f"{dashboard_runtime}:/runtime", "--entrypoint", "chown", plan.raw["images"]["dashboard"], "-R", "1000:1000", "/runtime"),
                timeout=120,
            ),
            "fix Dashboard runtime ownership",
        )
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
            attempt = 0
            while time.monotonic() < deadline:
                attempt += 1
                if verbose:
                    print(f"[VERBOSE] contracts-deploy attempt {attempt}; waiting for RPC and contract deployment", flush=True)
                last = executor.run((*compose, "run", "--rm", service.id), timeout=1800)
                if last.returncode == 0:
                    if verbose: print("[VERBOSE] contracts-deploy completed", flush=True)
                    return
                if verbose:
                    detail = (last.stderr.strip() or last.stdout.strip()).splitlines()[-1:] or ["no output"]
                    print(f"[VERBOSE] contracts-deploy attempt {attempt} failed: {detail[0]}", flush=True)
                time.sleep(2)
            raise ApplyError("RPC or contract deployment did not become ready: " + (last.stderr.strip() if last else "no job result"))
        _require(executor.run((*compose, "run", "--rm", service.id), timeout=1800), f"run {service.id}")
    else:
        recreate = ("--force-recreate",) if force_recreate else ()
        _require(executor.run((*compose, "up", "-d", "--build", *recreate, service.id), timeout=1800), f"start {service.id}")


def apply(plan, project_root: Path, *, resume=False, defer_verification=False, clean_chain_data=False, clean_empty_network_conflicts=False, prompt_cleanup_empty_network_conflicts=False, verbose=False):
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
                artifacts = _compile_contract_artifacts(project_root, root)
                render_plan(effective, bundle)
                _attach_contract_artifacts(effective, bundle, artifacts)
            elif not bundle.exists():
                artifacts = _compile_contract_artifacts(project_root, root)
                render_plan(effective, bundle)
                _attach_contract_artifacts(effective, bundle, artifacts)
            try:
                sources = source_evidence(effective, project_root)
            except SourceError as exc:
                raise ApplyError(f"source evidence failed: {exc}") from exc
            try:
                secret_hashes = distribute_secrets(effective, project_root)
            except SecretError as exc:
                raise ApplyError(f"secret distribution failed: {exc}") from exc
            previous_status = json.loads((root / "status.json").read_text()) if resume and (root / "status.json").exists() else {}
            previous_fingerprints = previous_status.get("service_fingerprints", {})
            status={"deployment_id": plan.deployment_id, "state": "running", "services": previous_status.get("services", {}), "service_fingerprints": {}, "sources": sources, "secret_hashes": secret_hashes, "readiness": previous_status.get("readiness", {})}
            record(root, {"state": "secrets_distributed", "machines": sorted(secret_hashes)})
            artifacts = _distribute_chain_artifact(effective, project_root)
            status["chain_artifacts"] = artifacts
            record(root, {"state": "chain_artifacts_distributed", "machines": sorted(artifacts)})
            _reject_existing_chain_data(effective, resume=resume, clean=clean_chain_data)
            write_status(root, status)
            for machine in effective.machines:
                if verbose: print(f"[VERBOSE] staging bundle on {machine.id}", flush=True)
                _stage(machine, project_root, root, bundle / "machines" / machine.id)
            for step in effective.steps:
                if verbose: print(f"[VERBOSE] step {step.id}: {step.description}", flush=True)
                if step.action == "preflight":
                    failed=[item["check"] for item in run_preflight(effective.machine(step.machine_id)) if not item["ok"]]
                    if failed: raise ApplyError(f"preflight failed on {step.machine_id}: {', '.join(failed)}")
                    status.setdefault("preflight", {})[step.machine_id] = "passed"
                    record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id})
                elif step.action == "network_preflight":
                    checks = run_network_preflight(effective)
                    failed = [item["check"] for item in checks if not item["ok"]]
                    if failed:
                        raise ApplyError("network preflight failed: " + ", ".join(failed))
                    status["network_preflight"] = checks
                    record(root, {"step": step.id, "state": "succeeded", "checks": len(checks)})
                elif step.action == "service_apply":
                    service=effective.service(step.service_id)
                    fingerprint = _service_fingerprint(effective, service, bundle, project_root)
                    status["service_fingerprints"][service.id] = fingerprint
                    if resume and status["services"].get(service.id) == "applied" and previous_fingerprints.get(service.id) == fingerprint:
                        record(root,{"step":step.id,"state":"reused","service":service.id})
                    else:
                        _apply_service(
                            effective,
                            effective.machine(service.machine_id),
                            service,
                            root,
                            bundle,
                            verbose=verbose,
                            force_recreate=clean_chain_data,
                            clean_empty_network_conflicts=clean_empty_network_conflicts,
                            prompt_cleanup_empty_network_conflicts=prompt_cleanup_empty_network_conflicts,
                        )
                        status["services"][service.id]="applied"; record(root,{"step":step.id,"state":"succeeded","service":service.id})
                    if service.type == "contracts-deploy":
                        targets = _distribute_contract_runtime(effective, service)
                        record(root, {"step": step.id, "state": "contract_runtime_distributed", "machines": targets})
                elif step.action == "readiness":
                    from .readiness import ReadinessError, wait_for_phase
                    phase = step.id.split(":", 1)[1]
                    try:
                        if verbose: print(f"[VERBOSE] waiting for {phase} readiness", flush=True)
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
