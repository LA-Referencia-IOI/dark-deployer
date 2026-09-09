"""Apply rendered groups with the same command sequence on local and SSH machines."""

from __future__ import annotations

import shutil
from pathlib import Path

from .executor import ExecutionError, LocalExecutor, SshExecutor, resolve_executor
from .model import DeploymentPlan, Group
from .render import render_plan
from .state import record, run_root, write_status


class ApplyError(RuntimeError):
    """An apply step failed; journal and status identify the exact group."""


def _require(result, description: str) -> None:
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise ApplyError(f"{description}: {detail}")


def _source_paths(project_root: Path) -> tuple[Path, ...]:
    """Public sources required by generated Compose; private/generated state stays out."""
    return tuple(path for path in (project_root / "components", project_root / "blockchain") if path.exists())


def _remote_prepare(executor: SshExecutor, machine, project_root: Path, run_dir: Path, group_dir: Path) -> Path:
    remote_workspace = Path(machine.workspace_root)
    remote_run = remote_workspace / ".generated" / "deployment-v2" / run_dir.name
    _require(executor.run(("mkdir", "-p", str(remote_workspace), str(remote_run / "groups"))), f"prepare remote workspace {machine.id}")
    for source in _source_paths(project_root):
        _require(executor.transfer(source, str(remote_workspace / source.name)), f"transfer {source.name} to {machine.id}")
    _require(executor.transfer(group_dir, str(remote_run / "groups" / group_dir.name)), f"transfer group {group_dir.name} to {machine.id}")
    return remote_run / "groups" / group_dir.name


def _local_prepare(machine, project_root: Path, run_dir: Path, group_dir: Path) -> Path:
    destination = run_dir / "local" / machine.id / "groups" / group_dir.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(group_dir, destination, dirs_exist_ok=True)
    return destination


def _apply_group(plan: DeploymentPlan, group: Group, project_root: Path, run_dir: Path, bundle: Path) -> None:
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
    _require(executor.run(("docker", "compose", "--project-name", f"{plan.deployment_id}-{group.id}", "-f", str(destination / "compose.yaml"), "up", "-d", "--build"), timeout=1800.0), f"apply group {group.id}")


def apply(plan: DeploymentPlan, project_root: Path) -> Path:
    """Render then apply groups in plan order. Preconditions and secrets must exist."""
    root = run_root(project_root, plan.deployment_id)
    bundle = root / "bundle"
    if bundle.exists():
        raise ApplyError(f"run directory already exists: {root}; use a future resume command")
    render_plan(plan, bundle)
    status: dict[str, object] = {"deployment_id": plan.deployment_id, "state": "running", "groups": {}}
    write_status(root, status)
    try:
        for step in plan.steps:
            if step.action != "compose_apply":
                continue
            record(root, {"step": step.id, "state": "started", "machine": step.machine_id, "group": step.group_id})
            _apply_group(plan, plan.group(step.group_id), project_root, root, bundle)
            status["groups"][step.group_id] = "applied"  # type: ignore[index]
            write_status(root, status)
            record(root, {"step": step.id, "state": "succeeded", "machine": step.machine_id, "group": step.group_id})
    except (ExecutionError, ApplyError) as exc:
        status["state"] = "failed"
        status["error"] = str(exc)
        write_status(root, status)
        record(root, {"state": "failed", "error": str(exc)})
        raise
    status["state"] = "applied"
    write_status(root, status)
    return root
