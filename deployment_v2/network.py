"""Deterministic network allocation and endpoint selection."""

from __future__ import annotations

import ipaddress

from .inventory import InventoryError
from .model import Endpoint, Group, Machine


_CHAIN_NODE_ORDER = ("validator01", "validator02", "validator03", "validator04", "rpc01")
SERVICE_DEFAULT_PORTS = {
    "blockchain-rpc": 8545, "admin-api": 8000, "minter-api": 8001,
    "resolver-api": 8002, "store-api": 8003, "dashboard": 8081,
    "explorer": 25000,
}


def exposed_service_port(raw: dict, service: str) -> int:
    """Return the sole host port for an explicitly exposed service."""
    definition = raw["exposure"]["services"].get(service)
    if not definition:
        return 0
    return int(definition["port"])


def exposure_bind_address(machine: Machine, raw: dict, service: str) -> str | None:
    definition = raw["exposure"]["services"].get(service)
    if not definition:
        return None
    return "127.0.0.1" if definition["bind"] == "loopback" else machine.private_address


def allocate_docker_subnets(machines: tuple[Machine, ...], raw: dict) -> dict[str, str]:
    docker = raw["defaults"]["docker"]
    pool = ipaddress.ip_network(docker["subnet_pool"])
    prefix = docker["subnet_prefix"]
    candidates = iter(pool.subnets(new_prefix=prefix))
    allocated: dict[str, str] = {}
    private_network = ipaddress.ip_network(raw["networks"][raw["deployment"]["private_network"]]["cidr"])
    for machine in sorted(machines, key=lambda item: item.id):
        network = ipaddress.ip_network(machine.docker_subnet) if machine.docker_subnet else next(candidates, None)
        if network is None:
            raise InventoryError("deployment topology v2: docker subnet pool is exhausted")
        if network.overlaps(private_network):
            raise InventoryError(f"deployment topology v2: Docker subnet {network} for {machine.id} overlaps private network")
        if any(network.overlaps(ipaddress.ip_network(existing)) for existing in allocated.values()):
            raise InventoryError(f"deployment topology v2: Docker subnet {network} is duplicated or overlaps another machine")
        allocated[machine.id] = str(network)
    return allocated


def endpoint_for(service: str, provider_machine: Machine, consumer_machine: Machine, port: int) -> Endpoint:
    """Return the only address family a consumer is allowed to use."""
    if provider_machine.id == consumer_machine.id:
        return Endpoint(service, service, port, "docker")
    return Endpoint(service, provider_machine.private_address, port, "private")


def derive_endpoints(machines: tuple[Machine, ...], groups: tuple[Group, ...], raw: dict) -> tuple[Endpoint, ...]:
    by_machine = {machine.id: machine for machine in machines}
    apps = next(group for group in groups if group.kind == "apps")
    apps_machine = by_machine[apps.machine_id]
    result: list[Endpoint] = []
    for group in groups:
        provider = by_machine[group.machine_id]
        if group.kind == "storage":
            colocated = sorted(
                (candidate for candidate in groups if candidate.kind == "storage" and candidate.machine_id == provider.id),
                key=lambda candidate: candidate.id,
            )
            slot = colocated.index(group)
            port = 9094 if provider.id == apps_machine.id else 9094 + (slot * 3)
            result.append(endpoint_for(f"cluster-{group.id}", provider, apps_machine, port))
            ipfs_port = 5001 if provider.id == apps_machine.id else 5001 + slot
            result.append(endpoint_for(f"ipfs-{group.members[0]}", provider, apps_machine, ipfs_port))
        elif group.kind == "apps":
            for name in ("blockchain-rpc", "admin-api", "minter-api", "resolver-api", "store-api", "dashboard"):
                result.append(endpoint_for(name, provider, provider, exposed_service_port(raw, name) or SERVICE_DEFAULT_PORTS[name]))
    return tuple(result)


def chain_node_addresses(plan) -> dict[str, str]:
    """Return the address every Besu node must advertise to its peers.

    A local execution is a multi-host simulation on one Docker daemon, so the
    only routable addresses between its Compose projects are fixed addresses on
    that machine's shared bridge.  An SSH deployment instead uses its real
    private/VPN address, with the node-specific published P2P port providing
    the distinction between co-located validators.
    """
    assignments = {
        member: plan.machine(group.machine_id)
        for group in plan.groups
        if group.kind in {"apps", "validators"}
        for member in group.members
    }
    addresses: dict[str, str] = {}
    for index, node in enumerate(_CHAIN_NODE_ORDER, start=10):
        machine = assignments[node]
        if machine.execution == "local":
            subnet = ipaddress.ip_network(plan.docker_subnets[machine.id])
            address = subnet.network_address + index
            if address not in subnet or address == subnet.broadcast_address:
                raise InventoryError(f"deployment topology v2: Docker subnet {subnet} for {machine.id} is too small for local QBFT addresses")
            addresses[node] = str(address)
        else:
            addresses[node] = machine.private_address
    return addresses


def host_bind_address(machine: Machine) -> str:
    """Local simulations publish only on loopback; servers use their VPN/LAN IP."""
    return "127.0.0.1" if machine.execution == "local" else machine.private_address


def storage_container_addresses(plan) -> dict[str, dict[str, str]]:
    """Allocate stable Kubo/Cluster addresses only inside local simulations.

    Production peers live on separate Docker daemons.  Their routable identity
    is the host VPN address plus published ports, so assigning a container IP
    adds no value there.  A local multi-host simulation needs stable bridge
    addresses instead of the inventory's conceptual VPN addresses.
    """
    result: dict[str, dict[str, str]] = {}
    by_machine: dict[str, int] = {}
    for group in sorted((item for item in plan.groups if item.kind == "storage"), key=lambda item: item.id):
        machine = plan.machine(group.machine_id)
        if machine.execution != "local":
            continue
        slot = by_machine.get(machine.id, 0)
        by_machine[machine.id] = slot + 1
        subnet = ipaddress.ip_network(plan.docker_subnets[machine.id])
        # Reserve .10-.14 for QBFT nodes, .100+ for storage pairs.
        ipfs = subnet.network_address + 100 + (slot * 2)
        cluster = ipfs + 1
        if ipfs not in subnet or cluster not in subnet or ipfs == subnet.broadcast_address or cluster == subnet.broadcast_address:
            raise InventoryError(f"deployment topology v2: Docker subnet {subnet} for {machine.id} is too small for local storage addresses")
        result[group.id] = {"ipfs": str(ipfs), "cluster": str(cluster)}
    return result


def storage_announce_address(plan, group: Group) -> str:
    machine = plan.machine(group.machine_id)
    local = storage_container_addresses(plan).get(group.id)
    return local["ipfs"] if local else machine.private_address
