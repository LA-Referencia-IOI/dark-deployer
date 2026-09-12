"""Pure expansion of a compact operator inventory into the v3 contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .catalogs import get_catalog
from .inventory import InventoryError, validate_inventory


class OperatorInventoryError(InventoryError):
    """The operator-facing document cannot be expanded safely."""


@dataclass(frozen=True)
class ResolutionResult:
    document: dict[str, Any]
    provenance: dict[str, str]
    warnings: tuple[str, ...]
    metadata: dict[str, str]


def _fail(message: str) -> OperatorInventoryError:
    return OperatorInventoryError("operator inventory: " + message)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"{path} must be an object")
    return value


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in value):
        raise _fail(f"{path} must be a lowercase identifier")
    return value


def _only_keys(value: dict, path: str, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(f"{path} contains unsupported fields: {', '.join(unknown)}")


def _merge(base: dict, override: dict, path: str = "overrides") -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if key not in result:
            raise _fail(f"{path}.{key} is not an overridable field")
        if isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = _merge(result[key], value, f"{path}.{key}")
        else:
            result[key] = deepcopy(value)
    return result


_PORTS = {
    "besu-rpc": 8545, "store-api": 8003, "minter-api": 8001,
    "admin-api": 8000, "resolver-api": 8002, "dashboard": 8081,
    "explorer": 25000, "ipfs-kubo": 5001, "ipfs-cluster": 9094,
}


def _network_for(consumer: dict, provider: dict, routing: dict) -> str:
    if provider["type"] == "ipfs-cluster":
        kind = "storage_api"
    elif provider["type"] in {"ipfs-kubo", "ipfs-cluster"}:
        kind = "storage_p2p"
    else:
        kind = "application_api"
    network = routing.get(kind)
    if not isinstance(network, str) or not network:
        raise _fail(f"routing.{kind} is required for remote connection to {provider['type']}")
    return network


def _route_exists(routes: list[dict], consumer: dict, provider_network: str) -> bool:
    source_networks = set(consumer.get("addresses", {}))
    return any(isinstance(route, dict) and route.get("from") in source_networks and route.get("to") == provider_network for route in routes)


def _service_group(service_id: str, services: dict, groups: dict) -> str:
    for group_id, definition in groups.items():
        if service_id in definition["services"]:
            return group_id
    raise _fail(f"internal recipe did not place {service_id}")


def resolve_inventory(raw: dict[str, Any], *, source_path: Path) -> ResolutionResult:
    """Expand a compact document without executing, acquiring, or reading secrets."""
    schema_path = Path(__file__).with_name("operator_schema.json")
    errors = sorted(Draft202012Validator(json.loads(schema_path.read_text())).iter_errors(raw), key=str)
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise _fail(f"{location}: {error.message}")
    catalog = get_catalog(raw.get("catalog"))
    deployment = _object(raw.get("deployment"), "deployment")
    deployment_id = _identifier(deployment.get("id"), "deployment.id")
    machines_input = _object(raw.get("machines"), "machines")
    placement = _object(raw.get("placement"), "placement")
    blockchain = _object(raw.get("blockchain"), "blockchain")
    storage = _object(raw.get("storage"), "storage")
    peers = _object(storage.get("peers"), "storage.peers")
    if not peers:
        raise _fail("storage.peers must contain at least one peer")
    if set(blockchain) - {"chain_id", "rpc", "validators", "artifact", "besu_image", "qbft"}:
        raise _fail("blockchain contains unsupported fields")
    rpc = _object(blockchain.get("rpc"), "blockchain.rpc")
    validators = _object(blockchain.get("validators"), "blockchain.validators")
    if set(validators) != {"validator01", "validator02", "validator03", "validator04"}:
        raise _fail("blockchain.validators must contain validator01 through validator04")
    if rpc.get("id") != "rpc01":
        raise _fail("blockchain.rpc.id must be rpc01 in dark-standard-1")

    result = deepcopy(catalog.document)
    # A catalogue records destinations and consumers, never site-local private
    # sources.  Production's REPLACE values must not leak into a local site.
    result["blockchain"]["artifact"].pop("source", None)
    for definition in result["secrets"].values():
        definition.pop("source", None)
    result["deployment"] = {"id": deployment_id, **({"label": deployment["label"]} if isinstance(deployment.get("label"), str) else {})}
    if "defaults" in raw:
        result["defaults"] = _merge(result["defaults"], _object(raw["defaults"], "defaults"), "defaults")
    networks = raw.get("networks")
    if networks is not None:
        result["networks"] = deepcopy(_object(networks, "networks"))
    if "routes" in raw:
        result["routes"] = deepcopy(raw["routes"])

    # Compact local machines intentionally need no fake SSH configuration.
    result["machines"] = {}
    for machine_id, definition in machines_input.items():
        _identifier(machine_id, f"machines.{machine_id}")
        definition = _object(definition, f"machines.{machine_id}")
        execution = definition.get("execution")
        if execution not in {"local", "ssh", "auto"}:
            raise _fail(f"machines.{machine_id}.execution must be local, ssh or auto")
        item = {"execution": execution}
        if execution != "local":
            for field in ("management_address", "addresses"):
                if field not in definition:
                    raise _fail(f"machines.{machine_id}.{field} is required for {execution}")
                item[field] = deepcopy(definition[field])
        else:
            item["management_address"] = definition.get("management_address", "127.0.0.1")
            if "addresses" in definition:
                item["addresses"] = deepcopy(definition["addresses"])
            else:
                network_id, network = next(iter(result["networks"].items()))
                cidr = (network.get("cidrs") or [network.get("cidr")])[0]
                item["addresses"] = {network_id: str(next(ipaddress.ip_network(cidr).hosts()))}
        for field in ("paths", "ssh", "docker"):
            if field in definition:
                item[field] = deepcopy(definition[field])
        result["machines"][machine_id] = item

    def group_machine(group_id: str) -> str:
        value = placement.get(group_id)
        if not isinstance(value, str) or value not in result["machines"]:
            raise _fail(f"placement.{group_id} must name a declared machine")
        return value

    # Start with the recipe and remove the second storage pair for a one-copy site.
    if set(peers) - {"storage-a", "storage-b"}:
        raise _fail("dark-standard-1 supports storage-a and storage-b")
    if "storage-a" not in peers:
        raise _fail("storage-a is required by dark-standard-1")
    if "storage-b" not in peers:
        for service_id in ("ipfs-storage-b", "cluster-storage-b"):
            result["services"].pop(service_id, None)
        result["services"]["store-api"]["connections"].pop("cluster_cluster-storage-b", None)
        for definition in result["secrets"].values():
            definition["consumers"] = [item for item in definition.get("consumers", []) if item not in {"ipfs-storage-b", "cluster-storage-b"}]
    result["storage"]["cluster_name"] = storage.get("cluster_name", result["storage"]["cluster_name"])
    result["storage"]["nodes"] = list(peers)
    result["storage"]["replication"] = deepcopy(_object(storage.get("replication"), "storage.replication"))

    result["groups"] = {}
    app_machine = group_machine("apps")
    group_services = {"apps": [sid for sid in result["services"] if sid not in {"validator01", "validator02", "validator03", "validator04", "ipfs-storage-a", "cluster-storage-a", "ipfs-storage-b", "cluster-storage-b"}]}
    group_members = {"apps": ["rpc01"]}
    for validator_id, definition in validators.items():
        group_id = _object(definition, f"blockchain.validators.{validator_id}").get("group")
        if not isinstance(group_id, str):
            raise _fail(f"blockchain.validators.{validator_id}.group is required")
        group_services.setdefault(group_id, []).append(validator_id)
        group_members.setdefault(group_id, []).append(validator_id)
    for peer_id, definition in peers.items():
        group_id = _object(definition, f"storage.peers.{peer_id}").get("group")
        if not isinstance(group_id, str):
            raise _fail(f"storage.peers.{peer_id}.group is required")
        suffix = peer_id.removeprefix("storage-")
        group_services.setdefault(group_id, []).extend([f"ipfs-storage-{suffix}", f"cluster-storage-{suffix}"])
        group_members.setdefault(group_id, []).append(peer_id)
    overrides = _object(raw.get("overrides", {}), "overrides")
    _only_keys(overrides, "overrides", {"explorer", "settings", "components"})
    explorer_override = _object(overrides.get("explorer", {}), "overrides.explorer")
    _only_keys(explorer_override, "overrides.explorer", {"group"})
    explorer_group = explorer_override.get("group", "apps")
    if explorer_group != "apps":
        group_services["apps"].remove("explorer")
        group_services.setdefault(explorer_group, []).append("explorer")
        group_members.setdefault(explorer_group, []).append("explorer")
    for group_id, services in group_services.items():
        if not services:
            continue
        kind = "apps" if group_id == "apps" else ("storage" if group_id.startswith("storage") else "validators")
        result["groups"][group_id] = {"kind": kind, "machine": group_machine(group_id), "members": group_members[group_id], "services": services}
        for service_id in services:
            result["services"][service_id]["machine"] = result["groups"][group_id]["machine"]

    # Translate compact chain choices into the original v3 block.
    result["blockchain"]["chain_id"] = blockchain.get("chain_id")
    for field in ("besu_image", "qbft"):
        if field in blockchain:
            result["blockchain"][field] = deepcopy(blockchain[field])
    if "artifact" in blockchain:
        result["blockchain"]["artifact"] = deepcopy(_object(blockchain["artifact"], "blockchain.artifact"))

    access = _object(raw.get("access", {"mode": "none"}), "access")
    mode = access.get("mode", "none")
    if mode not in {"none", "local-direct", "gateway"}:
        raise _fail("access.mode must be none, local-direct or gateway")
    if mode != "gateway":
        result["services"].pop("edge-proxy", None)
        for services in group_services.values():
            if "edge-proxy" in services:
                services.remove("edge-proxy")
    else:
        result["services"]["edge-proxy"]["machine"] = app_machine
        bind = access.get("bind")
        port = access.get("port", 8080)
        if bind not in {"loopback", "private", "public"}:
            raise _fail("access.bind must be loopback, private or public for gateway")
        exposure = {"mode": bind, "port": port, "protocols": ["tcp"]}
        if bind == "private":
            exposure["network"] = access.get("network")
        result["services"]["edge-proxy"]["exposure"] = exposure

    # Recalculate the connection transport and only publish what a remote edge needs.
    routing = _object(raw.get("routing", {}), "routing")
    # P2P is not represented as a service connection, but v3 still needs a
    # concrete network to derive Besu/Kubo/Cluster announce addresses.
    if routing:
        for key, infrastructure_key in (("blockchain_p2p", "besu"), ("storage_p2p", "ipfs"), ("storage_p2p", "cluster")):
            if key in routing:
                result["infrastructure"][infrastructure_key]["network"] = routing[key]
    machines = result["machines"]
    required_network: dict[str, str] = {}
    for consumer in result["services"].values():
        for connection in consumer.get("connections", {}).values():
            provider = result["services"][connection["service"]]
            if consumer["machine"] == provider["machine"]:
                connection.pop("network", None); connection.pop("protocol", None)
                continue
            network = _network_for(consumer, provider, routing)
            if network not in machines[provider["machine"]]["addresses"]:
                raise _fail(f"routing selects {network} but it is not available on {provider['machine']}")
            if network not in machines[consumer["machine"]]["addresses"] and not _route_exists(result.get("routes", []), machines[consumer["machine"]], network):
                raise _fail(f"routing selects {network} but it is neither shared nor routed from {consumer['machine']} to {provider['machine']}")
            prior = required_network.setdefault(connection["service"], network)
            if prior != network:
                raise _fail(f"{connection['service']} needs incompatible private networks {prior} and {network}")
            connection["network"] = network; connection["protocol"] = "tcp"
    # Store obtains both Cluster and Kubo API URLs from each declared Cluster
    # edge.  v3's visible graph models only the Cluster edge, so add Kubo's
    # required private exposure here rather than relying on a stale example.
    store = result["services"]["store-api"]
    for connection in store.get("connections", {}).values():
        cluster = result["services"][connection["service"]]
        kubo_id = cluster["connections"]["kubo"]["service"]
        kubo = result["services"][kubo_id]
        if store["machine"] != kubo["machine"]:
            network = connection["network"]
            prior = required_network.setdefault(kubo_id, network)
            if prior != network:
                raise _fail(f"{kubo_id} needs incompatible private networks {prior} and {network}")
    for service_id, network in required_network.items():
        service = result["services"][service_id]
        port = _PORTS.get(service["type"])
        if port is None:
            current = service.get("exposure", {})
            port = current.get("port")
        if not isinstance(port, int):
            raise _fail(f"cannot derive private port for {service_id}")
        service["exposure"] = {"mode": "private", "network": network, "port": port, "protocols": ["tcp"]}
    for service_id, service in result["services"].items():
        exposure = service.get("exposure")
        if exposure and exposure.get("mode") == "private" and service_id not in required_network:
            # Catalogued private endpoints are examples, not a request to
            # publish a port when no resolved remote dependency needs it.
            service.pop("exposure", None)
    if mode == "local-direct":
        for service_id in ("rpc01", "store-api", "minter-api", "admin-api", "resolver-api", "dashboard", "explorer"):
            service = result["services"].get(service_id)
            if service and service["machine"] == app_machine:
                service["exposure"] = {"mode": "loopback", "port": _PORTS[service["type"]], "protocols": ["tcp"]}

    networking = _object(raw.get("networking", {}), "networking")
    _only_keys(networking, "networking", {"services"})
    service_networking = _object(networking.get("services", {}), "networking.services")
    p2p_types = {"besu-rpc", "besu-validator", "ipfs-kubo", "ipfs-cluster"}
    for service_id, configuration in service_networking.items():
        _identifier(service_id, f"networking.services.{service_id}")
        if service_id not in result["services"]:
            raise _fail(f"networking.services.{service_id} references an unknown service")
        configuration = _object(configuration, f"networking.services.{service_id}")
        _only_keys(configuration, f"networking.services.{service_id}", {"advertise_address", "advertise_port", "p2p_advertise_port"})
        service = result["services"][service_id]
        advertised = {field: configuration[field] for field in ("advertise_address", "advertise_port") if field in configuration}
        if advertised:
            exposure = service.get("exposure")
            if not exposure or exposure.get("mode") != "private":
                raise _fail(f"networking.services.{service_id} can advertise only a derived private endpoint")
            exposure.update(advertised)
        if "p2p_advertise_port" in configuration:
            port = configuration["p2p_advertise_port"]
            if service["type"] not in p2p_types:
                raise _fail(f"networking.services.{service_id}.p2p_advertise_port is only valid for a P2P service")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise _fail(f"networking.services.{service_id}.p2p_advertise_port must be a port")
            service.setdefault("configuration", {})["p2p_advertise_port"] = port

    secrets = _object(raw.get("secrets", {}), "secrets")
    root = secrets.get("source_root")
    if root is not None and not isinstance(root, str):
        raise _fail("secrets.source_root must be a string")
    sources = _object(secrets.get("sources", {}), "secrets.sources")
    for secret_id, definition in result["secrets"].items():
        source = sources.get(secret_id)
        if source is None and root:
            source = str(Path(root) / definition["path"])
        if source is not None:
            definition["source"] = source if Path(source).is_absolute() else str((source_path.parent / source).resolve())
    for section in ("settings", "components"):
        if section in overrides:
            result[section] = _merge(result[section], _object(overrides[section], f"overrides.{section}"), f"overrides.{section}")

    try:
        validate_inventory(result)
    except InventoryError as exc:
        raise _fail(str(exc).removeprefix("deployment topology v3: ")) from exc
    provenance = {"/components": "catalog:dark-standard-1", "/settings": "catalog:dark-standard-1", "/services": "catalog recipe + placement/routing", "/groups": "placement", "/storage": "storage", "/blockchain": "blockchain"}
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    metadata = {"catalog": catalog.id, "catalog_sha256": catalog.digest, "resolver_version": "1", "input_sha256": sha256(canonical).hexdigest()}
    warnings = ("Two storage peers on one machine provide replication but not host-failure tolerance.",) if len(peers) > 1 and len({result["groups"][g]["machine"] for g in result["groups"] if g.startswith("storage")}) == 1 else ()
    return ResolutionResult(result, provenance, warnings, metadata)


def resolve_inventory_path(path: Path) -> ResolutionResult:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"cannot read {path}: {exc}") from exc
    return resolve_inventory(raw, source_path=path)
