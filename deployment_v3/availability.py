"""Pure availability analysis for a resolved deployment plan."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureScenario:
    id: str
    kind: str
    target: str
    consensus: str
    applications_rpc: str
    observers: str
    storage: str
    validators_remaining: int
    storage_peers_remaining: int


@dataclass(frozen=True)
class AvailabilityReport:
    validators: int
    quorum: int
    validator_failures_tolerated: int
    primary_rpc: str | None
    validator_groups: dict[str, int]
    validator_machines: dict[str, int]
    observer_groups: dict[str, int]
    storage_machines: dict[str, int]
    blockchain_full_copies: int
    storage_peers: int
    storage_publish_after_replicas: int
    storage_target_replicas: int
    scenarios: tuple[FailureScenario, ...]
    warnings: tuple[str, ...]


def analyze(plan) -> AvailabilityReport:
    """Describe quorum and failure domains without contacting any runtime."""
    service_group = {service_id: group.id for group in plan.groups for service_id in group.service_ids}
    validators = [service for service in plan.services if service.type == "besu-validator"]
    observers = [service for service in plan.services if service.type == "besu-observer"]
    validator_groups: dict[str, int] = {}
    validator_machines: dict[str, int] = {}
    observer_groups: dict[str, int] = {}
    storage_machines: dict[str, int] = {}
    for service in validators:
        group = service_group[service.id]
        validator_groups[group] = validator_groups.get(group, 0) + 1
        validator_machines[service.machine_id] = validator_machines.get(service.machine_id, 0) + 1
    for service in observers:
        group = service_group[service.id]
        observer_groups[group] = observer_groups.get(group, 0) + 1
    for service in plan.services:
        if service.type == "ipfs-kubo":
            storage_machines[service.machine_id] = storage_machines.get(service.machine_id, 0) + 1
    count = len(validators)
    quorum = (2 * count) // 3 + 1
    storage_peers = [service for service in plan.services if service.type == "ipfs-kubo"]
    replication = plan.raw["storage"]["replication"]
    publish_after = int(replication["publish_after_replicas"])
    target_replicas = int(replication["target_replicas"])
    primary = plan.primary_rpc().id

    def scenario(kind: str, target: str, lost_validators: int = 0, lost_storage: int = 0, *, primary_lost: bool = False, observers_lost: int = 0) -> FailureScenario:
        validators_remaining = count - lost_validators
        peers_remaining = len(storage_peers) - lost_storage
        consensus = "continues" if validators_remaining >= quorum else "quorum_lost"
        applications_rpc = "primary_lost" if primary_lost else "available"
        observer_state = "copy_lost" if observers_lost else "unchanged"
        if peers_remaining < publish_after:
            storage_state = "publication_unavailable"
        elif peers_remaining < target_replicas:
            storage_state = "durability_target_unmet"
        elif lost_storage:
            storage_state = "degraded"
        else:
            storage_state = "available"
        return FailureScenario(f"loss:{kind}:{target}", kind, target, consensus, applications_rpc, observer_state, storage_state, validators_remaining, peers_remaining)

    scenarios = [scenario("validator", service.id, lost_validators=1) for service in validators]
    scenarios.extend(scenario("validator_group", group, lost_validators=lost) for group, lost in validator_groups.items())
    observer_machines: dict[str, int] = {}
    for service in observers:
        observer_machines[service.machine_id] = observer_machines.get(service.machine_id, 0) + 1
    all_machines = sorted({service.machine_id for service in validators + observers + storage_peers} | {plan.primary_rpc().machine_id})
    for machine in all_machines:
        scenarios.append(scenario(
            "machine", machine,
            lost_validators=validator_machines.get(machine, 0),
            lost_storage=storage_machines.get(machine, 0),
            primary_lost=plan.primary_rpc().machine_id == machine,
            observers_lost=observer_machines.get(machine, 0),
        ))
    scenarios.append(scenario("primary_rpc", primary, primary_lost=True))
    scenarios.extend(scenario("storage_peer", service.id, lost_storage=1) for service in storage_peers)
    warnings = []
    if len(validator_machines) == 1:
        warnings.append("All validators share one machine; losing it loses consensus.")
    for machine, lost in validator_machines.items():
        if count - lost < quorum:
            warnings.append(f"Losing machine {machine} removes QBFT quorum.")
    for group, lost in validator_groups.items():
        if count - lost < quorum:
            warnings.append(f"Losing validator group {group} removes QBFT quorum.")
    if len(storage_machines) < len([service for service in plan.services if service.type == "ipfs-kubo"]):
        warnings.append("Storage replication shares a machine failure domain.")
    return AvailabilityReport(
        count, quorum, count - quorum, primary, validator_groups,
        validator_machines, observer_groups, storage_machines,
        count + len(observers) + len([service for service in plan.services if service.type == "besu-rpc"]),
        len(storage_peers), publish_after, target_replicas, tuple(scenarios), tuple(warnings),
    )
