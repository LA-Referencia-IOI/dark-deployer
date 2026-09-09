"""Typed, side-effect free objects used by deployment v2."""

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
    private_address: str
    ssh: SshSettings
    workspace_root: str
    data_root: str
    secrets_root: str
    docker_subnet: str | None = None


@dataclass(frozen=True)
class Group:
    id: str
    kind: Literal["apps", "validators", "storage"]
    machine_id: str
    members: tuple[str, ...]
    explorer: bool = False


@dataclass(frozen=True)
class Network:
    id: str
    kind: Literal["lan", "vpn"]
    cidr: str


@dataclass(frozen=True)
class Endpoint:
    service: str
    host: str
    port: int
    transport: Literal["docker", "private"]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class PlanStep:
    id: str
    machine_id: str
    group_id: str
    action: str
    depends_on: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class DeploymentPlan:
    deployment_id: str
    inventory_path: Path
    machines: tuple[Machine, ...]
    groups: tuple[Group, ...]
    networks: tuple[Network, ...]
    endpoints: tuple[Endpoint, ...]
    docker_subnets: dict[str, str]
    steps: tuple[PlanStep, ...]
    raw: dict

    def machine(self, machine_id: str) -> Machine:
        return next(machine for machine in self.machines if machine.id == machine_id)

    def group(self, group_id: str) -> Group:
        return next(group for group in self.groups if group.id == group_id)
