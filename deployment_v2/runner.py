"""Apply rendered groups with the same command sequence on local and SSH machines."""

from __future__ import annotations

import shutil
import json
import time
import os
from dataclasses import replace
from pathlib import Path

from .executor import ExecutionError, LocalExecutor, SshExecutor, resolve_executor, run_preflight
from .model import DeploymentPlan, Group
from .render import render_plan
from .sources import SourceError, require_matching_source_evidence, source_evidence
from .state import StateLockError, deployment_lock, record, run_root, write_status


class ApplyError(RuntimeError):
    """An apply step failed; journal and status identify the exact group."""


_RPC_READY_TIMEOUT_SECONDS = 90
_PUBLIC_SOURCE_EXCLUDES = (
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".generated", ".dark-secrets", ".env", ".env.integration", ".env.integration.secrets",
)


def _require(result, description: str) -> None:
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise ApplyError(f"{description}: {detail}")


def _copy_if_absent_or_identical(executor, source: Path, destination: Path, description: str) -> None:
    """Install immutable chain material without replacing an existing identity.

    A rerun may copy the same artifact safely.  A different genesis, private
    key or static-node list is a configuration change that must be handled by
    an explicit migration, never by an incidental deployment rerun.
    """
    present = executor.run(("test", "-e", str(destination)))
    if present.returncode == 0:
        identical = executor.run(("cmp", "-s", str(source), str(destination)))
        if identical.returncode:
            raise ApplyError(f"{description}: existing destination differs; refusing to overwrite {destination}")
        return
    _require(executor.run(("cp", str(source), str(destination))), description)


def _wait_for_rpc(executor, compose: tuple[str, ...], expected_chain_id: int) -> None:
    """Wait for the expected network before allowing contract deployment.

    The probe is a short-lived Python container already built from the
    contracts job image, so it uses the exact same Docker network and does not
    require curl, Python, or an exposed RPC port on the destination host.
    """
    program = (
        "import os; from web3 import Web3; "
        "web3=Web3(Web3.HTTPProvider(os.environ['DARK_RPC_URL'], request_kwargs={'timeout': 5})); "
        "assert web3.is_connected(), 'RPC unavailable'; "
        "assert web3.eth.chain_id == int(os.environ['DARK_CHAIN_ID']), 'unexpected chain ID'"
    )
    deadline = time.monotonic() + _RPC_READY_TIMEOUT_SECONDS
    last_detail = "RPC did not respond"
    while time.monotonic() < deadline:
        result = executor.run((*compose, "run", "--rm", "rpc-probe", "-c", program), timeout=30.0)
        if result.returncode == 0:
            return
        last_detail = result.stderr.strip() or result.stdout.strip() or last_detail
        time.sleep(2)
    raise ApplyError(f"RPC did not become ready on expected chain {expected_chain_id} within {_RPC_READY_TIMEOUT_SECONDS}s: {last_detail}")


def _source_paths(project_root: Path) -> tuple[Path, ...]:
    """Public sources required by generated Compose; private/generated state stays out."""
    return tuple(path for path in (project_root / "components", project_root / "blockchain", project_root / "deployment_v2") if path.exists())


def _ignore_public_source(_directory: str, names: list[str]) -> set[str]:
    """Keep source delivery free of VCS state, runtime data and private env files."""
    ignored = set(_PUBLIC_SOURCE_EXCLUDES).intersection(names)
    ignored.update(name for name in names if name.startswith(".env.node"))
    # Developer checkouts may contain framework-created links (for example
    # Laravel public/storage) whose target exists only inside a container.
    # They are not source inputs and must not make public staging fail.
    ignored.update(name for name in names if os.path.islink(Path(_directory) / name) and not os.path.exists(Path(_directory) / name))
    return ignored


def _remote_prepare(executor: SshExecutor, machine, project_root: Path, run_dir: Path, group_dir: Path) -> Path:
    remote_workspace = Path(machine.workspace_root)
    remote_run = remote_workspace / ".generated" / "deployment-v2" / run_dir.name
    _require(executor.run(("mkdir", "-p", str(remote_workspace), str(remote_run / "groups"))), f"prepare remote workspace {machine.id}")
    for source in _source_paths(project_root):
        _require(executor.transfer(source, str(remote_workspace / source.name), excludes=_PUBLIC_SOURCE_EXCLUDES + (".env.node*",)), f"transfer {source.name} to {machine.id}")
    _require(executor.transfer(group_dir, str(remote_run / "groups" / group_dir.name)), f"transfer group {group_dir.name} to {machine.id}")
    return remote_run / "groups" / group_dir.name


def _local_prepare(machine, project_root: Path, run_dir: Path, group_dir: Path) -> Path:
    destination = run_dir / "local" / machine.id / "groups" / group_dir.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(group_dir, destination, dirs_exist_ok=True)
    return destination


def _local_stage_sources(machine, project_root: Path, run_dir: Path) -> Path:
    """Stage public source trees below the run directory for a local daemon.

    Inventory paths describe destination servers.  A controller invoking a
    local simulation must not need permission to create those production-like
    paths (for example `/srv/dark`).
    """
    source_root = run_dir / "local" / machine.id / "sources"
    for source in _source_paths(project_root):
        shutil.copytree(source, source_root / source.name, dirs_exist_ok=True, ignore=_ignore_public_source)
    return source_root


def _effective_plan_for_apply(plan: DeploymentPlan, project_root: Path, run_dir: Path, *, stage_sources: bool = True) -> DeploymentPlan:
    """Use staged paths only for local machines; remote paths remain declared."""
    machines = []
    for machine in plan.machines:
        if isinstance(resolve_executor(machine), LocalExecutor):
            local_root = run_dir / "local" / machine.id
            workspace = _local_stage_sources(machine, project_root, run_dir) if stage_sources else local_root / "sources"
            machines.append(replace(
                machine,
                workspace_root=str(workspace),
                data_root=str(local_root / "data"),
                secrets_root=str(local_root / "secrets"),
            ))
        else:
            machines.append(machine)
    return replace(plan, machines=tuple(machines))


def _apply_group(plan: DeploymentPlan, group: Group, project_root: Path, run_dir: Path, bundle: Path, *, phase: str = "full") -> None:
    machine = plan.machine(group.machine_id)
    executor = resolve_executor(machine)
    group_dir = bundle / "groups" / group.id
    if isinstance(executor, SshExecutor):
        destination = _remote_prepare(executor, machine, project_root, run_dir, group_dir)
    else:
        destination = _local_prepare(machine, project_root, run_dir, group_dir)
    network = f"{plan.deployment_id}-{machine.id}"
    exists = executor.run(("docker", "network", "inspect", network))
    if exists.returncode:
        _require(executor.run(("docker", "network", "create", "--driver", "bridge", "--subnet", plan.docker_subnets[machine.id], network)), f"create network {network}")
    _require(executor.run(("mkdir", "-p", str(Path(machine.data_root) / plan.deployment_id), str(Path(machine.secrets_root)))), f"prepare data paths on {machine.id}")
    for secret_id, definition in plan.raw["secrets"].items():
        if group.kind not in definition.get("consumers", []):
            continue
        required_path = Path(machine.secrets_root) / definition["path"]
        _require(executor.run(("test", "-r", str(required_path))), f"required secret {secret_id} is readable on {machine.id}")
    if group.kind in {"apps", "validators"}:
        role = "rpc" if group.kind == "apps" else group.id
        artifact_definition = plan.raw["blockchain"].get("artifact", {})
        artifact_path = artifact_definition.get("path")
        if not artifact_path:
            raise ApplyError("blockchain.artifact.path is required to apply Besu groups")
        source = Path(machine.secrets_root) / artifact_path
        # Apps keeps the non-validator RPC under its apps data namespace;
        # validator groups use the shared blockchain namespace.
        target_namespace = "apps/blockchain" if group.kind == "apps" else "blockchain"
        target = Path(machine.data_root) / plan.deployment_id / target_namespace
        _require(executor.run(("test", "-r", str(source / "genesis.json"))), f"chain artifact genesis is readable for {role} on {machine.id}")
        _require(executor.run(("mkdir", "-p", str(target / "config"))), f"prepare chain config on {machine.id}")
        _copy_if_absent_or_identical(executor, source / "genesis.json", target / "config" / "genesis.json", f"install chain genesis for {role} on {machine.id}")
        _copy_if_absent_or_identical(executor, Path(machine.workspace_root) / "blockchain" / "config" / "besu-config.toml", target / "config" / "besu-config.toml", f"install Besu config for {role} on {machine.id}")
        for node in group.members:
            node_source = source / "nodes" / node
            node_target = target / node
            _require(executor.run(("test", "-r", str(node_source / "nodekey"))), f"chain key is readable for {node} on {machine.id}")
            _require(executor.run(("mkdir", "-p", str(node_target))), f"prepare chain node {node} on {machine.id}")
            for filename in ("nodekey", "key.pub", "static-nodes.json"):
                _copy_if_absent_or_identical(executor, node_source / filename, node_target / filename, f"install {filename} for {node} on {machine.id}")
    compose = ("docker", "compose", "--project-name", f"{plan.deployment_id}-{group.id}", "-f", str(destination / "compose.yaml"))
    if group.kind == "apps" and phase == "bootstrap":
        _require(executor.run((*compose, "--profile", "rpc", "up", "-d", "blockchain-rpc"), timeout=1800.0), f"start RPC for {group.id}")
        _wait_for_rpc(executor, compose, int(plan.raw["blockchain"]["chain_id"]))
        _require(executor.run((*compose, "run", "--rm", "contracts-deploy"), timeout=1800.0), f"deploy or verify contracts for {group.id}")
        return
    if group.kind == "apps" and phase == "runtime":
        _require(executor.run((*compose, "up", "-d", "--build", "--wait", "postgres"), timeout=1800.0), f"start PostgreSQL for {group.id}")
        # A bind-mounted PostgreSQL cluster can survive an interrupted first
        # init without creating POSTGRES_DB.  Make the database precondition
        # explicit and idempotent before running Alembic.
        ensure_db = (
            "sh", "-lc",
            "exists=$(psql -U dark -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='minter'\"); "
            "if [ \"$exists\" != \"1\" ]; then psql -U dark -d postgres -c 'CREATE DATABASE minter'; fi",
        )
        _require(executor.run((*compose, "exec", "-T", "postgres", *ensure_db), timeout=120.0), f"ensure PostgreSQL database for {group.id}")
        _require(executor.run((*compose, "run", "--rm", "minter-migrate"), timeout=1800.0), f"run Alembic migration for {group.id}")
        _require(executor.run((*compose, "up", "-d", "dashboard-mysql"), timeout=1800.0), f"start dashboard MySQL for {group.id}")
        _require(executor.run((*compose, "run", "--rm", "dashboard-migrate"), timeout=1800.0), f"run Laravel migration for {group.id}")
    _require(executor.run((*compose, "up", "-d", "--build"), timeout=1800.0), f"apply group {group.id}")


def apply(plan: DeploymentPlan, project_root: Path, *, resume: bool = False, defer_verification: bool = False) -> Path:
    """Render then apply groups in plan order. Preconditions and secrets must exist."""
    root = run_root(project_root, plan.deployment_id)
    try:
        with deployment_lock(root):
            return _apply_locked(plan, project_root, root, resume=resume, defer_verification=defer_verification)
    except StateLockError as exc:
        raise ApplyError(str(exc)) from exc


def push(plan: DeploymentPlan, project_root: Path) -> Path:
    """Render and transfer public sources/bundles without creating Docker state."""
    root = run_root(project_root, plan.deployment_id)
    try:
        with deployment_lock(root):
            bundle = root / "bundle"
            if bundle.exists():
                raise ApplyError(f"run bundle already exists: {root}; use apply or resume")
            evidence = source_evidence(plan, project_root)
            effective = _effective_plan_for_apply(plan, project_root, root)
            render_plan(effective, bundle)
            for group in effective.groups:
                machine = effective.machine(group.machine_id)
                executor = resolve_executor(machine)
                group_dir = bundle / "groups" / group.id
                if isinstance(executor, SshExecutor):
                    _remote_prepare(executor, machine, project_root, root, group_dir)
                else:
                    _local_prepare(machine, project_root, root, group_dir)
                record(root, {"step": f"push:{group.id}", "state": "succeeded", "machine": machine.id, "group": group.id})
            write_status(root, {"deployment_id": plan.deployment_id, "state": "pushed", "groups": {}, "sources": evidence})
            return root
    except (StateLockError, ExecutionError, SourceError) as exc:
        raise ApplyError(str(exc)) from exc


def _apply_locked(plan: DeploymentPlan, project_root: Path, root: Path, *, resume: bool, defer_verification: bool = False) -> Path:
    """Internal apply implementation; caller owns the controller journal lock."""
    bundle = root / "bundle"
    pushed = False
    status_path = root / "status.json"
    if bundle.exists() and not resume:
        if status_path.exists():
            try:
                pushed = json.loads(status_path.read_text()).get("state") == "pushed"
            except json.JSONDecodeError:
                pushed = False
        if not pushed:
            raise ApplyError(f"run directory already exists: {root}; use deploy.py resume")
    if (resume or pushed) and status_path.exists():
        try:
            status = json.loads(status_path.read_text())
        except json.JSONDecodeError as exc:
            raise ApplyError(f"invalid existing run status: {status_path}") from exc
        if status.get("deployment_id") != plan.deployment_id or not isinstance(status.get("groups"), dict):
            raise ApplyError(f"existing run status does not belong to {plan.deployment_id}")
    else:
        status = {"deployment_id": plan.deployment_id, "state": "running", "groups": {}}
    try:
        evidence = source_evidence(plan, project_root)
        if resume or pushed:
            recorded_evidence = status.get("sources")
            if not isinstance(recorded_evidence, dict):
                raise ApplyError("existing run has no valid source evidence; create a new deployment run")
            require_matching_source_evidence(recorded_evidence, evidence)
    except SourceError as exc:
        raise ApplyError(str(exc)) from exc
    effective_plan = _effective_plan_for_apply(plan, project_root, root)
    if not bundle.exists():
        render_plan(effective_plan, bundle)
    if resume or pushed:
        status["state"] = "running"
        status.pop("error", None)
    else:
        status["sources"] = evidence
    write_status(root, status)
    try:
        for step in plan.steps:
            if step.action == "preflight":
                checks = run_preflight(effective_plan.machine(step.machine_id))
                failed = [check["check"] for check in checks if not check["ok"]]
                if failed:
                    raise ApplyError(f"preflight failed on {step.machine_id}: " + ", ".join(failed))
                record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id})
                continue
            if step.action == "verify":
                # Persist the completed runtime phases before the read-only
                # verifier inspects them.  It owns the final verified/failed
                # status and records its own evidence in the same journal.
                status["state"] = "applied"
                write_status(root, status)
                from .verify import verify

                report = verify(plan, project_root)
                if not report["ok"] and not defer_verification:
                    raise ApplyError("post-apply verification failed; inspect status.json for evidence")
                if not report["ok"]:
                    record(root, {"step": step.id, "state": "pending", "reason": "readiness_retry_deferred"})
                status = json.loads(status_path.read_text())
                completed_steps = status.setdefault("completed_steps", [])
                completed_steps.append(step.id)
                write_status(root, status)
                record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id})
                continue
            if step.action not in {"compose_apply", "apps_bootstrap", "apps_runtime"}:
                continue
            completed_steps = status.setdefault("completed_steps", [])
            if step.id in completed_steps:
                record(root, {"step": step.id, "state": "skipped", "reason": "already_applied", "machine": step.machine_id, "group": step.group_id})
                continue
            record(root, {"step": step.id, "state": "started", "machine": step.machine_id, "group": step.group_id})
            phase = "bootstrap" if step.action == "apps_bootstrap" else "runtime" if step.action == "apps_runtime" else "full"
            _apply_group(effective_plan, effective_plan.group(step.group_id), project_root, root, bundle, phase=phase)
            completed_steps.append(step.id)
            if step.action in {"compose_apply", "apps_runtime"}:
                status["groups"][step.group_id] = "applied"  # type: ignore[index]
            write_status(root, status)
            record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id, "group": step.group_id})
    except (ExecutionError, ApplyError, SourceError) as exc:
        status["state"] = "failed"
        status["error"] = str(exc)
        write_status(root, status)
        record(root, {"state": "failed", "error": str(exc)})
        raise
    if status.get("state") != "verified":
        status["state"] = "applied"
    write_status(root, status)
    return root
