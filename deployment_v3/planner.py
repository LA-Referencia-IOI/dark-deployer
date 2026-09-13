"""Build a dependency-ordered service plan from inventory v3."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .executor import LocalExecutor, resolve_executor
from .inventory import groups_for_inventory, load_inventory
from .model import DeploymentPlan, PlanStep
from .network import allocate_docker_subnets, derive_endpoints


PHASES = (
    ("validators", {"besu-validator"}),
    ("rpc", {"besu-rpc"}),
    ("observers", {"besu-observer"}),
    ("contracts", {"contracts-deploy", "rpc-probe"}),
    ("storage", {"ipfs-kubo", "ipfs-cluster"}),
    ("data", {"minter-postgres", "dashboard-mysql", "dashboard-redis", "store-api", "minter-migrate"}),
    ("applications", {"minter-api", "minter-worker", "admin-api", "resolver-api", "dashboard-migrate", "dashboard", "explorer", "edge-proxy"}),
)


def _layers(services, phase_order):
    pending = {service.id: {item["service"] for item in service.connections.values()} for service in services}
    resolved: set[str] = set()
    layers = []
    while pending:
        candidates = [service_id for service_id, dependencies in pending.items() if dependencies <= resolved]
        if candidates:
            earliest = min(phase_order[next(item for item in services if item.id == service_id).type] for service_id in candidates)
            ready = sorted(service_id for service_id in candidates if phase_order[next(item for item in services if item.id == service_id).type] == earliest)
        else:
            ready = []
        if not ready:
            raise ValueError("service dependency cycle: " + ", ".join(sorted(pending)))
        layers.append(ready)
        resolved.update(ready)
        for service_id in ready:
            pending.pop(service_id)
    return layers


def build_plan(inventory_path: Path) -> DeploymentPlan:
    raw, machines, services, networks = load_inventory(inventory_path)
    groups = groups_for_inventory(raw, machines, services)
    machines = tuple(replace(machine, execution="local" if isinstance(resolve_executor(machine), LocalExecutor) else "ssh") if machine.execution == "auto" else machine for machine in machines)
    steps = [PlanStep(f"preflight:{machine.id}", machine.id, "", "preflight", (), f"Validate {machine.id}") for machine in machines]
    previous: tuple[str, ...] = tuple(step.id for step in steps)
    steps.append(PlanStep("network-preflight", "", "", "network_preflight", previous, "Validate declared routes between hosts"))
    previous = ("network-preflight",)
    by_id = {service.id: service for service in services}
    group_by_service = {service_id: group.id for group in groups for service_id in group.service_ids}
    phase_by_type = {service_type: phase for phase, types in PHASES for service_type in types}
    phase_order = {service_type: index for index, (_, types) in enumerate(PHASES) for service_type in types}
    ordered = sorted(services, key=lambda item: (phase_order[item.type], item.id))
    completed_phases: set[str] = set()
    for index, layer in enumerate(_layers(ordered, phase_order), start=1):
        current = []
        for service_id in layer:
            service = by_id[service_id]
            dependencies = tuple(f"apply:{provider['service']}" for provider in service.connections.values()) or previous
            step_id = f"apply:{service.id}"
            group_id = group_by_service[service.id]
            steps.append(PlanStep(step_id, service.machine_id, service.id, "service_apply", dependencies, f"Start {service.id} ({group_id})", group_id))
            current.append(step_id)
        previous = tuple(current)
        phase = phase_by_type[by_id[layer[0]].type]
        remaining_same_phase = any(phase_by_type[by_id[item].type] == phase for future in _layers(ordered, phase_order)[index:] for item in future)
        if not remaining_same_phase and phase not in completed_phases:
            steps.append(PlanStep(f"readiness:{phase}", machines[0].id, "", "readiness", previous, f"Verify {phase} readiness"))
            previous = (f"readiness:{phase}",)
            completed_phases.add(phase)
    steps.append(PlanStep("verify:deployment", machines[0].id, "", "verify", previous, "Verify deployment"))
    return DeploymentPlan(raw["deployment"]["id"], inventory_path.resolve(), machines, services, networks, derive_endpoints(machines, services, raw), allocate_docker_subnets(machines, raw), tuple(steps), raw, groups)
