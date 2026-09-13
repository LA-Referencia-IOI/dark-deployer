"""Endpoint and local Docker-network derivation for inventory v3."""

from __future__ import annotations

import ipaddress

from .inventory import InventoryError
from .model import Endpoint, Machine, ServiceInstance

DEFAULT_PORTS = {
    "besu-rpc": 8545, "besu-observer": 8545, "admin-api": 8000, "minter-api": 8001,
    "resolver-api": 8002, "store-api": 8003, "dashboard": 8080,
    "explorer": 80, "edge-proxy": 80, "ipfs-kubo": 5001, "ipfs-cluster": 9094,
    "minter-postgres": 5432, "dashboard-mysql": 3306,
    "dashboard-redis": 6379,
}


def allocate_docker_subnets(machines: tuple[Machine, ...], raw: dict) -> dict[str, str]:
    pool = ipaddress.ip_network(raw["defaults"]["docker"]["subnet_pool"])
    prefix = raw["defaults"]["docker"]["subnet_prefix"]
    candidates = iter(pool.subnets(new_prefix=prefix))
    allocated: dict[str, str] = {}
    physical = [
        ipaddress.ip_network(cidr)
        for item in raw["networks"].values()
        for cidr in (item.get("cidrs") or [item["cidr"]])
    ]
    for machine in sorted(machines, key=lambda item: item.id):
        network = ipaddress.ip_network(machine.docker_subnet) if machine.docker_subnet else next(candidates, None)
        if network is None or any(network.overlaps(item) for item in physical) or any(network.overlaps(ipaddress.ip_network(value)) for value in allocated.values()):
            raise InventoryError(f"deployment topology v3: invalid Docker subnet for {machine.id}")
        allocated[machine.id] = str(network)
    return allocated


def internal_port(service: ServiceInstance) -> int:
    if service.type == "edge-proxy" and service.configuration.get("tls", {}).get("mode") == "direct":
        return 443
    return int(service.configuration.get("port", DEFAULT_PORTS.get(service.type, 0)))


def endpoint_for(service: ServiceInstance, provider: Machine, consumer: Machine, connection: dict[str, str]) -> Endpoint:
    if provider.id == consumer.id:
        return Endpoint(service.id, service.id, internal_port(service), "docker")
    if not service.exposure or service.exposure.get("mode") != "private":
        raise InventoryError(f"deployment topology v3: {service.id} must expose a private port for remote consumers")
    network = connection.get("network")
    if not network or service.exposure.get("network") != network:
        raise InventoryError(f"deployment topology v3: {service.id} must expose on the selected connection network")
    return Endpoint(
        service.id,
        str(service.exposure.get("advertise_address") or provider.address_on(network)),
        int(service.exposure.get("advertise_port") or service.exposure["port"]),
        "private",
        network,
    )


def derive_endpoints(machines: tuple[Machine, ...], services: tuple[ServiceInstance, ...], raw: dict) -> tuple[Endpoint, ...]:
    by_machine = {item.id: item for item in machines}
    by_id = {item.id: item for item in services}
    result: dict[tuple[str, str], Endpoint] = {}
    for consumer in services:
        for connection in consumer.connections.values():
            provider = by_id[connection["service"]]
            if provider.type == "contracts-deploy":
                continue
            result[(consumer.id, provider.id)] = endpoint_for(provider, by_machine[provider.machine_id], by_machine[consumer.machine_id], connection)
    return tuple(result.values())


def host_bind_address(machine: Machine, exposure: dict | None) -> str | None:
    if not exposure or exposure.get("mode") == "none":
        return None
    if exposure["mode"] == "loopback":
        return "127.0.0.1"
    if exposure["mode"] == "public":
        return "0.0.0.0"
    return machine.address_on(exposure["network"])


def p2p_endpoint(machine: Machine, service: ServiceInstance, network_id: str, default_port: int) -> tuple[str, int]:
    """Return the address peers should dial, distinct from Docker's bind IP."""
    exposure = service.exposure or {}
    host = str(exposure.get("advertise_address") or machine.address_on(network_id))
    port = int(service.configuration.get("p2p_advertise_port", default_port))
    return host, port


def has_remote_peer(plan, service_types: set[str]) -> bool:
    """Whether a peer family crosses a Docker host boundary."""
    return len({service.machine_id for service in plan.services if service.type in service_types}) > 1


def chain_node_addresses(plan) -> dict[str, str]:
    """Use Docker addresses only when every Besu node shares one host."""
    result = {}
    remote = has_remote_peer(plan, {"besu-rpc", "besu-validator", "besu-observer"})
    for index, service in enumerate((item for item in plan.services if item.type in {"besu-rpc", "besu-validator", "besu-observer"}), start=10):
        machine = plan.machine(service.machine_id)
        node_id = service.configuration["node_id"]
        if not remote:
            subnet = ipaddress.ip_network(plan.docker_subnets[machine.id])
            result[node_id] = str(subnet.network_address + index)
        else:
            # All Besu nodes use the network selected by the infrastructure
            # policy. The validator rejects absent addresses before rendering.
            result[node_id] = machine.address_on(plan.raw["infrastructure"]["besu"]["network"])
    return result


def chain_node_ports(plan) -> dict[str, int]:
    """Return the host P2P port each Besu node announces to remote peers."""
    start = int(plan.raw["infrastructure"]["besu"]["p2p_port_start"])
    def node_role(node_id: str) -> str:
        definition = plan.raw["blockchain"]["nodes"][node_id]
        return definition["role"]
    rank = {"validator": 0, "rpc": 1, "observer": 2}
    nodes = tuple(sorted(plan.raw["blockchain"]["nodes"], key=lambda node: (rank[node_role(node)], node)))
    remote = has_remote_peer(plan, {"besu-rpc", "besu-validator", "besu-observer"})
    by_node = {
        service.configuration["node_id"]: service
        for service in plan.services
        if service.type in {"besu-rpc", "besu-validator", "besu-observer"}
    }
    return {
        node: int(by_node[node].configuration.get("p2p_advertise_port", start + index if remote else 30303))
        for index, node in enumerate(nodes)
    }
