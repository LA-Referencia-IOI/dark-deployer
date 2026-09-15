"""Pure projection from a resolved deployment plan to a canvas graph.

This module never touches the filesystem or the network. It only reads the
already-resolved ``DeploymentPlan`` (and the availability report) produced by
``deployment_v3`` and turns them into a JSON-ready description of the topology:

* machines, with the services nested on them;
* networks, with the machines reachable on each one;
* groups, as the logical containers of services;
* edges for every declared connection, flagged as ``docker`` (same host) or
  ``private`` (crosses a host boundary);
* the availability summary.

The canvas draws every service, but only the ``private`` edges: intra-host
links share a Docker network and carry no information worth a line.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

FAMILY_BY_TYPE: dict[str, str] = {
    "besu-validator": "blockchain",
    "besu-rpc": "blockchain",
    "besu-observer": "blockchain",
    "contracts-deploy": "blockchain",
    "rpc-probe": "blockchain",
    "ipfs-kubo": "storage",
    "ipfs-cluster": "storage",
    "minter-api": "applications",
    "minter-worker": "applications",
    "minter-postgres": "applications",
    "minter-migrate": "applications",
    "admin-api": "applications",
    "resolver-api": "applications",
    "store-api": "applications",
    "dashboard": "applications",
    "dashboard-mysql": "applications",
    "dashboard-redis": "applications",
    "dashboard-migrate": "applications",
    "explorer": "web",
    "edge-proxy": "web",
}

# Families whose instances an operator multiplies and distributes; everything
# else is a singleton that can only be *placed*.
SCALABLE_TYPES = frozenset(
    {"besu-validator", "besu-rpc", "besu-observer", "ipfs-kubo", "ipfs-cluster"}
)

FAMILY_ORDER = ("blockchain", "storage", "applications", "web", "other")


def _family(service_type: str) -> str:
    return FAMILY_BY_TYPE.get(service_type, "other")


def _subtitle(service) -> str | None:
    configuration = service.configuration
    for key in ("node_id", "peer_name", "worker"):
        value = configuration.get(key)
        if isinstance(value, str) and value and value != service.id:
            return value
    return None


@dataclass(frozen=True)
class ServiceNode:
    id: str
    type: str
    family: str
    node_kind: str  # "scalable" | "singleton"
    machine_id: str
    group_id: str
    subtitle: str | None
    exposure: dict[str, Any] | None


@dataclass(frozen=True)
class MachineNode:
    id: str
    execution: str
    management_address: str
    addresses: dict[str, str]
    docker_subnet: str | None
    site: str | None
    group_ids: list[str]
    service_count: int
    # What happens to the deployment if this machine is lost (from the
    # deployer's own failure scenarios). None when the machine holds nothing
    # whose loss matters.
    if_lost: dict[str, str] | None = None


@dataclass(frozen=True)
class NetworkNode:
    id: str
    kind: str
    cidr: str
    machine_ids: list[str]


@dataclass(frozen=True)
class GroupNode:
    id: str
    kind: str
    machine_id: str
    service_ids: list[str]
    members: list[str]
    # Consensus outcome if this whole validator group is lost.
    if_lost: dict[str, str] | None = None


@dataclass(frozen=True)
class Edge:
    consumer: str
    provider: str
    name: str
    network: str | None
    protocol: str | None
    # "docker" (same host), "private" (crosses a host) or "dependency" for the
    # ordering-only edges into contracts-deploy, which the deployer never turns
    # into a network endpoint.
    transport: str
    routed: bool
    crosses_host: bool
    host: str | None
    port: int | None


@dataclass(frozen=True)
class AvailabilityView:
    validators: int
    quorum: int
    validator_failures_tolerated: int
    primary_rpc: str | None
    blockchain_full_copies: int
    storage_peers: int
    storage_publish_after_replicas: int
    storage_target_replicas: int
    warnings: list[str]


@dataclass(frozen=True)
class OperatorView:
    """The compact facts a control panel needs, straight from the operator file."""

    deployment_id: str
    label: str | None
    profile: str | None
    format_version: int | None
    primary_rpc: str | None
    rpc_nodes: list[str]
    bindings: dict[str, str]
    validator_groups: dict[str, int | None]
    observer_groups: dict[str, int | None]
    storage_peers: dict[str, str | None]
    replication: dict[str, int]
    routing: dict[str, Any]
    machines: list[str]
    networks: list[str]
    sites: dict[str, str]
    overrides: dict[str, str]
    api_replicas: dict[str, dict[str, Any]]
    resolver_instances: dict[str, dict[str, Any]]
    blockchain_advanced: dict[str, Any]
    defaults: dict[str, Any]
    secret_ids: list[str]
    legacy_access: bool
    settings_overrides: dict[str, Any]
    component_overrides: dict[str, Any]
    objectives: list[str]
    acknowledgements: list[str]
    # The authored web edge and the NAT/announce overrides, verbatim, so the
    # panels can edit them without a second source of truth.
    proxies: dict[str, Any]
    advertise: dict[str, Any]


@dataclass(frozen=True)
class TopologyGraph:
    deployment_id: str
    label: str | None
    profile: str | None
    inventory_format: str
    inventory_path: str
    docker_subnets: dict[str, str]
    routing: dict[str, str]
    families: list[str]
    machines: list[MachineNode]
    networks: list[NetworkNode]
    groups: list[GroupNode]
    services: list[ServiceNode]
    edges: list[Edge]
    availability: AvailabilityView
    warnings: list[str]
    operator: OperatorView | None = None
    # Site-provided reachability between networks. Service connections ride a
    # network; when two machines do not share one, a route is what makes the
    # connection possible at all.
    routes: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _routes(raw: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in raw.get("routes") or []:
        if not isinstance(entry, dict):
            continue
        source, target = entry.get("from"), entry.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        row = {"from": source, "to": target}
        if isinstance(entry.get("via"), str):
            row["via"] = entry["via"]
        rows.append(row)
    return rows


def _operator_view(source: dict[str, Any]) -> OperatorView | None:
    if source.get("format") != "dark-operator-inventory":
        return None

    def _int_map(value: Any, field_name: str) -> dict[str, int | None]:
        if not isinstance(value, dict):
            return {}
        return {
            key: (item.get(field_name) if isinstance(item, dict) else None)
            for key, item in value.items()
        }

    blockchain = source.get("blockchain") if isinstance(source.get("blockchain"), dict) else {}
    rpc = blockchain.get("rpc") if isinstance(blockchain.get("rpc"), dict) else {}
    storage = source.get("storage") if isinstance(source.get("storage"), dict) else {}
    replication = storage.get("replication") if isinstance(storage.get("replication"), dict) else {}
    policy = source.get("availability") if isinstance(source.get("availability"), dict) else {}
    deployment = source.get("deployment") if isinstance(source.get("deployment"), dict) else {}
    overrides = source.get("overrides") if isinstance(source.get("overrides"), dict) else {}

    return OperatorView(
        deployment_id=str(deployment.get("id", "")),
        label=deployment.get("label") if isinstance(deployment.get("label"), str) else None,
        profile=source.get("profile") if isinstance(source.get("profile"), str) else None,
        format_version=source.get("format_version") if isinstance(source.get("format_version"), int) else None,
        primary_rpc=rpc.get("primary") if isinstance(rpc.get("primary"), str) else None,
        rpc_nodes=sorted(rpc.get("nodes") or {}),
        bindings={k: v for k, v in (rpc.get("bindings") or {}).items() if isinstance(v, str)},
        validator_groups=_int_map(blockchain.get("validator_groups"), "validator_count"),
        observer_groups=_int_map(blockchain.get("observer_groups"), "observer_count"),
        storage_peers={
            key: (item.get("group") if isinstance(item, dict) else None)
            for key, item in (storage.get("peers") or {}).items()
        },
        replication={k: v for k, v in replication.items() if isinstance(v, int)},
        routing={k: v for k, v in (source.get("routing") or {}).items() if isinstance(v, (str, dict))},
        machines=sorted(source.get("machines") or {}),
        networks=sorted(source.get("networks") or {}),
        sites={key: value.get("lan") for key, value in (source.get("sites") or {}).items()
               if isinstance(value, dict) and isinstance(value.get("lan"), str)},
        overrides={
            key: item["group"]
            for key, item in overrides.items()
            if isinstance(item, dict) and isinstance(item.get("group"), str)
        },
        api_replicas={key: value for key, value in (storage.get("readers") or {}).items()
                      if isinstance(value, dict)},
        resolver_instances={key: value for key, value in (source.get("resolver", {}).get("instances", {})
                                                          if isinstance(source.get("resolver"), dict) else {}).items()
                            if isinstance(value, dict)},
        blockchain_advanced={
            key: blockchain.get(key) for key in ("chain_id", "observer_max_block_lag", "artifact", "qbft")
            if key in blockchain
        },
        defaults=source.get("defaults") if isinstance(source.get("defaults"), dict) else {},
        secret_ids=sorted((source.get("secrets") or {}).keys()) if isinstance(source.get("secrets"), dict) else [],
        legacy_access=isinstance(source.get("access"), dict),
        settings_overrides=overrides.get("settings") if isinstance(overrides.get("settings"), dict) else {},
        component_overrides=overrides.get("components") if isinstance(overrides.get("components"), dict) else {},
        objectives=[item for item in (policy.get("objectives") or []) if isinstance(item, str)],
        acknowledgements=[item for item in (policy.get("acknowledgements") or []) if isinstance(item, str)],
        proxies={
            key: value
            for key, value in ((source.get("proxies") or {}) if isinstance(source.get("proxies"), dict) else {}).items()
            if isinstance(value, dict)
        },
        advertise={
            key: value
            for key, value in (
                _section_dict(source, "networking", "services") or {}
            ).items()
            if isinstance(value, dict)
        },
    )


def _section_dict(source: dict[str, Any], *path: str) -> dict | None:
    node: Any = source
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def _endpoint_index(endpoints) -> dict[tuple[str, str | None], Any]:
    index: dict[tuple[str, str | None], Any] = {}
    for endpoint in endpoints:
        index.setdefault((endpoint.service, endpoint.network), endpoint)
    return index


def build_graph(
    plan,
    availability,
    source: dict[str, Any],
    warnings: list[str],
    *,
    inventory_path: str | None = None,
) -> TopologyGraph:
    """Turn a resolved plan into a canvas graph. Pure: no I/O, no side effects."""
    services = plan.services
    by_id = {service.id: service for service in services}
    machines = plan.machines
    machine_by_id = {machine.id: machine for machine in machines}
    group_of = {service_id: group.id for group in plan.groups for service_id in group.service_ids}
    endpoints = _endpoint_index(plan.endpoints)

    families = sorted(
        {_family(service.type) for service in services},
        key=lambda name: (FAMILY_ORDER.index(name) if name in FAMILY_ORDER else 99, name),
    )

    service_nodes = [
        ServiceNode(
            id=service.id,
            type=service.type,
            family=_family(service.type),
            node_kind="scalable" if service.type in SCALABLE_TYPES else "singleton",
            machine_id=service.machine_id,
            group_id=group_of.get(service.id, ""),
            subtitle=_subtitle(service),
            exposure=dict(service.exposure) if service.exposure else None,
        )
        for service in sorted(
            services,
            key=lambda item: (
                item.machine_id,
                FAMILY_ORDER.index(_family(item.type)) if _family(item.type) in FAMILY_ORDER else 99,
                item.id,
            ),
        )
    ]

    edges: list[Edge] = []
    for consumer in services:
        for name, connection in consumer.connections.items():
            provider_id = connection["service"]
            provider = by_id.get(provider_id)
            if provider is None:
                continue
            network = connection.get("network")
            # contracts-deploy is a one-shot job: consumers depend on it but do
            # not open a socket to it, so it is an ordering edge, not a link.
            routed = provider.type != "contracts-deploy"
            crosses = routed and provider.machine_id != consumer.machine_id
            endpoint = endpoints.get((provider_id, network)) if routed else None
            edges.append(
                Edge(
                    consumer=consumer.id,
                    provider=provider_id,
                    name=name,
                    network=network,
                    protocol=connection.get("protocol"),
                    transport="dependency" if not routed else ("private" if crosses else "docker"),
                    routed=routed,
                    crosses_host=crosses,
                    host=endpoint.host if endpoint else None,
                    port=endpoint.port if endpoint else None,
                )
            )
    edges.sort(key=lambda edge: (not edge.crosses_host, edge.consumer, edge.provider, edge.name))

    groups_by_machine: dict[str, list[str]] = {}
    for group in plan.groups:
        groups_by_machine.setdefault(group.machine_id, []).append(group.id)
    service_count: dict[str, int] = {}
    for service in services:
        service_count[service.machine_id] = service_count.get(service.machine_id, 0) + 1

    def _fate(scenario) -> dict[str, str]:
        return {
            "consensus": scenario.consensus,
            "storage": scenario.storage,
            "observers": scenario.observers,
            "applications_rpc": scenario.applications_rpc,
        }

    machine_fate = {
        scenario.target: scenario
        for scenario in availability.scenarios
        if scenario.kind == "machine"
    }
    group_fate = {
        scenario.target: scenario
        for scenario in availability.scenarios
        if scenario.kind == "validator_group"
    }

    machine_nodes = [
        MachineNode(
            id=machine.id,
            execution=machine.execution,
            management_address=machine.management_address,
            addresses=dict(machine.addresses),
            docker_subnet=plan.docker_subnets.get(machine.id),
            site=machine.site,
            group_ids=sorted(groups_by_machine.get(machine.id, [])),
            service_count=service_count.get(machine.id, 0),
            if_lost=_fate(machine_fate[machine.id]) if machine.id in machine_fate else None,
        )
        for machine in sorted(machines, key=lambda item: item.id)
    ]

    machine_networks: dict[str, list[str]] = {network.id: [] for network in plan.networks}
    for machine in machines:
        for network_id in machine.addresses:
            machine_networks.setdefault(network_id, []).append(machine.id)
    network_nodes = [
        NetworkNode(
            id=network.id,
            kind=network.kind,
            cidr=network.cidr,
            machine_ids=sorted(machine_networks.get(network.id, [])),
        )
        for network in sorted(plan.networks, key=lambda item: item.id)
    ]

    group_nodes = [
        GroupNode(
            id=group.id,
            kind=group.kind,
            machine_id=group.machine_id,
            service_ids=list(group.service_ids),
            members=list(group.members),
            if_lost=(
                {"consensus": group_fate[group.id].consensus}
                if group.id in group_fate
                else None
            ),
        )
        for group in sorted(plan.groups, key=lambda item: (item.machine_id, item.id))
    ]

    availability_view = AvailabilityView(
        validators=availability.validators,
        quorum=availability.quorum,
        validator_failures_tolerated=availability.validator_failures_tolerated,
        primary_rpc=availability.primary_rpc,
        blockchain_full_copies=availability.blockchain_full_copies,
        storage_peers=availability.storage_peers,
        storage_publish_after_replicas=availability.storage_publish_after_replicas,
        storage_target_replicas=availability.storage_target_replicas,
        warnings=list(availability.warnings),
    )

    routing = {key: value for key, value in (source.get("routing") or {}).items()
               if isinstance(value, (str, dict))}

    return TopologyGraph(
        deployment_id=plan.deployment_id,
        label=(source.get("deployment") or {}).get("label"),
        profile=source.get("profile"),
        inventory_format=(
            "operator" if source.get("format") == "dark-operator-inventory" else "v3"
        ),
        inventory_path=inventory_path or str(plan.inventory_path),
        docker_subnets=dict(plan.docker_subnets),
        routing=routing,
        families=families,
        machines=machine_nodes,
        networks=network_nodes,
        groups=group_nodes,
        services=service_nodes,
        edges=edges,
        availability=availability_view,
        warnings=list(warnings),
        operator=_operator_view(source),
        routes=_routes(plan.raw if isinstance(plan.raw, dict) else {}),
    )
