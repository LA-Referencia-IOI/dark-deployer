"""Typed, side-effect free objects used by deployment v3."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ExecutionMode = Literal["local", "ssh", "auto"]


@dataclass(frozen=True)
class SshSettings:
    user: str
    port: int
    private_key_file: str
    known_hosts_file: str | None


@dataclass(frozen=True)
class Machine:
    id: str
    execution: ExecutionMode
    management_address: str
    addresses: dict[str, str]
    ssh: SshSettings
    workspace_root: str
    data_root: str
    secrets_root: str
    docker_subnet: str | None = None

    def address_on(self, network_id: str) -> str:
        try:
            return self.addresses[network_id]
        except KeyError as exc:
            raise ValueError(f"machine {self.id} has no address on network {network_id}") from exc


@dataclass(frozen=True)
class ServiceInstance:
    id: str
    type: str
    machine_id: str
    # Each connection is {"service": provider_id, "network": network_id?,
    # "protocol": tcp|udp?}. Same-machine connections omit route metadata and
    # keep Docker DNS.
    connections: dict[str, dict[str, str]]
    configuration: dict
    exposure: dict | None = None


@dataclass(frozen=True)
class Group:
    """Logical server/deployment unit, independent of its transport."""

    id: str
    kind: Literal["apps", "validators", "observers", "storage", "server"]
    machine_id: str
    members: tuple[str, ...]
    service_ids: tuple[str, ...]


@dataclass(frozen=True)
class Network:
    id: str
    kind: Literal["lan", "vpn"]
    cidr: str
    cidrs: tuple[str, ...] = ()

    def contains(self, address: str) -> bool:
        import ipaddress
        return any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr) for cidr in (self.cidrs or (self.cidr,)))


@dataclass(frozen=True)
class Endpoint:
    service: str
    host: str
    port: int
    transport: Literal["docker", "private"]
    network: str | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class PlanStep:
    id: str
    machine_id: str
    service_id: str
    action: str
    depends_on: tuple[str, ...]
    description: str
    group_id: str = ""


@dataclass(frozen=True)
class DeploymentPlan:
    deployment_id: str
    inventory_path: Path
    machines: tuple[Machine, ...]
    services: tuple[ServiceInstance, ...]
    networks: tuple[Network, ...]
    endpoints: tuple[Endpoint, ...]
    docker_subnets: dict[str, str]
    steps: tuple[PlanStep, ...]
    raw: dict
    groups: tuple[Group, ...] = ()

    def machine(self, machine_id: str) -> Machine:
        return next(machine for machine in self.machines if machine.id == machine_id)

    def service(self, service_id: str) -> ServiceInstance:
        return next(service for service in self.services if service.id == service_id)

    def primary_rpc(self) -> ServiceInstance:
        """Return the explicitly selected application RPC node."""
        return self.service(self.raw["blockchain"]["primary_rpc"])

    def group(self, group_id: str) -> Group:
        return next(group for group in self.groups if group.id == group_id)
