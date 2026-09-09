"""Strict loading and semantic validation for deployment topology v2."""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .model import Group, Machine, Network, SshSettings


class InventoryError(ValueError):
    """The inventory is malformed or describes an impossible deployment."""


ROOT = Path(__file__).resolve().parent
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*$")
REQUIRED_COMPONENTS = frozenset(
    {
        "dark-dapp", "dark-explorador", "dark-core-lib", "dark-core-admin-api",
        "dark-core-resolver-api", "dark-store-api", "dark-core-minter-api",
        "dashboard-web", "dark-ipfs",
    }
)


def _load_schema() -> dict[str, Any]:
    return json.loads((ROOT / "schema.json").read_text())


def _error(message: str) -> InventoryError:
    return InventoryError(f"deployment topology v2: {message}")


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise _error(f"{path} must be a lowercase identifier")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{path} must be a non-empty string")
    return value.strip()


def _absolute_path(value: Any, path: str) -> str:
    raw = _string(value, path)
    if not Path(raw).is_absolute():
        raise _error(f"{path} must be an absolute path")
    return raw


def _ipv4(value: Any, path: str) -> str:
    try:
        parsed = ipaddress.ip_address(_string(value, path))
    except ValueError as exc:
        raise _error(f"{path} must be an IPv4 address") from exc
    if parsed.version != 4:
        raise _error(f"{path} must be an IPv4 address")
    return str(parsed)


def _cidr(value: Any, path: str) -> str:
    try:
        parsed = ipaddress.ip_network(_string(value, path), strict=True)
    except ValueError as exc:
        raise _error(f"{path} must be a CIDR network") from exc
    if parsed.version != 4:
        raise _error(f"{path} must be an IPv4 CIDR")
    return str(parsed)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{path} must be an object")
    return value


def _validate_schema(document: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(_load_schema()).iter_errors(document), key=str)
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise _error(f"{location}: {error.message}")


def _ssh(raw: dict[str, Any], defaults: dict[str, Any], path: str) -> SshSettings:
    merged = dict(defaults)
    merged.update(raw)
    port = merged.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise _error(f"{path}.port must be a TCP port")
    known_hosts = merged.get("known_hosts_file")
    return SshSettings(
        user=_string(merged.get("user"), f"{path}.user"),
        port=port,
        private_key_file=_absolute_path(merged.get("private_key_file"), f"{path}.private_key_file"),
        known_hosts_file=_absolute_path(known_hosts, f"{path}.known_hosts_file") if known_hosts else None,
    )


def load_inventory(path: Path) -> tuple[dict[str, Any], tuple[Machine, ...], tuple[Group, ...], tuple[Network, ...]]:
    """Load v2 JSON and return canonical domain objects without side effects."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise _error(f"inventory not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise _error(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise _error("root must be an object")
    _validate_schema(raw)

    deployment = _object(raw["deployment"], "deployment")
    _identifier(deployment.get("id"), "deployment.id")
    defaults = _object(raw["defaults"], "defaults")
    defaults_ssh = _object(defaults.get("ssh"), "defaults.ssh")
    defaults_paths = _object(defaults.get("paths"), "defaults.paths")
    for field in ("workspace_root", "data_root", "secrets_root"):
        _absolute_path(defaults_paths.get(field), f"defaults.paths.{field}")
    docker = _object(defaults.get("docker"), "defaults.docker")
    subnet_pool = _cidr(docker.get("subnet_pool"), "defaults.docker.subnet_pool")
    prefix = docker.get("subnet_prefix")
    if not isinstance(prefix, int) or not 20 <= prefix <= 30:
        raise _error("defaults.docker.subnet_prefix must be an integer from 20 to 30")
    if prefix < ipaddress.ip_network(subnet_pool).prefixlen:
        raise _error("defaults.docker.subnet_prefix cannot be wider than subnet_pool")

    networks: list[Network] = []
    for network_id, definition in _object(raw["networks"], "networks").items():
        _identifier(network_id, f"networks.{network_id}")
        definition = _object(definition, f"networks.{network_id}")
        kind = definition.get("kind")
        if kind not in {"lan", "vpn"}:
            raise _error(f"networks.{network_id}.kind must be lan or vpn")
        networks.append(Network(network_id, kind, _cidr(definition.get("cidr"), f"networks.{network_id}.cidr")))
    if not networks:
        raise _error("networks cannot be empty")
    private_network_id = _string(deployment.get("private_network"), "deployment.private_network")
    if private_network_id not in {network.id for network in networks}:
        raise _error("deployment.private_network must reference networks")

    machines: list[Machine] = []
    addresses_seen: set[str] = set()
    for machine_id, definition in _object(raw["machines"], "machines").items():
        _identifier(machine_id, f"machines.{machine_id}")
        definition = _object(definition, f"machines.{machine_id}")
        execution = definition.get("execution")
        if execution not in {"local", "ssh", "auto"}:
            raise _error(f"machines.{machine_id}.execution must be local, ssh or auto")
        addresses = _object(definition.get("addresses"), f"machines.{machine_id}.addresses")
        private_address = _ipv4(addresses.get(private_network_id), f"machines.{machine_id}.addresses.{private_network_id}")
        if private_address in addresses_seen:
            raise _error(f"machines.{machine_id} duplicates private address {private_address}")
        addresses_seen.add(private_address)
        private_cidr = next(network.cidr for network in networks if network.id == private_network_id)
        if ipaddress.ip_address(private_address) not in ipaddress.ip_network(private_cidr):
            raise _error(f"machines.{machine_id} private address is outside {private_network_id}")
        override_paths = dict(defaults_paths)
        override_paths.update(_object(definition.get("paths", {}), f"machines.{machine_id}.paths"))
        machine_docker = _object(definition.get("docker", {}), f"machines.{machine_id}.docker")
        declared_subnet = machine_docker.get("subnet")
        if declared_subnet:
            declared_subnet = _cidr(declared_subnet, f"machines.{machine_id}.docker.subnet")
        machines.append(Machine(
            id=machine_id,
            execution=execution,
            management_address=_string(definition.get("management_address"), f"machines.{machine_id}.management_address"),
            private_address=private_address,
            ssh=_ssh(_object(definition.get("ssh", {}), f"machines.{machine_id}.ssh"), defaults_ssh, f"machines.{machine_id}.ssh"),
            workspace_root=_absolute_path(override_paths["workspace_root"], f"machines.{machine_id}.paths.workspace_root"),
            data_root=_absolute_path(override_paths["data_root"], f"machines.{machine_id}.paths.data_root"),
            secrets_root=_absolute_path(override_paths["secrets_root"], f"machines.{machine_id}.paths.secrets_root"),
            docker_subnet=declared_subnet,
        ))

    groups: list[Group] = []
    declared_members: set[str] = set()
    for group_id, definition in _object(raw["groups"], "groups").items():
        _identifier(group_id, f"groups.{group_id}")
        definition = _object(definition, f"groups.{group_id}")
        kind = definition.get("kind")
        if kind not in {"apps", "validators", "storage"}:
            raise _error(f"groups.{group_id}.kind must be apps, validators or storage")
        machine_id = _string(definition.get("machine"), f"groups.{group_id}.machine")
        if machine_id not in {machine.id for machine in machines}:
            raise _error(f"groups.{group_id}.machine references unknown machine {machine_id}")
        members = definition.get("members")
        if not isinstance(members, list) or not members or not all(isinstance(item, str) for item in members):
            raise _error(f"groups.{group_id}.members must be a non-empty string list")
        if len(set(members)) != len(members) or declared_members.intersection(members):
            raise _error(f"groups.{group_id}.members contains duplicated member IDs")
        declared_members.update(members)
        explorer = definition.get("explorer", False)
        if not isinstance(explorer, bool) or explorer and kind != "validators":
            raise _error(f"groups.{group_id}.explorer is only valid for validators")
        groups.append(Group(group_id, kind, machine_id, tuple(members), explorer))
    _validate_domain(raw, groups)
    return raw, tuple(machines), tuple(groups), tuple(networks)


def _validate_domain(raw: dict[str, Any], groups: list[Group]) -> None:
    apps = [group for group in groups if group.kind == "apps"]
    validator_groups = [group for group in groups if group.kind == "validators"]
    storage_groups = [group for group in groups if group.kind == "storage"]
    if len(apps) != 1:
        raise _error("exactly one apps group is required")
    if len(validator_groups) != 2 or sum(group.explorer for group in validator_groups) != 1:
        raise _error("exactly two validator groups and one explorer group are required")
    if not storage_groups:
        raise _error("at least one storage group is required")
    blockchain = _object(raw["blockchain"], "blockchain")
    nodes = _object(blockchain.get("nodes"), "blockchain.nodes")
    validators = [node_id for node_id, item in nodes.items() if _object(item, f"blockchain.nodes.{node_id}").get("validator") is True]
    rpc = [node_id for node_id, item in nodes.items() if _object(item, f"blockchain.nodes.{node_id}").get("validator") is False]
    if len(validators) != 4 or len(rpc) != 1:
        raise _error("blockchain.nodes requires four validators and one non-validator RPC")
    if rpc[0] not in apps[0].members:
        raise _error("the non-validator RPC must belong to the apps group")
    if set(validators) != {member for group in validator_groups for member in group.members}:
        raise _error("validator group members must exactly match blockchain validators")
    storage = _object(raw["storage"], "storage")
    node_ids = storage.get("nodes")
    if not isinstance(node_ids, list) or set(node_ids) != {member for group in storage_groups for member in group.members}:
        raise _error("storage.nodes must exactly match storage group members")
    replication = _object(storage.get("replication"), "storage.replication")
    publish = replication.get("publish_after_replicas")
    target = replication.get("target_replicas")
    if not isinstance(publish, int) or not isinstance(target, int) or not 1 <= publish <= target <= len(node_ids):
        raise _error("storage replication must satisfy 1 <= publish_after_replicas <= target_replicas <= storage nodes")
    components = _object(raw["components"], "components")
    missing = REQUIRED_COMPONENTS.difference(components)
    if missing:
        raise _error("components missing: " + ", ".join(sorted(missing)))
    for component, definition in components.items():
        definition = _object(definition, f"components.{component}")
        _string(definition.get("repository_url"), f"components.{component}.repository_url")
