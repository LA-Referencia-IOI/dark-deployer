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


def _only_keys(value: dict[str, Any], path: str, allowed: set[str]) -> None:
    unknown = sorted(set(value).difference(allowed))
    if unknown:
        raise _error(f"{path} contains unknown field(s): " + ", ".join(unknown))


def _validate_schema(document: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(_load_schema()).iter_errors(document), key=str)
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise _error(f"{location}: {error.message}")


def _ssh(raw: dict[str, Any], defaults: dict[str, Any], path: str) -> SshSettings:
    _only_keys(raw, path, {"user", "port", "private_key_file", "known_hosts_file"})
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
    _only_keys(deployment, "deployment", {"id", "label", "private_network"})
    _identifier(deployment.get("id"), "deployment.id")
    defaults = _object(raw["defaults"], "defaults")
    _only_keys(defaults, "defaults", {"ssh", "paths", "docker"})
    defaults_ssh = _object(defaults.get("ssh"), "defaults.ssh")
    defaults_paths = _object(defaults.get("paths"), "defaults.paths")
    _only_keys(defaults_ssh, "defaults.ssh", {"user", "port", "private_key_file", "known_hosts_file"})
    _only_keys(defaults_paths, "defaults.paths", {"workspace_root", "data_root", "secrets_root"})
    for field in ("workspace_root", "data_root", "secrets_root"):
        _absolute_path(defaults_paths.get(field), f"defaults.paths.{field}")
    docker = _object(defaults.get("docker"), "defaults.docker")
    _only_keys(docker, "defaults.docker", {"subnet_pool", "subnet_prefix"})
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
        _only_keys(definition, f"networks.{network_id}", {"kind", "cidr", "interface"})
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
        _only_keys(definition, f"machines.{machine_id}", {"execution", "management_address", "addresses", "paths", "docker", "ssh"})
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
        local_paths = _object(definition.get("paths", {}), f"machines.{machine_id}.paths")
        _only_keys(local_paths, f"machines.{machine_id}.paths", {"workspace_root", "data_root", "secrets_root"})
        override_paths.update(local_paths)
        machine_docker = _object(definition.get("docker", {}), f"machines.{machine_id}.docker")
        _only_keys(machine_docker, f"machines.{machine_id}.docker", {"subnet"})
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
        _only_keys(definition, f"groups.{group_id}", {"kind", "machine", "members", "explorer"})
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


def _positive_integer(value: Any, path: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise _error(f"{path} must be a positive integer")
    return value


def _validate_settings(settings: dict[str, Any]) -> None:
    """Keep emitted Minter and Store tuning explicit in the topology."""
    _only_keys(settings, "settings", {"minter", "store"})
    minter = _object(settings.get("minter"), "settings.minter")
    _only_keys(minter, "settings.minter", {"shoulder", "metadata", "replication", "chain"})
    shoulder = minter.get("shoulder")
    if not isinstance(shoulder, str) or not re.fullmatch(r"2[0-9]{2}", shoulder):
        raise _error("settings.minter.shoulder must use the 2MM format")

    metadata = _object(minter.get("metadata"), "settings.minter.metadata")
    metadata_required = {"page_size", "concurrency", "min_concurrency"}
    _only_keys(metadata, "settings.minter.metadata", metadata_required)
    if set(metadata) != metadata_required:
        raise _error("settings.minter.metadata is incomplete")
    page_size = _positive_integer(metadata["page_size"], "settings.minter.metadata.page_size")
    concurrency = _positive_integer(metadata["concurrency"], "settings.minter.metadata.concurrency")
    minimum = _positive_integer(metadata["min_concurrency"], "settings.minter.metadata.min_concurrency")
    if page_size > 1_000 or minimum > concurrency:
        raise _error("settings.minter.metadata has incompatible page size or concurrency")

    replication = _object(minter.get("replication"), "settings.minter.replication")
    replication_required = {
        "enabled", "page_size", "concurrency", "status_batch_size", "promotion_batch_size",
        "promotion_pressure_high_percent", "promotion_pressure_medium_percent",
        "promotion_min_batch_size", "maintenance_cycle_seconds", "idle_sleep_seconds",
        "first_pin_recheck_seconds", "first_pin_second_recheck_seconds", "first_pin_max_recheck_seconds",
        "durability_recheck_seconds", "durability_second_recheck_seconds", "durability_max_recheck_seconds",
        "storage_retry_seconds",
    }
    _only_keys(replication, "settings.minter.replication", replication_required)
    if set(replication) != replication_required or not isinstance(replication.get("enabled"), bool):
        raise _error("settings.minter.replication is incomplete or enabled is not boolean")
    values = {
        field: _positive_integer(replication[field], f"settings.minter.replication.{field}")
        for field in replication_required.difference({"enabled"})
    }
    if values["page_size"] > 1_000 or values["status_batch_size"] > 200:
        raise _error("settings.minter.replication page or status batch exceeds its supported limit")
    if values["promotion_batch_size"] > values["page_size"] or values["promotion_min_batch_size"] > values["promotion_batch_size"]:
        raise _error("settings.minter.replication promotion batch settings are incompatible")
    if values["promotion_pressure_medium_percent"] > values["promotion_pressure_high_percent"] or values["promotion_pressure_high_percent"] > 100:
        raise _error("settings.minter.replication promotion pressure percentages are invalid")
    if not (values["first_pin_recheck_seconds"] <= values["first_pin_second_recheck_seconds"] <= values["first_pin_max_recheck_seconds"]):
        raise _error("settings.minter.replication first-pin rechecks must be nondecreasing")
    if not (values["durability_recheck_seconds"] <= values["durability_second_recheck_seconds"] <= values["durability_max_recheck_seconds"]):
        raise _error("settings.minter.replication durability rechecks must be nondecreasing")

    chain = _object(minter.get("chain"), "settings.minter.chain")
    chain_required = {"page_size", "rpc_batch_size"}
    _only_keys(chain, "settings.minter.chain", chain_required)
    if set(chain) != chain_required:
        raise _error("settings.minter.chain is incomplete")
    for field in chain_required:
        _positive_integer(chain[field], f"settings.minter.chain.{field}")

    store = _object(settings.get("store"), "settings.store")
    store_required = {"add_concurrency", "status_concurrency", "promotion_concurrency"}
    _only_keys(store, "settings.store", store_required)
    if set(store) != store_required:
        raise _error("settings.store is incomplete")
    for field in store_required:
        _positive_integer(store[field], f"settings.store.{field}")


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
    storage_by_machine: dict[str, list[Group]] = {}
    for group in storage_groups:
        storage_by_machine.setdefault(group.machine_id, []).append(group)
    for machine_id, assigned in storage_by_machine.items():
        execution = raw["machines"][machine_id]["execution"]
        if len(assigned) > 1 and execution != "local":
            raise _error("a non-local machine may host only one storage group; use separate hosts or an explicit local simulation")
    blockchain = _object(raw["blockchain"], "blockchain")
    _only_keys(blockchain, "blockchain", {"chain_id", "besu_image", "nodes", "qbft", "artifact"})
    artifact = _object(blockchain.get("artifact", {}), "blockchain.artifact")
    if artifact:
        _only_keys(artifact, "blockchain.artifact", {"path"})
        artifact_path = _string(artifact.get("path"), "blockchain.artifact.path")
        if Path(artifact_path).is_absolute() or ".." in Path(artifact_path).parts:
            raise _error("blockchain.artifact.path must be relative to secrets_root")
    qbft = _object(blockchain.get("qbft"), "blockchain.qbft")
    _only_keys(qbft, "blockchain.qbft", {"block_period_seconds", "epoch_length", "request_timeout_seconds"})
    for field in ("block_period_seconds", "epoch_length", "request_timeout_seconds"):
        value = qbft.get(field)
        if not isinstance(value, int) or value < 1:
            raise _error(f"blockchain.qbft.{field} must be a positive integer")
    nodes = _object(blockchain.get("nodes"), "blockchain.nodes")
    validators = []
    rpc = []
    for node_id, item in nodes.items():
        definition = _object(item, f"blockchain.nodes.{node_id}")
        _only_keys(definition, f"blockchain.nodes.{node_id}", {"validator", "p2p_port"})
        if definition.get("validator") is True:
            validators.append(node_id)
        elif definition.get("validator") is False:
            rpc.append(node_id)
    if len(validators) != 4 or len(rpc) != 1:
        raise _error("blockchain.nodes requires four validators and one non-validator RPC")
    if rpc[0] not in apps[0].members:
        raise _error("the non-validator RPC must belong to the apps group")
    if set(validators) != {member for group in validator_groups for member in group.members}:
        raise _error("validator group members must exactly match blockchain validators")
    storage = _object(raw["storage"], "storage")
    _only_keys(storage, "storage", {"cluster_name", "nodes", "replication"})
    node_ids = storage.get("nodes")
    if not isinstance(node_ids, list) or set(node_ids) != {member for group in storage_groups for member in group.members}:
        raise _error("storage.nodes must exactly match storage group members")
    replication = _object(storage.get("replication"), "storage.replication")
    _only_keys(replication, "storage.replication", {"publish_after_replicas", "target_replicas"})
    publish = replication.get("publish_after_replicas")
    target = replication.get("target_replicas")
    if not isinstance(publish, int) or not isinstance(target, int) or not 1 <= publish <= target <= len(node_ids):
        raise _error("storage replication must satisfy 1 <= publish_after_replicas <= target_replicas <= storage nodes")
    _validate_settings(_object(raw["settings"], "settings"))
    components = _object(raw["components"], "components")
    missing = REQUIRED_COMPONENTS.difference(components)
    if missing:
        raise _error("components missing: " + ", ".join(sorted(missing)))
    for component, definition in components.items():
        definition = _object(definition, f"components.{component}")
        _only_keys(definition, f"components.{component}", {"repository_url", "branch"})
        _string(definition.get("repository_url"), f"components.{component}.repository_url")
        branch = definition.get("branch")
        if branch is not None:
            branch = _string(branch, f"components.{component}.branch")
            if branch.startswith("-") or ".." in branch or branch.endswith("/"):
                raise _error(f"components.{component}.branch is not a safe Git branch name")

    secrets = _object(raw["secrets"], "secrets")
    for secret_id, definition in secrets.items():
        _identifier(secret_id, f"secrets.{secret_id}")
        definition = _object(definition, f"secrets.{secret_id}")
        _only_keys(definition, f"secrets.{secret_id}", {"path", "consumers", "format"})
        relative = _string(definition.get("path"), f"secrets.{secret_id}.path")
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise _error(f"secrets.{secret_id}.path must be a safe path relative to secrets_root")
        consumers = definition.get("consumers", [])
        if not isinstance(consumers, list) or not all(isinstance(item, str) for item in consumers):
            raise _error(f"secrets.{secret_id}.consumers must be a string list")
