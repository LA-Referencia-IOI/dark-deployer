"""Turn a validated inventory into an ordered plan without side effects."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .executor import LocalExecutor, resolve_executor
from .inventory import load_inventory
from .model import DeploymentPlan, PlanStep
from .network import allocate_docker_subnets, derive_endpoints


def build_plan(inventory_path: Path) -> DeploymentPlan:
    raw, machines, groups, networks = load_inventory(inventory_path)
    # ``auto`` is useful in a portable inventory, but endpoint rendering needs
    # a concrete answer: Docker aliases/bridge addresses are valid only on a
    # daemon actually selected as local. Resolution is read-only (DNS/interface
    # inspection) and happens before the otherwise pure placement calculations.
    machines = tuple(
        replace(machine, execution="local" if isinstance(resolve_executor(machine), LocalExecutor) else "ssh")
        if machine.execution == "auto" else machine
        for machine in machines
    )
    steps: list[PlanStep] = []
    for machine in machines:
        steps.append(PlanStep(f"preflight:{machine.id}", machine.id, "", "preflight", (), f"Validate Docker and required paths on {machine.id}"))
    validators = [group for group in groups if group.kind == "validators"]
    for group in validators:
        steps.append(PlanStep(f"apply:{group.id}", group.machine_id, group.id, "compose_apply", (f"preflight:{group.machine_id}",), f"Start {group.id}"))
    validator_steps = tuple(f"apply:{group.id}" for group in validators)
    apps = next(group for group in groups if group.kind == "apps")
    steps.append(PlanStep("bootstrap:apps", apps.machine_id, apps.id, "apps_bootstrap", (f"preflight:{apps.machine_id}", *validator_steps), "Start the non-validator RPC and deploy or verify contracts"))
    for group in (group for group in groups if group.kind == "storage"):
        steps.append(PlanStep(f"apply:{group.id}", group.machine_id, group.id, "compose_apply", (f"preflight:{group.machine_id}", "bootstrap:apps"), f"Start Kubo and Cluster for {group.id}"))
    storage_steps = tuple(f"apply:{group.id}" for group in groups if group.kind == "storage")
    steps.append(PlanStep("apply:apps", apps.machine_id, apps.id, "apps_runtime", ("bootstrap:apps", *storage_steps), "Start APIs, workers and dashboard after storage is available"))
    steps.append(PlanStep("verify:deployment", apps.machine_id, apps.id, "verify", ("apply:apps", *storage_steps), "Verify chain, storage and application readiness"))
    return DeploymentPlan(
        deployment_id=raw["deployment"]["id"], inventory_path=inventory_path.resolve(),
        machines=machines, groups=groups, networks=networks, endpoints=derive_endpoints(machines, groups),
        docker_subnets=allocate_docker_subnets(machines, raw), steps=tuple(steps), raw=raw,
    )
