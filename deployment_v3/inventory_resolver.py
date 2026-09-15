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


def _selector_network(selector: object, *, source_machine: dict, target_machine: dict, sites: dict, path: str) -> str:
    """Resolve a literal network or the compact ``site_lan`` selector."""
    if not isinstance(selector, str) or not selector:
        raise _fail(f"{path} must select a network or site_lan")
    if selector != "site_lan":
        return selector
    site = target_machine.get("site")
    if not isinstance(site, str) or site not in sites or not isinstance(sites[site], dict) or not isinstance(sites[site].get("lan"), str):
        raise _fail(f"{path} uses site_lan but target machine has no declared site ({target_machine.get('management_address', 'unknown')})")
    return sites[site]["lan"]


def _network_for(consumer: dict, provider: dict, routing: dict, machines: dict, sites: dict, *, traffic_kind: str | None = None) -> str:
    if traffic_kind:
        kind = traffic_kind
    elif provider["type"] == "ipfs-cluster":
        kind = "storage_api"
    elif provider["type"] in {"ipfs-kubo", "ipfs-cluster"}:
        kind = "storage_p2p"
    else:
        kind = "application_api"
    policy = routing.get(kind, routing.get("defaults"))
    if policy is None:
        raise _fail(f"routing.{kind} is required for remote connection to {provider['type']}")
    source_machine = machines[consumer["machine"]]
    target_machine = machines[provider["machine"]]
    if isinstance(policy, str):
        return _selector_network(policy, source_machine=source_machine, target_machine=target_machine, sites=sites, path=f"routing.{kind}")
    if not isinstance(policy, dict):
        raise _fail(f"routing.{kind} must be a network, site_lan or locality policy")
    _only_keys(policy, f"routing.{kind}", {"same_site", "cross_site"})
    source_site, target_site = source_machine.get("site"), target_machine.get("site")
    if not isinstance(source_site, str) or not isinstance(target_site, str):
        raise _fail(f"routing.{kind} locality policy requires sites on both machines")
    branch = "same_site" if source_site == target_site else "cross_site"
    return _selector_network(policy.get(branch), source_machine=source_machine, target_machine=target_machine, sites=sites, path=f"routing.{kind}.{branch}")


def _route_exists(routes: list[dict], consumer: dict, provider_network: str) -> bool:
    source_networks = set(consumer.get("addresses", {}))
    return any(isinstance(route, dict) and route.get("from") in source_networks and route.get("to") == provider_network for route in routes)


def _service_group(service_id: str, services: dict, groups: dict) -> str:
    for group_id, definition in groups.items():
        if service_id in definition["services"]:
            return group_id
    raise _fail(f"internal recipe did not place {service_id}")


def _validate_production_objectives(raw: dict, result: dict) -> tuple[str, ...]:
    if raw.get("profile") != "production":
        return ()
    policy = _object(raw.get("availability"), "availability")
    objectives = set(policy.get("objectives", []))
    acknowledgements = set(policy.get("acknowledgements", []))
    if not objectives:
        raise _fail("production profile requires availability.objectives")
    services = result["services"]
    groups = result["groups"]
    validators = [key for key, value in services.items() if value["type"] == "besu-validator"]
    observers = [key for key, value in services.items() if value["type"] == "besu-observer"]
    rpcs = [key for key, value in services.items() if value["type"] == "besu-rpc"]
    quorum = (2 * len(validators)) // 3 + 1
    validator_groups = [sum(service in validators for service in group["services"]) for group in groups.values()]
    machines = set(value["machine"] for value in services.values())
    validators_by_machine = [sum(value["type"] == "besu-validator" and value["machine"] == machine for value in services.values()) for machine in machines]
    storage_by_machine = [sum(value["type"] == "ipfs-kubo" and value["machine"] == machine for value in services.values()) for machine in machines]
    target = result["storage"]["replication"]["target_replicas"]
    storage_count = sum(value["type"] == "ipfs-kubo" for value in services.values())
    satisfied = {
        "tolerate_validator_individual": len(validators) - 1 >= quorum,
        "tolerate_validator_group": all(len(validators) - lost >= quorum for lost in validator_groups),
        "tolerate_machine": all(len(validators) - lost >= quorum for lost in validators_by_machine),
        "observer_copy": bool(observers),
        "rpc_redundant": len(rpcs) >= 2,
        "storage_durable": all(storage_count - lost >= target for lost in storage_by_machine),
    }
    unmet = sorted(objective for objective in objectives if not satisfied[objective])
    unacknowledged = [objective for objective in unmet if objective not in acknowledgements]
    if unacknowledged:
        raise _fail("production availability objective(s) not met and not acknowledged: " + ", ".join(unacknowledged))
    irrelevant = sorted(acknowledgements - set(unmet))
    if irrelevant:
        raise _fail("availability.acknowledgements contains satisfied or unselected objective(s): " + ", ".join(irrelevant))
    return tuple(f"Production objective acknowledged but not met: {objective}." for objective in unmet)


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
    _only_keys(storage, "storage", {"cluster_name", "peers", "replication", "api_replicas"})
    peers = _object(storage.get("peers"), "storage.peers")
    if not peers:
        raise _fail("storage.peers must contain at least one peer")
    if set(blockchain) - {"chain_id", "rpc", "validator_groups", "observer_groups", "artifact", "qbft", "observer_max_block_lag"}:
        raise _fail("blockchain contains unsupported fields")
    rpc = _object(blockchain.get("rpc"), "blockchain.rpc")
    validator_groups = _object(blockchain.get("validator_groups"), "blockchain.validator_groups")
    if not validator_groups:
        raise _fail("blockchain.validator_groups must contain at least one group")
    validators = {}
    index = 1
    for group_id, definition in validator_groups.items():
        _identifier(group_id, f"blockchain.validator_groups.{group_id}")
        count = _object(definition, f"blockchain.validator_groups.{group_id}").get("validator_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise _fail(f"blockchain.validator_groups.{group_id}.validator_count must be a positive integer")
        for _ in range(count):
            validators[f"validator{index:02d}"] = {"group": group_id}; index += 1
    observers = {}
    index = 1
    for group_id, definition in _object(blockchain.get("observer_groups", {}), "blockchain.observer_groups").items():
        _identifier(group_id, f"blockchain.observer_groups.{group_id}")
        count = _object(definition, f"blockchain.observer_groups.{group_id}").get("observer_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise _fail(f"blockchain.observer_groups.{group_id}.observer_count must be a positive integer")
        for _ in range(count):
            observers[f"observer{index:02d}"] = {"group": group_id}; index += 1
    rpc_nodes = _object(rpc.get("nodes"), "blockchain.rpc.nodes")
    primary_rpc = rpc.get("primary")
    if not rpc_nodes or primary_rpc not in rpc_nodes:
        raise _fail("blockchain.rpc.primary must name a declared RPC node")
    if not validators or not rpc_nodes:
        raise _fail("blockchain requires at least one validator and one RPC")

    result = deepcopy(catalog.document)
    templates = deepcopy(catalog.service_templates)
    dynamic_types = {"besu-validator", "besu-rpc", "besu-observer", "ipfs-kubo", "ipfs-cluster"}
    result["services"] = {key: value for key, value in result["services"].items() if value["type"] not in dynamic_types}
    for service in result["services"].values():
        for connection in service.get("connections", {}).values():
            if connection.get("service") == "rpc01":
                connection["service"] = primary_rpc
    for node_id, definition in rpc_nodes.items():
        _identifier(node_id, f"blockchain.rpc.nodes.{node_id}")
        service = deepcopy(templates["besu-rpc"]); service["configuration"]["node_id"] = node_id
        result["services"][node_id] = service
    for node_id in validators:
        service = deepcopy(templates["besu-validator"]); service["configuration"]["node_id"] = node_id
        result["services"][node_id] = service
    for node_id in observers:
        service = deepcopy(templates["besu-observer"]); service["configuration"]["node_id"] = node_id
        result["services"][node_id] = service
    bindings = _object(rpc.get("bindings", {}), "blockchain.rpc.bindings")
    rpc_providers = set(rpc_nodes) | set(observers)
    for consumer_id, provider_id in bindings.items():
        if consumer_id not in result["services"]:
            raise _fail(f"blockchain.rpc.bindings.{consumer_id} names an unknown consumer service")
        if provider_id not in rpc_providers:
            raise _fail(f"blockchain.rpc.bindings.{consumer_id} must name an RPC or observer node")
        connections = result["services"][consumer_id].get("connections", {})
        if "rpc" not in connections:
            raise _fail(f"blockchain.rpc.bindings.{consumer_id} does not have an RPC connection")
        connections["rpc"]["service"] = provider_id
    result["blockchain"]["nodes"] = {
        **{node: {"role": "validator", "p2p_port": 30303} for node in validators},
        **{node: {"role": "rpc", "p2p_port": 30303} for node in rpc_nodes},
        **{node: {"role": "observer", "p2p_port": 30303} for node in observers},
    }
    result["blockchain"]["primary_rpc"] = primary_rpc
    result["blockchain"]["observer_max_block_lag"] = blockchain.get("observer_max_block_lag", 1)
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
    site_definitions = _object(raw.get("sites", {}), "sites")
    if site_definitions:
        result["sites"] = deepcopy(site_definitions)
    if "routes" in raw:
        result["routes"] = deepcopy(raw["routes"])

    # Compact local machines intentionally need no fake SSH configuration.
    result["machines"] = {}
    for machine_id, definition in machines_input.items():
        _identifier(machine_id, f"machines.{machine_id}")
        definition = _object(definition, f"machines.{machine_id}")
        execution = definition.get("execution")
        if execution not in {"local", "docker-lab", "ssh", "auto"}:
            raise _fail(f"machines.{machine_id}.execution must be local, docker-lab, ssh or auto")
        item = {"execution": execution}
        if execution not in {"local", "docker-lab"}:
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
        if "site" in definition:
            item["site"] = definition["site"]
        result["machines"][machine_id] = item

    def group_machine(group_id: str) -> str:
        value = placement.get(group_id)
        if not isinstance(value, str) or value not in result["machines"]:
            raise _fail(f"placement.{group_id} must name a declared machine")
        return value

    if not peers:
        raise _fail("storage.peers must contain at least one peer")
    result["services"]["store-api"]["connections"] = {}
    for peer_id, definition in peers.items():
        _identifier(peer_id, f"storage.peers.{peer_id}")
        suffix = peer_id.removeprefix("storage-")
        kubo_id, cluster_id = f"ipfs-storage-{suffix}", f"cluster-storage-{suffix}"
        kubo = deepcopy(templates["ipfs-kubo"]); kubo["configuration"]["peer_name"] = peer_id
        cluster = deepcopy(templates["ipfs-cluster"]); cluster["configuration"]["peer_name"] = peer_id
        cluster["connections"] = {"kubo": {"service": kubo_id}}
        result["services"][kubo_id] = kubo; result["services"][cluster_id] = cluster
        result["services"]["store-api"]["connections"][f"cluster_{cluster_id}"] = {"service": cluster_id}
    result["secrets"]["ipfs-swarm-key"]["consumers"] = sorted(
        service_id for service_id, service in result["services"].items() if service["type"] == "ipfs-kubo"
    )
    result["secrets"]["ipfs-cluster-secret"]["consumers"] = sorted(
        service_id for service_id, service in result["services"].items() if service["type"] == "ipfs-cluster"
    )
    result["storage"]["cluster_name"] = storage.get("cluster_name", result["storage"]["cluster_name"])
    result["storage"]["nodes"] = list(peers)
    result["storage"]["replication"] = deepcopy(_object(storage.get("replication"), "storage.replication"))

    result["groups"] = {}
    app_machine = group_machine("apps")
    api_replicas = _object(storage.get("api_replicas", {}), "storage.api_replicas")
    replica_groups: dict[str, str] = {}
    for service_id, definition in api_replicas.items():
        _identifier(service_id, f"storage.api_replicas.{service_id}")
        if service_id in result["services"]:
            raise _fail(f"storage.api_replicas.{service_id} duplicates a catalogue service")
        definition = _object(definition, f"storage.api_replicas.{service_id}")
        _only_keys(definition, f"storage.api_replicas.{service_id}", {"group", "consumers"})
        group_id = definition.get("group")
        if not isinstance(group_id, str):
            raise _fail(f"storage.api_replicas.{service_id}.group is required")
        group_machine(group_id)
        consumers = definition.get("consumers")
        if not isinstance(consumers, list) or not consumers or len(set(consumers)) != len(consumers):
            raise _fail(f"storage.api_replicas.{service_id}.consumers must be a non-empty list of unique service IDs")
        replica = deepcopy(result["services"]["store-api"])
        for consumer_id in consumers:
            if not isinstance(consumer_id, str) or consumer_id not in result["services"]:
                raise _fail(f"storage.api_replicas.{service_id}.consumers references an unknown service")
            connection = result["services"][consumer_id].get("connections", {}).get("store_api")
            if connection is None:
                raise _fail(f"storage.api_replicas.{service_id}.consumers may name only services with a store_api connection")
            connection["service"] = service_id
        result["services"][service_id] = replica
        replica_groups[service_id] = group_id
    dynamic_ids = set(validators) | set(observers) | set(rpc_nodes) | set(replica_groups) | {sid for sid, service in result["services"].items() if service["type"] in {"ipfs-kubo", "ipfs-cluster"}}
    group_services = {"apps": [sid for sid in result["services"] if sid not in dynamic_ids]}
    group_members = {"apps": []}
    for rpc_id, definition in rpc_nodes.items():
        group_id = _object(definition, f"blockchain.rpc.nodes.{rpc_id}").get("group")
        if not isinstance(group_id, str): raise _fail(f"blockchain.rpc.nodes.{rpc_id}.group is required")
        group_services.setdefault(group_id, []).append(rpc_id); group_members.setdefault(group_id, []).append(rpc_id)
    for validator_id, definition in validators.items():
        group_id = _object(definition, f"blockchain.validators.{validator_id}").get("group")
        if not isinstance(group_id, str):
            raise _fail(f"blockchain.validators.{validator_id}.group is required")
        group_services.setdefault(group_id, []).append(validator_id)
        group_members.setdefault(group_id, []).append(validator_id)
    for observer_id, definition in observers.items():
        group_id = _object(definition, f"blockchain.observers.{observer_id}").get("group")
        group_services.setdefault(group_id, []).append(observer_id)
        group_members.setdefault(group_id, []).append(observer_id)
    for peer_id, definition in peers.items():
        group_id = _object(definition, f"storage.peers.{peer_id}").get("group")
        if not isinstance(group_id, str):
            raise _fail(f"storage.peers.{peer_id}.group is required")
        suffix = peer_id.removeprefix("storage-")
        group_services.setdefault(group_id, []).extend([f"ipfs-storage-{suffix}", f"cluster-storage-{suffix}"])
        group_members.setdefault(group_id, []).append(peer_id)
    for service_id, group_id in replica_groups.items():
        group_services.setdefault(group_id, []).append(service_id)
        group_members.setdefault(group_id, []).append(service_id)
    if "access" in raw and "proxies" in raw:
        raise _fail("access is a legacy compatibility field and cannot be combined with proxies")
    overrides = _object(raw.get("overrides", {}), "overrides")
    _only_keys(overrides, "overrides", {"explorer", "resolver", "settings", "components"})
    explorer_override = _object(overrides.get("explorer", {}), "overrides.explorer")
    _only_keys(explorer_override, "overrides.explorer", {"group"})
    explorer_group = explorer_override.get("group", "apps")
    if explorer_group != "apps":
        group_services["apps"].remove("explorer")
        group_services.setdefault(explorer_group, []).append("explorer")
        group_members.setdefault(explorer_group, []).append("explorer")
    resolver_override = _object(overrides.get("resolver", {}), "overrides.resolver")
    _only_keys(resolver_override, "overrides.resolver", {"group"})
    resolver_group = resolver_override.get("group", "apps")
    if resolver_group != "apps":
        group_services["apps"].remove("resolver-api")
        group_services.setdefault(resolver_group, []).append("resolver-api")
        group_members.setdefault(resolver_group, []).append("resolver-api")
    for group_id, services in group_services.items():
        if not services:
            continue
        types = {result["services"][service]["type"] for service in services}
        kind = "storage" if types <= {"ipfs-kubo", "ipfs-cluster"} else "observers" if types == {"besu-observer"} else "validators" if types == {"besu-validator"} else "apps"
        result["groups"][group_id] = {"kind": kind, "machine": group_machine(group_id), "members": group_members[group_id], "services": services}
        for service_id in services:
            result["services"][service_id]["machine"] = result["groups"][group_id]["machine"]

    # Translate compact chain choices into the original v3 block.
    result["blockchain"]["chain_id"] = blockchain.get("chain_id")
    for field in ("qbft",):
        if field in blockchain:
            result["blockchain"][field] = deepcopy(blockchain[field])
    if "artifact" in blockchain:
        result["blockchain"]["artifact"] = deepcopy(_object(blockchain["artifact"], "blockchain.artifact"))

    access = _object(raw.get("access", {"mode": "none"}), "access")
    mode = access.get("mode", "none")
    if mode not in {"none", "local-direct", "gateway"}:
        raise _fail("access.mode must be none, local-direct or gateway")
    if "proxies" in raw:
        # The catalogue has an old single edge service only so old operator
        # documents remain resolvable.  New documents declare every proxy and
        # every public rule explicitly.
        result["services"].pop("edge-proxy", None)
        for group in result["groups"].values():
            group["services"] = [service for service in group["services"] if service != "edge-proxy"]
        proxies = _object(raw["proxies"], "proxies")
        if not proxies:
            raise _fail("proxies must contain at least one proxy")
        for proxy_id, definition in proxies.items():
            _identifier(proxy_id, f"proxies.{proxy_id}")
            definition = _object(definition, f"proxies.{proxy_id}")
            _only_keys(definition, f"proxies.{proxy_id}", {"group", "listener", "tls", "sites"})
            group_id = definition.get("group")
            if not isinstance(group_id, str) or group_id not in result["groups"]:
                raise _fail(f"proxies.{proxy_id}.group must name a placed group")
            listener = _object(definition.get("listener"), f"proxies.{proxy_id}.listener")
            _only_keys(listener, f"proxies.{proxy_id}.listener", {"bind", "port", "network"})
            bind = listener.get("bind")
            port = listener.get("port")
            if bind not in {"loopback", "private", "public"} or not isinstance(port, int) or not 1 <= port <= 65535:
                raise _fail(f"proxies.{proxy_id}.listener requires bind (loopback, private or public) and a TCP port")
            exposure = {"mode": bind, "port": port, "protocols": ["tcp"]}
            if bind == "private":
                network = listener.get("network")
                if not isinstance(network, str):
                    raise _fail(f"proxies.{proxy_id}.listener.network is required for private bind")
                exposure["network"] = network
            elif "network" in listener:
                raise _fail(f"proxies.{proxy_id}.listener.network is only valid for private bind")
            sites = definition.get("sites")
            if not isinstance(sites, list):
                raise _fail(f"proxies.{proxy_id}.sites must be a list")
            connections = {}
            normalized_sites = []
            for site_index, site in enumerate(sites):
                site = _object(site, f"proxies.{proxy_id}.sites[{site_index}]")
                _only_keys(site, f"proxies.{proxy_id}.sites[{site_index}]", {"host", "public_origin", "routes"})
                routes = site.get("routes")
                if not isinstance(routes, list):
                    raise _fail(f"proxies.{proxy_id}.sites[{site_index}].routes must be a list")
                normalized_routes = []
                for route_index, route in enumerate(routes):
                    route = _object(route, f"proxies.{proxy_id}.sites[{site_index}].routes[{route_index}]")
                    _only_keys(route, f"proxies.{proxy_id}.sites[{site_index}].routes[{route_index}]", {"id", "path", "service", "upstream_path"})
                    route_id = _identifier(route.get("id"), f"proxies.{proxy_id}.sites[{site_index}].routes[{route_index}].id")
                    service_id = route.get("service")
                    if not isinstance(service_id, str) or service_id not in result["services"]:
                        raise _fail(f"proxies.{proxy_id}.sites[{site_index}].routes[{route_index}].service must name a catalogue service")
                    if route_id in connections:
                        raise _fail(f"proxies.{proxy_id} has duplicate route id {route_id}")
                    connections[route_id] = {"service": service_id}
                    normalized_routes.append({"id": route_id, "path": route.get("path"), "connection": route_id, "upstream_path": route.get("upstream_path")})
                normalized_sites.append({"host": site.get("host", ""), "public_origin": site.get("public_origin"), "routes": normalized_routes})
            configuration = {"sites": normalized_sites, "tls": deepcopy(definition.get("tls", {"mode": "http"}))}
            result["services"][proxy_id] = {"type": "edge-proxy", "machine": result["groups"][group_id]["machine"], "connections": connections, "configuration": configuration, "exposure": exposure}
            result["groups"][group_id]["services"].append(proxy_id)
    elif mode != "gateway":
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
    # Legacy inventories retain one infrastructure network.  A locality policy
    # is expanded below into explicit ``peerings`` and does not overwrite that
    # compatibility value with a dict.
    if routing:
        for key, infrastructure_key in (("blockchain_p2p", "besu"), ("storage_p2p", "ipfs"), ("storage_p2p", "cluster")):
            if isinstance(routing.get(key), str) and routing[key] != "site_lan":
                result["infrastructure"][infrastructure_key]["network"] = routing[key]
            elif isinstance(routing.get(key), dict):
                # Required legacy field; explicit peerings take precedence.
                fallback = routing[key].get("cross_site")
                if fallback == "site_lan":
                    fallback = next(iter(site_definitions.values()), {}).get("lan")
                if isinstance(fallback, str):
                    result["infrastructure"][infrastructure_key]["network"] = fallback
    machines = result["machines"]
    required_networks: dict[str, set[str]] = {}
    for consumer in result["services"].values():
        for connection in consumer.get("connections", {}).values():
            provider = result["services"][connection["service"]]
            # contracts-deploy is a one-shot artifact producer, not an HTTP
            # endpoint. Its dependency orders consumers after the handoff but
            # must never manufacture a private listener.
            if provider["type"] == "contracts-deploy":
                connection.pop("network", None); connection.pop("protocol", None)
                continue
            if consumer["machine"] == provider["machine"]:
                connection.pop("network", None); connection.pop("protocol", None)
                continue
            network = _network_for(consumer, provider, routing, machines, site_definitions)
            if network not in machines[provider["machine"]]["addresses"]:
                raise _fail(f"routing selects {network} but it is not available on {provider['machine']}")
            if network not in machines[consumer["machine"]]["addresses"] and not _route_exists(result.get("routes", []), machines[consumer["machine"]], network):
                raise _fail(f"routing selects {network} but it is neither shared nor routed from {consumer['machine']} to {provider['machine']}")
            required_networks.setdefault(connection["service"], set()).add(network)
            connection["network"] = network; connection["protocol"] = "tcp"
    # Store obtains both Cluster and Kubo API URLs from each declared Cluster
    # edge.  v3's visible graph models only the Cluster edge, so add Kubo's
    # required private exposure here rather than relying on a stale example.
    for store in (service for service in result["services"].values() if service["type"] == "store-api"):
        for connection in store.get("connections", {}).values():
            cluster = result["services"][connection["service"]]
            kubo_id = cluster["connections"]["kubo"]["service"]
            kubo = result["services"][kubo_id]
            if store["machine"] != kubo["machine"]:
                network = connection["network"]
                required_networks.setdefault(kubo_id, set()).add(network)
    for service_id, networks_for_service in required_networks.items():
        service = result["services"][service_id]
        port = _PORTS.get(service["type"])
        if port is None:
            current = service.get("exposure", {})
            port = current.get("port")
        if not isinstance(port, int):
            raise _fail(f"cannot derive private port for {service_id}")
        if len(networks_for_service) == 1:
            service["exposure"] = {"mode": "private", "network": next(iter(networks_for_service)), "port": port, "protocols": ["tcp"]}
        else:
            service.pop("exposure", None)
            service["listeners"] = [
                {"network": network, "port": port, "protocols": ["tcp"]}
                for network in sorted(networks_for_service)
            ]
    for service_id, service in result["services"].items():
        exposure = service.get("exposure")
        if exposure and exposure.get("mode") == "private" and service_id not in required_networks and service.get("type") != "edge-proxy":
            # Catalogued private endpoints are examples, not a request to
            # publish a port when no resolved remote dependency needs it.
            service.pop("exposure", None)
    # Besu deliberately ships without curl/wget. For an all-local deployment,
    # expose RPC only on loopback so controller-side readiness and verification
    # can perform real JSON-RPC checks without making the endpoint public.
    rpc_service = result["services"][primary_rpc]
    if all(machine.get("execution") in {"local", "docker-lab"} for machine in machines.values()) and not rpc_service.get("exposure"):
        rpc_service["exposure"] = {"mode": "loopback", "port": _PORTS["besu-rpc"], "protocols": ["tcp"]}
    if mode == "local-direct":
        for service_id in (primary_rpc, "store-api", "minter-api", "admin-api", "resolver-api", "dashboard", "explorer"):
            service = result["services"].get(service_id)
            if service and service["machine"] == app_machine:
                service["exposure"] = {"mode": "loopback", "port": _PORTS[service["type"]], "protocols": ["tcp"]}

    networking = _object(raw.get("networking", {}), "networking")
    _only_keys(networking, "networking", {"services"})
    service_networking = _object(networking.get("services", {}), "networking.services")
    p2p_types = {"besu-rpc", "besu-validator", "besu-observer", "ipfs-kubo", "ipfs-cluster"}
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

    def locality_policy(value: object) -> bool:
        return value == "site_lan" or isinstance(value, dict)

    # Expand the compact site policy into a directed, auditable P2P matrix.
    # It is deliberately generated after all placements and port overrides are
    # known, so artifacts, Compose and readiness share one source of truth.
    if locality_policy(routing.get("blockchain_p2p", routing.get("defaults"))):
        node_services = [service for service in result["services"].values() if service["type"] in {"besu-rpc", "besu-validator", "besu-observer"}]
        rank = {"validator": 0, "rpc": 1, "observer": 2}
        ordered_nodes = sorted(node_services, key=lambda item: (rank[result["blockchain"]["nodes"][item["configuration"]["node_id"]]["role"]], item["configuration"]["node_id"]))
        # Service definitions are keyed by ID; build the port map explicitly
        # rather than relying on dict values carrying their own key.
        ordered_ids = [service_id for service_id, service in result["services"].items() if service in ordered_nodes]
        ordered_ids.sort(key=lambda service_id: (rank[result["blockchain"]["nodes"][result["services"][service_id]["configuration"]["node_id"]]["role"]], result["services"][service_id]["configuration"]["node_id"]))
        ports = {
            service_id: int(result["services"][service_id]["configuration"].get("p2p_advertise_port", result["infrastructure"]["besu"]["p2p_port_start"] + index))
            for index, service_id in enumerate(ordered_ids)
        }
        result.setdefault("peerings", {})["blockchain"] = [
            {
                "from": source_id, "to": target_id,
                "network": _network_for(result["services"][source_id], result["services"][target_id], routing, machines, site_definitions, traffic_kind="blockchain_p2p"),
                "address": machines[result["services"][target_id]["machine"]]["addresses"][_network_for(result["services"][source_id], result["services"][target_id], routing, machines, site_definitions, traffic_kind="blockchain_p2p")],
                "port": ports[target_id],
            }
            for source_id in ordered_ids for target_id in ordered_ids if source_id != target_id
        ]
    if locality_policy(routing.get("storage_p2p", routing.get("defaults"))):
        for family, service_type, infrastructure_key, port_key in (
            ("ipfs", "ipfs-kubo", "ipfs", "swarm_port"),
            ("cluster", "ipfs-cluster", "cluster", "p2p_port"),
        ):
            ids = sorted(service_id for service_id, service in result["services"].items() if service["type"] == service_type)
            result.setdefault("peerings", {})[family] = [
                {
                    "from": source_id, "to": target_id,
                    "network": _network_for(result["services"][source_id], result["services"][target_id], routing, machines, site_definitions, traffic_kind="storage_p2p"),
                    "address": machines[result["services"][target_id]["machine"]]["addresses"][_network_for(result["services"][source_id], result["services"][target_id], routing, machines, site_definitions, traffic_kind="storage_p2p")],
                    "port": int(result["services"][target_id]["configuration"].get("p2p_advertise_port", result["infrastructure"][infrastructure_key][port_key])),
                }
                for source_id in ids for target_id in ids if source_id != target_id
            ]

    # Storage APIs are used first for bootstrap discovery. When locality
    # routing sends different consumers through LAN and VPN, a single legacy
    # exposure is insufficient; materialize one listener per selected network.
    for provider_id, provider in result["services"].items():
        if provider["type"] not in {"ipfs-kubo", "ipfs-cluster"}:
            continue
        exposure = provider.get("exposure")
        if not isinstance(exposure, dict) or exposure.get("mode") != "private":
            continue
        networks = {
            connection.get("network")
            for consumer in result["services"].values()
            for connection in consumer.get("connections", {}).values()
            if connection.get("service") == provider_id
            and consumer.get("machine") != provider.get("machine")
            and isinstance(connection.get("network"), str)
        }
        family = "ipfs" if provider["type"] == "ipfs-kubo" else "cluster"
        networks.update(
            edge["network"]
            for edge in result.get("peerings", {}).get(family, [])
            if edge.get("to") == provider_id and isinstance(edge.get("network"), str)
        )
        all_networks = networks | {exposure.get("network")}
        if len(all_networks) > 1:
            provider.pop("exposure", None)
            provider["listeners"] = [
                {"network": network, "port": int(exposure["port"]), "protocols": list(exposure.get("protocols", ["tcp"]))}
                for network in sorted(all_networks)
                if isinstance(network, str)
            ]

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
    provenance = {"/components": "catalog:dark-platform-baseline-v1.0", "/images": "catalog:dark-platform-baseline-v1.0", "/settings": "catalog:dark-platform-baseline-v1.0", "/services": "catalog recipe + placement/routing", "/groups": "placement", "/storage": "storage", "/blockchain": "blockchain"}
    if "sites" in result:
        provenance["/sites"] = "operator sites"
    if "peerings" in result:
        provenance["/peerings"] = "operator locality routing"
    for node_id, definition in result["blockchain"]["nodes"].items():
        role = definition["role"]
        if role == "validator":
            source = "blockchain.validator_groups"
        elif role == "observer":
            source = "blockchain.observer_groups"
        else:
            source = f"blockchain.rpc.nodes.{node_id}"
        provenance[f"/blockchain/nodes/{node_id}"] = source
    for group_id in result["groups"]:
        provenance[f"/groups/{group_id}"] = f"placement.{group_id}"
    for service_id, service in result["services"].items():
        provenance[f"/services/{service_id}"] = "catalog singleton" if service_id not in dynamic_ids else "operator cardinality + catalog template"
        for connection_id in service.get("connections", {}):
            provenance[f"/services/{service_id}/connections/{connection_id}"] = "catalog dependency + routing"
        if "exposure" in service:
            provenance[f"/services/{service_id}/exposure"] = "resolved consumer reachability or explicit proxy listener"
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    metadata = {"catalog": catalog.id, "catalog_sha256": catalog.digest, "operator_format_version": str(raw["format_version"]), "resolver_version": "3", "input_sha256": sha256(canonical).hexdigest()}
    warnings = list(_validate_production_objectives(raw, result))
    if len(peers) > 1 and len({result["groups"][g]["machine"] for g in result["groups"] if g.startswith("storage")}) == 1:
        warnings.append("Multiple storage peers on one machine provide replication but not host-failure tolerance.")
    return ResolutionResult(result, provenance, tuple(warnings), metadata)


def resolve_inventory_path(path: Path) -> ResolutionResult:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"cannot read {path}: {exc}") from exc
    return resolve_inventory(raw, source_path=path)
