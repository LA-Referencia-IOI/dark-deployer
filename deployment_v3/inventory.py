"""Strict loading and semantic validation for deployment topology v3."""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

from .model import Group, Machine, Network, ServiceInstance, SshSettings


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
SERVICE_TYPES = frozenset({
    "besu-rpc", "besu-validator", "besu-observer", "explorer", "minter-api", "minter-worker",
    "minter-postgres", "minter-migrate", "admin-api", "resolver-api", "store-api",
    "dashboard", "dashboard-mysql", "dashboard-redis", "dashboard-migrate",
    "ipfs-kubo", "ipfs-cluster", "contracts-deploy", "rpc-probe", "edge-proxy",
})

# The inventory is a typed connection graph, not a bag of strings.  Keeping
# the contract here makes renderer errors actionable before anything is copied
# or started on a host.
CONNECTION_CONTRACTS = {
    "explorer": {"rpc": "besu-rpc"},
    "minter-api": {"database": "minter-postgres", "store_api": "store-api", "rpc": "besu-rpc", "contracts": "contracts-deploy", "migration": "minter-migrate"},
    "minter-worker": {"database": "minter-postgres", "store_api": "store-api", "rpc": "besu-rpc", "contracts": "contracts-deploy", "migration": "minter-migrate"},
    "minter-migrate": {"database": "minter-postgres"},
    "admin-api": {"rpc": "besu-rpc", "contracts": "contracts-deploy"},
    "resolver-api": {"rpc": "besu-rpc", "store_api": "store-api", "contracts": "contracts-deploy"},
    "dashboard": {"database": "dashboard-mysql", "admin_api": "admin-api", "minter_api": "minter-api", "resolver_api": "resolver-api", "store_api": "store-api", "migration": "dashboard-migrate"},
    "dashboard-migrate": {"database": "dashboard-mysql", "admin_api": "admin-api", "minter_api": "minter-api", "resolver_api": "resolver-api", "store_api": "store-api"},
    "ipfs-cluster": {"kubo": "ipfs-kubo"},
    # Proxy connections are named by the inventory routes.  Their type and
    # completeness are validated from ``configuration.sites[].routes`` below.
    "edge-proxy": {},
    "contracts-deploy": {"rpc": "besu-rpc"},
    "rpc-probe": {"rpc": "besu-rpc"},
}


def _load_schema() -> dict[str, Any]:
    return json.loads((ROOT / "schema.json").read_text())


def _error(message: str) -> InventoryError:
    return InventoryError(f"deployment topology v3: {message}")


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


def load_inventory(path: Path) -> tuple[dict[str, Any], tuple[Machine, ...], tuple[ServiceInstance, ...], tuple[Network, ...]]:
    """Load either supported inventory format and return canonical v3 objects."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise _error(f"inventory not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise _error(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise _error("root must be an object")
    # Kept here rather than in the CLI so every existing entry point receives
    # exactly the same resolved document.
    if raw.get("format") == "dark-operator-inventory":
        from .inventory_resolver import resolve_inventory
        raw = resolve_inventory(raw, source_path=path).document
    else:
        # Runtime image versions are an installed release property, not an
        # operator choice. Keep legacy full inventories on the same central
        # image set as compact inventories and discard the former Besu pin.
        from .catalogs import get_catalog
        raw["images"] = dict(get_catalog("dark-platform-baseline-v1.0").document["images"])
        raw.get("blockchain", {}).pop("besu_image", None)
    return validate_inventory(raw)


def validate_inventory(raw: dict[str, Any]) -> tuple[dict[str, Any], tuple[Machine, ...], tuple[ServiceInstance, ...], tuple[Network, ...]]:
    """Validate an already parsed v3 document without filesystem side effects."""
    if not isinstance(raw, dict):
        raise _error("root must be an object")
    _validate_schema(raw)

    images = _object(raw["images"], "images")
    required_images = {"besu", "postgres", "mysql", "dashboard", "kubo", "ipfs_cluster", "edge_proxy"}
    # ``redis`` is accepted only to operate snapshots rendered before Redis
    # was removed from the Dashboard baseline. New catalog profiles omit it.
    _only_keys(images, "images", required_images | {"redis"})
    if not required_images.issubset(images):
        raise _error("images must declare every managed runtime image")
    for image_id, image in images.items():
        _string(image, f"images.{image_id}")

    infrastructure = _object(raw["infrastructure"], "infrastructure")
    _only_keys(infrastructure, "infrastructure", {"besu", "ipfs", "cluster", "web"})
    for name, fields in {
        "besu": {"network", "p2p_port_start", "rpc_port"},
        "ipfs": {"network", "api_port", "swarm_port"},
        "cluster": {"network", "api_port", "p2p_port"},
        "web": {"http_port"},
    }.items():
        definition = _object(infrastructure.get(name), f"infrastructure.{name}")
        _only_keys(definition, f"infrastructure.{name}", fields)
        for field in fields:
            if field not in definition:
                raise _error(f"infrastructure.{name}.{field} is required")

    deployment = _object(raw["deployment"], "deployment")
    _only_keys(deployment, "deployment", {"id", "label"})
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
        _only_keys(definition, f"networks.{network_id}", {"kind", "cidr", "cidrs", "interface"})
        kind = definition.get("kind")
        if kind not in {"lan", "vpn"}:
            raise _error(f"networks.{network_id}.kind must be lan or vpn")
        has_cidr = "cidr" in definition
        has_cidrs = "cidrs" in definition
        if has_cidr == has_cidrs:
            raise _error(f"networks.{network_id} must declare exactly one of cidr or cidrs")
        if has_cidr:
            cidrs = (_cidr(definition["cidr"], f"networks.{network_id}.cidr"),)
        else:
            values = definition["cidrs"]
            if not isinstance(values, list) or not values:
                raise _error(f"networks.{network_id}.cidrs must be a non-empty list")
            cidrs = tuple(_cidr(value, f"networks.{network_id}.cidrs[{index}]") for index, value in enumerate(values))
            if len(set(cidrs)) != len(cidrs):
                raise _error(f"networks.{network_id}.cidrs contains duplicate CIDRs")
        networks.append(Network(network_id, kind, cidrs[0], cidrs))
    if not networks:
        raise _error("networks cannot be empty")
    network_ids = {network.id for network in networks}
    sites_raw = raw.get("sites", {})
    sites = _object(sites_raw, "sites")
    for site_id, definition in sites.items():
        _identifier(site_id, f"sites.{site_id}")
        definition = _object(definition, f"sites.{site_id}")
        _only_keys(definition, f"sites.{site_id}", {"lan"})
        lan = _identifier(definition.get("lan"), f"sites.{site_id}.lan")
        if lan not in network_ids:
            raise _error(f"sites.{site_id}.lan references unknown network {lan}")
        if next(network for network in networks if network.id == lan).kind != "lan":
            raise _error(f"sites.{site_id}.lan must reference a LAN network")
    routes = raw.get("routes", [])
    if not isinstance(routes, list):
        raise _error("routes must be a list")
    declared_routes: set[tuple[str, str]] = set()
    for index, route in enumerate(routes):
        route = _object(route, f"routes[{index}]")
        _only_keys(route, f"routes[{index}]", {"from", "to", "via"})
        source = _identifier(route.get("from"), f"routes[{index}].from")
        destination = _identifier(route.get("to"), f"routes[{index}].to")
        if source not in network_ids or destination not in network_ids:
            raise _error(f"routes[{index}] references an unknown network")
        if source == destination:
            raise _error(f"routes[{index}] must connect two different networks")
        if (source, destination) in declared_routes:
            raise _error(f"routes[{index}] duplicates {source}->{destination}")
        if "via" in route:
            _identifier(route["via"], f"routes[{index}].via")
        declared_routes.add((source, destination))
    for name in ("besu", "ipfs", "cluster"):
        selected = _identifier(infrastructure[name]["network"], f"infrastructure.{name}.network")
        if selected not in network_ids:
            raise _error(f"infrastructure.{name}.network references unknown network {selected}")
    for name, field in (("besu", "p2p_port_start"), ("besu", "rpc_port"), ("ipfs", "api_port"), ("ipfs", "swarm_port"), ("cluster", "api_port"), ("cluster", "p2p_port"), ("web", "http_port")):
        value = infrastructure[name][field]
        if not isinstance(value, int) or not 1 <= value <= 65535:
            raise _error(f"infrastructure.{name}.{field} must be a TCP/UDP port")
    if infrastructure["besu"]["p2p_port_start"] + 4 > 65535:
        raise _error("infrastructure.besu.p2p_port_start leaves no room for five Besu nodes")
    machines: list[Machine] = []
    addresses_seen: dict[str, set[str]] = {network.id: set() for network in networks}
    for machine_id, definition in _object(raw["machines"], "machines").items():
        _identifier(machine_id, f"machines.{machine_id}")
        definition = _object(definition, f"machines.{machine_id}")
        _only_keys(definition, f"machines.{machine_id}", {"execution", "management_address", "addresses", "paths", "docker", "ssh", "site"})
        execution = definition.get("execution")
        if execution not in {"local", "docker-lab", "ssh", "auto"}:
            raise _error(f"machines.{machine_id}.execution must be local, docker-lab, ssh or auto")
        addresses = _object(definition.get("addresses"), f"machines.{machine_id}.addresses")
        _only_keys(addresses, f"machines.{machine_id}.addresses", {network.id for network in networks})
        if not addresses:
            raise _error(f"machines.{machine_id}.addresses cannot be empty")
        canonical_addresses: dict[str, str] = {}
        for network in networks:
            if network.id not in addresses:
                continue
            address = _ipv4(addresses[network.id], f"machines.{machine_id}.addresses.{network.id}")
            if address in addresses_seen[network.id]:
                raise _error(f"machines.{machine_id} duplicates address {address} on {network.id}")
            if not network.contains(address):
                raise _error(f"machines.{machine_id} address {address} is outside {network.id}")
            addresses_seen[network.id].add(address)
            canonical_addresses[network.id] = address
        site = definition.get("site")
        if site is not None:
            site = _identifier(site, f"machines.{machine_id}.site")
            if site not in sites:
                raise _error(f"machines.{machine_id}.site references unknown site {site}")
            site_lan = sites[site]["lan"]
            if site_lan not in canonical_addresses:
                raise _error(f"machines.{machine_id} must have an address on its site LAN {site_lan}")
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
            addresses=canonical_addresses,
            ssh=_ssh(_object(definition.get("ssh", {}), f"machines.{machine_id}.ssh"), defaults_ssh, f"machines.{machine_id}.ssh"),
            workspace_root=_absolute_path(override_paths["workspace_root"], f"machines.{machine_id}.paths.workspace_root"),
            data_root=_absolute_path(override_paths["data_root"], f"machines.{machine_id}.paths.data_root"),
            secrets_root=_absolute_path(override_paths["secrets_root"], f"machines.{machine_id}.paths.secrets_root"),
            docker_subnet=declared_subnet,
            site=site,
        ))

    services: list[ServiceInstance] = []
    for service_id, definition in _object(raw["services"], "services").items():
        _identifier(service_id, f"services.{service_id}")
        definition = _object(definition, f"services.{service_id}")
        _only_keys(definition, f"services.{service_id}", {"type", "machine", "connections", "configuration", "exposure", "listeners"})
        service_type = _string(definition.get("type"), f"services.{service_id}.type")
        if service_type not in SERVICE_TYPES:
            raise _error(f"services.{service_id}.type is unknown: {service_type}")
        machine_id = _string(definition.get("machine"), f"services.{service_id}.machine")
        if machine_id not in {machine.id for machine in machines}:
            raise _error(f"services.{service_id}.machine references unknown machine {machine_id}")
        raw_connections = _object(definition.get("connections", {}), f"services.{service_id}.connections")
        connections: dict[str, dict[str, str]] = {}
        for name, value in raw_connections.items():
            if not isinstance(name, str):
                raise _error(f"services.{service_id}.connections keys must be strings")
            if isinstance(value, str):
                # V3 intentionally does not retain the ambiguous shorthand.
                raise _error(f"services.{service_id}.connections.{name} must declare service and network")
            item = _object(value, f"services.{service_id}.connections.{name}")
            _only_keys(item, f"services.{service_id}.connections.{name}", {"service", "network", "protocol"})
            provider = _identifier(item.get("service"), f"services.{service_id}.connections.{name}.service")
            network = item.get("network")
            if network is not None:
                network = _identifier(network, f"services.{service_id}.connections.{name}.network")
                if network not in {entry.id for entry in networks}:
                    raise _error(f"services.{service_id}.connections.{name}.network references unknown network {network}")
            protocol = item.get("protocol")
            if protocol is not None and protocol not in {"tcp", "udp"}:
                raise _error(f"services.{service_id}.connections.{name}.protocol must be tcp or udp")
            connections[name] = {
                "service": provider,
                **({"network": network} if network else {}),
                **({"protocol": protocol} if protocol else {}),
            }
        configuration = _object(definition.get("configuration", {}), f"services.{service_id}.configuration")
        exposure = definition.get("exposure")
        listeners: tuple[dict, ...] = ()
        if exposure is not None and "listeners" in definition:
            raise _error(f"services.{service_id} cannot combine exposure and listeners")
        if exposure is not None:
            exposure = _object(exposure, f"services.{service_id}.exposure")
            _only_keys(exposure, f"services.{service_id}.exposure", {"mode", "network", "port", "protocols", "advertise_address", "advertise_port"})
            if exposure.get("mode") not in {"none", "loopback", "private", "public"}:
                raise _error(f"services.{service_id}.exposure.mode must be none, loopback, private or public")
            if exposure.get("mode") != "none" and (not isinstance(exposure.get("port"), int) or not 1 <= exposure["port"] <= 65535):
                raise _error(f"services.{service_id}.exposure.port must be a TCP port")
            protocols = exposure.get("protocols", ["tcp"])
            if not isinstance(protocols, list) or not protocols or any(item not in {"tcp", "udp"} for item in protocols):
                raise _error(f"services.{service_id}.exposure.protocols must contain tcp and/or udp")
            exposure["protocols"] = list(dict.fromkeys(protocols))
            if exposure.get("advertise_address") is not None:
                exposure["advertise_address"] = _ipv4(exposure["advertise_address"], f"services.{service_id}.exposure.advertise_address")
            if exposure.get("advertise_port") is not None:
                if not isinstance(exposure["advertise_port"], int) or not 1 <= exposure["advertise_port"] <= 65535:
                    raise _error(f"services.{service_id}.exposure.advertise_port must be a TCP/UDP port")
            if exposure.get("mode") != "private" and (exposure.get("advertise_address") is not None or exposure.get("advertise_port") is not None):
                raise _error(f"services.{service_id}.exposure advertise fields are only valid for private exposure")
            if exposure.get("mode") == "private":
                network = _identifier(exposure.get("network"), f"services.{service_id}.exposure.network")
                if network not in {entry.id for entry in networks}:
                    raise _error(f"services.{service_id}.exposure.network references unknown network {network}")
                if network not in next(machine.addresses for machine in machines if machine.id == machine_id):
                    raise _error(f"services.{service_id}.exposure.network is not available on {machine_id}")
            elif exposure.get("network") is not None:
                raise _error(f"services.{service_id}.exposure.network is only valid for private exposure")
        if "listeners" in definition:
            raw_listeners = definition["listeners"]
            if not isinstance(raw_listeners, list) or not raw_listeners:
                raise _error(f"services.{service_id}.listeners must be a non-empty list")
            parsed_listeners: list[dict] = []
            seen_listeners: set[tuple[str, int, tuple[str, ...]]] = set()
            machine_addresses = next(machine.addresses for machine in machines if machine.id == machine_id)
            for index, listener in enumerate(raw_listeners):
                listener = _object(listener, f"services.{service_id}.listeners[{index}]")
                _only_keys(listener, f"services.{service_id}.listeners[{index}]", {"network", "port", "protocols", "advertise_address", "advertise_port"})
                network = _identifier(listener.get("network"), f"services.{service_id}.listeners[{index}].network")
                if network not in network_ids or network not in machine_addresses:
                    raise _error(f"services.{service_id}.listeners[{index}].network is not available on {machine_id}")
                port = listener.get("port")
                if not isinstance(port, int) or not 1 <= port <= 65535:
                    raise _error(f"services.{service_id}.listeners[{index}].port must be a TCP port")
                protocols = listener.get("protocols", ["tcp"])
                if not isinstance(protocols, list) or not protocols or any(item not in {"tcp", "udp"} for item in protocols):
                    raise _error(f"services.{service_id}.listeners[{index}].protocols must contain tcp and/or udp")
                normalized = {"network": network, "port": port, "protocols": list(dict.fromkeys(protocols))}
                if "advertise_address" in listener:
                    normalized["advertise_address"] = _ipv4(listener["advertise_address"], f"services.{service_id}.listeners[{index}].advertise_address")
                if "advertise_port" in listener:
                    advertised_port = listener["advertise_port"]
                    if not isinstance(advertised_port, int) or not 1 <= advertised_port <= 65535:
                        raise _error(f"services.{service_id}.listeners[{index}].advertise_port must be a TCP/UDP port")
                    normalized["advertise_port"] = advertised_port
                key = (network, port, tuple(normalized["protocols"]))
                if key in seen_listeners:
                    raise _error(f"services.{service_id}.listeners contains duplicate listener {network}:{port}")
                seen_listeners.add(key)
                parsed_listeners.append(normalized)
            listeners = tuple(parsed_listeners)
        services.append(ServiceInstance(service_id, service_type, machine_id, dict(connections), dict(configuration), exposure, listeners))
    _validate_domain(raw, services, machines, declared_routes)
    return raw, tuple(machines), tuple(services), tuple(networks)


def groups_for_inventory(raw: dict[str, Any], machines: tuple[Machine, ...], services: tuple[ServiceInstance, ...]) -> tuple[Group, ...]:
    """Return explicit logical servers, with a compatibility fallback.

    Older v3 inventories only had ``services.*.machine``.  They remain valid
    and are represented as one synthetic server group per machine.  New
    inventories can use ``groups`` to preserve the v2 five-server topology
    while keeping service placement and all network/port values centralized.
    """
    machine_ids = {machine.id for machine in machines}
    by_service = {service.id: service for service in services}
    if "groups" not in raw:
        return tuple(
            Group(machine.id, "server", machine.id, tuple(service.id for service in services if service.machine_id == machine.id), tuple(service.id for service in services if service.machine_id == machine.id))
            for machine in machines
        )
    definitions = raw["groups"]
    if not isinstance(definitions, dict) or not definitions:
        raise _error("groups must be a non-empty object")
    groups: list[Group] = []
    assigned: dict[str, str] = {}
    for group_id, definition in definitions.items():
        _identifier(group_id, f"groups.{group_id}")
        definition = _object(definition, f"groups.{group_id}")
        _only_keys(definition, f"groups.{group_id}", {"kind", "machine", "members", "services"})
        kind = definition.get("kind")
        if kind not in {"apps", "validators", "observers", "storage"}:
            raise _error(f"groups.{group_id}.kind must be apps, validators, observers or storage")
        machine_id = _string(definition.get("machine"), f"groups.{group_id}.machine")
        if machine_id not in machine_ids:
            raise _error(f"groups.{group_id}.machine references unknown machine {machine_id}")
        members = definition.get("members", [])
        if not isinstance(members, list) or not members or any(not isinstance(item, str) or not item for item in members):
            raise _error(f"groups.{group_id}.members must be a non-empty list of strings")
        service_ids = definition.get("services")
        if service_ids is not None:
            if not isinstance(service_ids, list) or any(item not in by_service for item in service_ids):
                raise _error(f"groups.{group_id}.services must contain known service IDs")
        else:
            service_ids = []
            for member in members:
                if member in by_service:
                    service_ids.append(member)
                    continue
                matches = [service.id for service in services if service.configuration.get("node_id") == member or service.configuration.get("peer_name") == member]
                if not matches:
                    raise _error(f"groups.{group_id}.members references unknown node or service {member}")
                service_ids.extend(matches)
        if not service_ids:
            raise _error(f"groups.{group_id} must resolve at least one service")
        expected_type = {"validators": "besu-validator", "observers": "besu-observer"}.get(kind)
        if expected_type and any(by_service[service_id].type != expected_type for service_id in service_ids):
            raise _error(f"groups.{group_id}.{kind} may contain only {expected_type} services")
        if kind == "storage" and any(by_service[service_id].type not in {"ipfs-kubo", "ipfs-cluster"} for service_id in service_ids):
            raise _error(f"groups.{group_id}.storage may contain only IPFS storage services")
        for service_id in service_ids:
            if by_service[service_id].machine_id != machine_id:
                raise _error(f"groups.{group_id}.services.{service_id} is assigned to another machine")
            if service_id in assigned:
                raise _error(f"service {service_id} belongs to both groups.{assigned[service_id]} and groups.{group_id}")
            assigned[service_id] = group_id
        groups.append(Group(group_id, kind, machine_id, tuple(dict.fromkeys(members)), tuple(dict.fromkeys(service_ids))))
    # When a machine has exactly one group, the remaining services on that
    # machine belong to it implicitly. This keeps production inventories
    # readable while still requiring explicit service lists when several
    # simulated groups share one local Docker daemon.
    groups_by_machine: dict[str, list[int]] = {}
    for index, group in enumerate(groups):
        groups_by_machine.setdefault(group.machine_id, []).append(index)
    mutable_services = [list(group.service_ids) for group in groups]
    for service_id, service in by_service.items():
        if service_id in assigned:
            continue
        candidates = groups_by_machine.get(service.machine_id, [])
        if len(candidates) != 1:
            raise _error("groups do not assign service unambiguously: " + service_id)
        index = candidates[0]
        mutable_services[index].append(service_id)
        assigned[service_id] = groups[index].id
    groups = [Group(group.id, group.kind, group.machine_id, group.members, tuple(dict.fromkeys(mutable_services[index]))) for index, group in enumerate(groups)]
    return tuple(groups)


def _positive_integer(value: Any, path: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise _error(f"{path} must be a positive integer")
    return value


def _validate_settings(settings: dict[str, Any]) -> None:
    """Keep emitted Minter and Store tuning explicit in the topology."""
    _only_keys(settings, "settings", {"minter", "store"})
    minter = _object(settings.get("minter"), "settings.minter")
    _only_keys(minter, "settings.minter", {"shoulder", "logging", "metadata", "replication", "chain"})
    shoulder = minter.get("shoulder")
    if not isinstance(shoulder, str) or not re.fullmatch(r"2[0-9a-z]{2}", shoulder):
        raise _error("settings.minter.shoulder must use the 2xx format with lowercase alphanumeric characters")

    # Optional so previously generated v3 inventories remain valid. Catalog-backed
    # operator inventories inherit INFO and can override it explicitly.
    logging = minter.get("logging")
    if logging is not None:
        logging_settings = _object(logging, "settings.minter.logging")
        _only_keys(logging_settings, "settings.minter.logging", {"level"})
        level = logging_settings.get("level")
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if not isinstance(level, str) or level.upper() not in allowed_levels:
            raise _error("settings.minter.logging.level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

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
    _only_keys(store, "settings.store", store_required | {"logging"})
    if not store_required.issubset(store):
        raise _error("settings.store is incomplete")
    for field in store_required:
        _positive_integer(store[field], f"settings.store.{field}")

    # Optional so existing fully expanded v3 inventories remain valid. Catalog
    # backed operator inventories inherit the production-safe WARNING default.
    logging = store.get("logging")
    if logging is not None:
        logging_settings = _object(logging, "settings.store.logging")
        _only_keys(logging_settings, "settings.store.logging", {"level"})
        level = logging_settings.get("level")
        allowed_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if not isinstance(level, str) or level.upper() not in allowed_levels:
            raise _error("settings.store.logging.level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")


def _provider_id(connection: dict[str, str]) -> str:
    return connection["service"]


def _proxy_routes(service: ServiceInstance) -> list[dict[str, str]]:
    """Validate and return the public routing contract of an edge proxy.

    A route is deliberately a connection name plus two path prefixes.  This
    keeps addresses out of public configuration: same-host routes use Docker
    DNS and remote routes use the normal private connection derivation.
    """
    configuration = service.configuration
    _only_keys(configuration, f"services.{service.id}.configuration", {"sites", "tls"})
    sites = configuration.get("sites")
    if not isinstance(sites, list) or not sites:
        raise _error(f"services.{service.id}.configuration.sites must be a non-empty list")
    tls = configuration.get("tls", {"mode": "http"})
    tls = _object(tls, f"services.{service.id}.configuration.tls")
    _only_keys(tls, f"services.{service.id}.configuration.tls", {"mode", "certificate_secret", "key_secret"})
    mode = tls.get("mode", "http")
    if mode not in {"http", "direct", "external"}:
        raise _error(f"services.{service.id}.configuration.tls.mode must be http, direct or external")
    if mode == "direct":
        if not isinstance(tls.get("certificate_secret"), str) or not isinstance(tls.get("key_secret"), str):
            raise _error(f"services.{service.id}.configuration.tls direct mode requires certificate_secret and key_secret")
    elif "certificate_secret" in tls or "key_secret" in tls:
        raise _error(f"services.{service.id}.configuration.tls certificate secrets are only valid for direct TLS")

    routes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    route_ids: set[str] = set()
    for site_index, site in enumerate(sites):
        site = _object(site, f"services.{service.id}.configuration.sites[{site_index}]")
        _only_keys(site, f"services.{service.id}.configuration.sites[{site_index}]", {"host", "public_origin", "routes"})
        host = site.get("host", "")
        if not isinstance(host, str) or any(char.isspace() for char in host):
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].host must be a hostname or empty")
        origin = site.get("public_origin")
        if not isinstance(origin, str):
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].public_origin is required")
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].public_origin must be an http(s) origin")
        if host and parsed.hostname != host:
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].public_origin host must match host")
        if mode == "direct" and parsed.scheme != "https":
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].public_origin must use https for direct TLS")
        definitions = site.get("routes")
        if not isinstance(definitions, list) or not definitions:
            raise _error(f"services.{service.id}.configuration.sites[{site_index}].routes must be a non-empty list")
        for route_index, route in enumerate(definitions):
            path = f"services.{service.id}.configuration.sites[{site_index}].routes[{route_index}]"
            route = _object(route, path)
            _only_keys(route, path, {"id", "path", "connection", "upstream_path"})
            route_id = _identifier(route.get("id"), path + ".id")
            if route_id in route_ids:
                raise _error(f"{path}.id duplicates another proxy route")
            route_ids.add(route_id)
            public_path = route.get("path")
            upstream_path = route.get("upstream_path")
            connection = route.get("connection")
            for value, label in ((public_path, "path"), (upstream_path, "upstream_path")):
                if not isinstance(value, str) or not value.startswith("/") or "//" in value or "/../" in value or value.endswith("/.."):
                    raise _error(f"{path}.{label} must be an absolute, normalized path prefix")
            if not isinstance(connection, str) or connection not in service.connections:
                raise _error(f"{path}.connection must name a declared proxy connection")
            key = (host.lower(), public_path)
            if key in seen:
                raise _error(f"{path}.path duplicates another route for host {host or '<default>'}")
            seen.add(key)
            routes.append({"host": host, "id": route["id"], "path": public_path, "connection": connection, "upstream_path": upstream_path})
    return routes


def _validate_domain(
    raw: dict[str, Any],
    services: list[ServiceInstance],
    machines: list[Machine] | None = None,
    declared_routes: set[tuple[str, str]] | None = None,
) -> None:
    declared_routes = declared_routes or set()
    by_id = {service.id: service for service in services}
    by_type: dict[str, list[ServiceInstance]] = {}
    for service in services:
        by_type.setdefault(service.type, []).append(service)
    for service in services:
        for name, connection in service.connections.items():
            provider_id = _provider_id(connection)
            if provider_id not in by_id:
                raise _error(f"services.{service.id}.connections.{name} references unknown service {provider_id}")
    for service in services:
        contract = CONNECTION_CONTRACTS.get(service.type, {})
        if service.type in {"dashboard", "dashboard-migrate"} and "redis" in service.connections:
            contract = {**contract, "redis": "dashboard-redis"}
        if service.type == "store-api":
            _only_keys(service.configuration, f"services.{service.id}.configuration", {"mode"})
            mode = service.configuration.get("mode", "read_write")
            if mode not in {"read_write", "read_only"}:
                raise _error(f"services.{service.id}.configuration.mode must be read_write or read_only")
            if service.id == "store-api" and mode != "read_write":
                raise _error("services.store-api.configuration.mode must be read_write")
            # The main Store API is the writer and must see every Cluster API.
            # A read-only Store API uses only same-site Cluster/Kubo endpoints;
            # cross-site retrieval happens through the IPFS/Cluster P2P mesh.
            providers = [by_id.get(_provider_id(connection)) for connection in service.connections.values()]
            clusters = by_type.get("ipfs-cluster", [])
            if not providers or any(provider is None or provider.type != "ipfs-cluster" for provider in providers):
                raise _error(f"services.{service.id}.connections must contain only ipfs-cluster services")
            if mode != "read_only" and {provider.id for provider in providers} != {cluster.id for cluster in clusters}:
                raise _error(f"services.{service.id}.connections must name every ipfs-cluster service exactly once")
        elif service.type == "edge-proxy":
            routes = _proxy_routes(service)
            used = {route["connection"] for route in routes}
            if set(service.connections) != used:
                raise _error(f"services.{service.id}.connections must contain exactly the connections used by its routes")
            for name in used:
                provider = by_id.get(_provider_id(service.connections[name]))
                if provider and provider.type not in {"dashboard", "explorer", "minter-api", "resolver-api"}:
                    raise _error(f"services.{service.id}.connections.{name} must reference an HTTP application service")
        else:
            if set(service.connections) != set(contract):
                expected = ", ".join(sorted(contract)) or "none"
                raise _error(f"services.{service.id}.connections must contain exactly: {expected}")
            for name, provider_type in contract.items():
                provider = by_id.get(_provider_id(service.connections[name]))
                allowed_types = {provider_type}
                if provider_type == "besu-rpc":
                    # Observers keep RPC private but may intentionally serve a
                    # colocated or routed Resolver/API consumer.
                    allowed_types.add("besu-observer")
                if provider and provider.type not in allowed_types:
                    raise _error(f"services.{service.id}.connections.{name} must reference {provider_type}")
    required = {"besu-rpc", "minter-api", "minter-postgres", "store-api", "admin-api", "resolver-api", "dashboard", "ipfs-kubo", "ipfs-cluster"}
    missing_types = sorted(item for item in required if not by_type.get(item))
    if missing_types:
        raise _error("services missing required types: " + ", ".join(missing_types))
    proxies_by_machine: dict[str, list[str]] = {}
    for service in by_type.get("edge-proxy", []):
        proxies_by_machine.setdefault(service.machine_id, []).append(service.id)
    for machine_id, proxy_ids in proxies_by_machine.items():
        if len(proxy_ids) > 1:
            raise _error(f"machine {machine_id} may run only one edge-proxy: " + ", ".join(sorted(proxy_ids)))
    infrastructure = raw["infrastructure"]
    policy_ports = {
        "besu-rpc": infrastructure["besu"]["rpc_port"],
        "ipfs-kubo": infrastructure["ipfs"]["api_port"],
        "ipfs-cluster": infrastructure["cluster"]["api_port"],
        "edge-proxy": infrastructure["web"]["http_port"],
    }
    for service_type, expected_port in policy_ports.items():
        for service in by_type.get(service_type, []):
            if service.exposure and service.exposure.get("mode") == "private" and int(service.exposure["port"]) != expected_port:
                raise _error(f"services.{service.id}.exposure.port must match infrastructure policy ({expected_port})")
    workers = by_type.get("minter-worker", [])
    worker_kinds = {item.configuration.get("worker") for item in workers}
    if worker_kinds != {"metadata", "replication", "chain"}:
        raise _error("services must contain exactly metadata, replication and chain minter workers")
    minter_machine = by_type["minter-api"][0].machine_id
    colocated = [*by_type["minter-api"], *by_type["minter-postgres"], *workers]
    if any(service.machine_id != minter_machine for service in colocated):
        raise _error("minter-api, minter-postgres and all minter workers must share one machine because metadata storage is local")
    for worker in workers:
        if _provider_id(worker.connections["database"]) not in {service.id for service in by_type["minter-postgres"]}:
            raise _error(f"services.{worker.id} must connect to minter-postgres")
    minter = by_type["minter-api"][0]
    if _provider_id(minter.connections["database"]) not in {service.id for service in by_type["minter-postgres"]}:
        raise _error(f"services.{minter.id} must connect to minter-postgres")
    ports: dict[tuple[str, str, int], str] = {}
    for service in services:
        exposed = [] if not service.exposure or service.exposure.get("mode") == "none" else [(str(service.exposure["mode"]), str(service.exposure.get("network", "")), int(service.exposure["port"]))]
        exposed.extend(("private", str(listener["network"]), int(listener["port"])) for listener in service.listeners)
        for mode, network, port in exposed:
            key = (service.machine_id, mode, network, port)
            if key in ports:
                raise _error(f"services.{service.id} listener duplicates {ports[key]} on {key[0]}:{key[-1]}")
            ports[key] = service.id
    if machines is not None:
        machine_by_id = {machine.id: machine for machine in machines}
        for consumer in services:
            for name, connection in consumer.connections.items():
                provider = by_id[_provider_id(connection)]
                if provider.type == "contracts-deploy":
                    continue
                if consumer.machine_id == provider.machine_id:
                    if "network" in connection or "protocol" in connection:
                        raise _error(f"services.{consumer.id}.connections.{name}.network/protocol are only valid between hosts")
                    continue
                network = connection.get("network")
                if not network:
                    raise _error(f"services.{consumer.id}.connections.{name}.network is required between hosts")
                protocol = connection.get("protocol")
                if protocol not in {"tcp", "udp"}:
                    raise _error(f"services.{consumer.id}.connections.{name}.protocol must be tcp or udp between hosts")
                if network not in machine_by_id[provider.machine_id].addresses:
                    raise _error(f"services.{consumer.id}.connections.{name}.network is not available on {provider.machine_id}")
                if network not in machine_by_id[consumer.machine_id].addresses:
                    sources = set(machine_by_id[consumer.machine_id].addresses)
                    if not any((source, network) in declared_routes for source in sources):
                        raise _error(f"services.{consumer.id}.connections.{name}.network is neither shared nor routed from {consumer.machine_id}")
                listener = next((item for item in provider.listeners if item["network"] == network), None)
                if not listener and (not provider.exposure or provider.exposure.get("mode") != "private"):
                    raise _error(f"services.{provider.id} must expose a private endpoint for {consumer.id}")
                if not listener and provider.exposure.get("network") != network:
                    raise _error(f"services.{consumer.id}.connections.{name}.network does not match {provider.id} exposure")
                protocols = listener["protocols"] if listener else provider.exposure.get("protocols", ["tcp"])
                if protocol not in protocols:
                    raise _error(f"services.{consumer.id}.connections.{name}.protocol is not exposed by {provider.id}")
        peerings = _object(raw.get("peerings", {}), "peerings")
        _only_keys(peerings, "peerings", {"blockchain", "ipfs", "cluster"})
        family_types = {
            "blockchain": {"besu-rpc", "besu-validator", "besu-observer"},
            "ipfs": {"ipfs-kubo"},
            "cluster": {"ipfs-cluster"},
        }
        for family, edges in peerings.items():
            if not isinstance(edges, list):
                raise _error(f"peerings.{family} must be a list")
            seen_edges: set[tuple[str, str]] = set()
            for index, edge in enumerate(edges):
                edge = _object(edge, f"peerings.{family}[{index}]")
                _only_keys(edge, f"peerings.{family}[{index}]", {"from", "to", "network", "address", "port"})
                source = _identifier(edge.get("from"), f"peerings.{family}[{index}].from")
                target = _identifier(edge.get("to"), f"peerings.{family}[{index}].to")
                if source == target or (source, target) in seen_edges:
                    raise _error(f"peerings.{family}[{index}] duplicates or loops {source}->{target}")
                seen_edges.add((source, target))
                if source not in by_id or target not in by_id or by_id[source].type not in family_types[family] or by_id[target].type not in family_types[family]:
                    raise _error(f"peerings.{family}[{index}] must reference {family} services")
                network = _identifier(edge.get("network"), f"peerings.{family}[{index}].network")
                if network not in machine_by_id[by_id[target].machine_id].addresses:
                    raise _error(f"peerings.{family}[{index}].network is not available on target {target}")
                if network not in machine_by_id[by_id[source].machine_id].addresses:
                    sources = set(machine_by_id[by_id[source].machine_id].addresses)
                    if not any((candidate, network) in declared_routes for candidate in sources):
                        raise _error(f"peerings.{family}[{index}] is neither shared nor routed from {source}")
                address = _ipv4(edge.get("address"), f"peerings.{family}[{index}].address")
                if address != machine_by_id[by_id[target].machine_id].address_on(network):
                    raise _error(f"peerings.{family}[{index}].address must match target address on {network}")
                port = edge.get("port")
                if not isinstance(port, int) or not 1 <= port <= 65535:
                    raise _error(f"peerings.{family}[{index}].port must be a TCP/UDP port")
        p2p_policies = (
            ("besu", {"besu-rpc", "besu-validator", "besu-observer"}),
            ("ipfs", {"ipfs-kubo"}),
            ("cluster", {"ipfs-cluster"}),
        )
        for policy, types in p2p_policies:
            peers = [service for service in services if service.type in types]
            if len({peer.machine_id for peer in peers}) < 2:
                continue
            family = {"besu": "blockchain", "ipfs": "ipfs", "cluster": "cluster"}[policy]
            if raw.get("peerings", {}).get(family):
                continue
            network = raw["infrastructure"][policy]["network"]
            for peer in peers:
                if network not in machine_by_id[peer.machine_id].addresses:
                    raise _error(f"services.{peer.id} requires {network} for cross-host {policy} P2P")
    blockchain = _object(raw["blockchain"], "blockchain")
    _only_keys(blockchain, "blockchain", {"chain_id", "nodes", "qbft", "artifact", "primary_rpc", "observer_max_block_lag"})
    observer_max_block_lag = blockchain.get("observer_max_block_lag", 1)
    if not isinstance(observer_max_block_lag, int) or isinstance(observer_max_block_lag, bool) or observer_max_block_lag < 0:
        raise _error("blockchain.observer_max_block_lag must be a non-negative integer")
    artifact = _object(blockchain.get("artifact", {}), "blockchain.artifact")
    if artifact:
        _only_keys(artifact, "blockchain.artifact", {"path", "source"})
        artifact_path = _string(artifact.get("path"), "blockchain.artifact.path")
        if Path(artifact_path).is_absolute() or ".." in Path(artifact_path).parts:
            raise _error("blockchain.artifact.path must be relative to secrets_root")
        if "source" in artifact:
            _string(artifact["source"], "blockchain.artifact.source")
    qbft = _object(blockchain.get("qbft"), "blockchain.qbft")
    _only_keys(qbft, "blockchain.qbft", {"block_period_seconds", "epoch_length", "request_timeout_seconds"})
    for field in ("block_period_seconds", "epoch_length", "request_timeout_seconds"):
        value = qbft.get(field)
        if not isinstance(value, int) or value < 1:
            raise _error(f"blockchain.qbft.{field} must be a positive integer")
    nodes = _object(blockchain.get("nodes"), "blockchain.nodes")
    validators = []
    rpc = []
    observers = []
    for node_id, item in nodes.items():
        definition = _object(item, f"blockchain.nodes.{node_id}")
        _only_keys(definition, f"blockchain.nodes.{node_id}", {"role", "p2p_port"})
        role = definition.get("role")
        if role == "validator":
            validators.append(node_id)
        elif role == "rpc":
            rpc.append(node_id)
        elif role == "observer":
            observers.append(node_id)
        else:
            raise _error(f"blockchain.nodes.{node_id}.role must be validator, rpc or observer")
    if not validators or not rpc:
        raise _error("blockchain.nodes requires at least one validator and one RPC")
    declared_nodes = {service.configuration.get("node_id") for service in by_type.get("besu-validator", []) + by_type.get("besu-rpc", []) + by_type.get("besu-observer", [])}
    if set(validators + rpc + observers) != declared_nodes:
        raise _error("blockchain nodes must match besu service instances")
    primary_rpc = blockchain.get("primary_rpc")
    if primary_rpc not in rpc:
        raise _error("blockchain.primary_rpc must name a besu-rpc node")
    storage = _object(raw["storage"], "storage")
    _only_keys(storage, "storage", {"cluster_name", "nodes", "replication"})
    node_ids = storage.get("nodes")
    kubo_peers = {service.configuration.get("peer_name") for service in by_type.get("ipfs-kubo", [])}
    if not isinstance(node_ids, list) or set(node_ids) != kubo_peers:
        raise _error("storage.nodes must exactly match ipfs-kubo peer names")
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
        _only_keys(definition, f"secrets.{secret_id}", {"path", "consumers", "format", "source", "mode"})
        relative = _string(definition.get("path"), f"secrets.{secret_id}.path")
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise _error(f"secrets.{secret_id}.path must be a safe path relative to secrets_root")
        if "source" in definition:
            source_path = _string(definition["source"], f"secrets.{secret_id}.source")
            if Path(source_path).is_absolute() is False and ".." in Path(source_path).parts:
                raise _error(f"secrets.{secret_id}.source must not escape the project")
        if "mode" in definition and definition["mode"] != "0600":
            raise _error(f"secrets.{secret_id}.mode must be 0600")
        consumers = definition.get("consumers", [])
        if not isinstance(consumers, list) or not all(isinstance(item, str) for item in consumers):
            raise _error(f"secrets.{secret_id}.consumers must be a string list")
        unknown_consumers = sorted(set(consumers).difference(by_id))
        if unknown_consumers:
            raise _error(f"secrets.{secret_id}.consumers references unknown service(s): " + ", ".join(unknown_consumers))
    for proxy in by_type.get("edge-proxy", []):
        tls = proxy.configuration.get("tls", {})
        if tls.get("mode") == "direct":
            for secret_id in (tls["certificate_secret"], tls["key_secret"]):
                if secret_id not in secrets:
                    raise _error(f"services.{proxy.id}.configuration.tls references unknown secret {secret_id}")
                if proxy.id not in secrets[secret_id].get("consumers", []):
                    raise _error(f"secrets.{secret_id}.consumers must include TLS proxy {proxy.id}")
