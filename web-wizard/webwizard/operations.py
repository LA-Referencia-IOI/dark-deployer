"""Typed operations over a draft operator inventory.

Every operation is a named change applied through ``DraftSession.apply``: it is
committed only when the whole compact inventory still resolves into a valid v3
plan. A rejected operation raises ``OperationRejected`` and leaves the previous
valid draft untouched, so the canvas never shows an impossible deployment.

Parameters are typed decisions (identifiers, counts, enumerations), never free
JSON patches.
"""

from __future__ import annotations

from typing import Any, Callable

from .session import DraftSession

IDENTIFIER_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
ROUTING_ROLES = ("blockchain_p2p", "storage_p2p", "storage_api", "application_api")
EXECUTIONS = ("local", "ssh", "auto")
NETWORK_KINDS = ("lan", "vpn")
PROFILES = ("local", "lab", "production")
OVERRIDE_SERVICES = ("explorer", "resolver")
OBJECTIVES = (
    "tolerate_validator_individual", "tolerate_validator_group", "tolerate_machine",
    "observer_copy", "rpc_redundant", "storage_durable",
)


class OperationRejected(RuntimeError):
    """The change would leave the inventory invalid, so it was not applied."""


# -- parameter helpers ------------------------------------------------

def _identifier(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise OperationRejected(f"'{key}' must be a non-empty identifier")
    if any(char not in IDENTIFIER_CHARS for char in value):
        raise OperationRejected(f"'{key}' must be a lowercase identifier (a-z, 0-9, -)")
    return value


def _text(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OperationRejected(f"'{key}' must be a non-empty string")
    return value.strip()


def _choice(params: dict, key: str, allowed: tuple[str, ...]) -> str:
    value = params.get(key)
    if value not in allowed:
        raise OperationRejected(f"'{key}' must be one of: {', '.join(allowed)}")
    return value


def _count(params: dict, key: str, *, minimum: int = 1, maximum: int = 1000) -> int:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise OperationRejected(f"'{key}' must be an integer from {minimum} to {maximum}")
    return value


def _commit(session: DraftSession, stage: str, change: Callable[[dict], None]) -> None:
    if not session.apply(change, stage=stage):
        raise OperationRejected(session.last_error or "the change was rejected")


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _known(raw: dict, section: str, key: str, what: str) -> None:
    if key not in _section(raw, section):
        raise OperationRejected(f"unknown {what} '{key}'")


# -- operations -------------------------------------------------------

def _move_group(session: DraftSession, params: dict) -> None:
    """Place a group (and everything it hosts) on another machine."""
    group = _identifier(params, "group")
    machine = _identifier(params, "machine")
    _known(session.raw, "placement", group, "group")
    _known(session.raw, "machines", machine, "machine")
    _commit(session, "placement", lambda raw: raw["placement"].__setitem__(group, machine))


def _add_machine(session: DraftSession, params: dict) -> None:
    machine_id = _identifier(params, "id")
    execution = _choice(params, "execution", EXECUTIONS)
    definition: dict[str, Any] = {"execution": execution}

    addresses = params.get("addresses")
    if addresses is not None:
        # A local machine gets one address on the first network unless told
        # otherwise, which is rarely what a multi-network deployment wants.
        if not isinstance(addresses, dict) or not addresses:
            raise OperationRejected("'addresses' must map a network to an IPv4 address")
        definition["addresses"] = {str(key): str(value) for key, value in addresses.items()}
    management = params.get("management_address")
    if management is not None:
        definition["management_address"] = _text(params, "management_address")

    if execution != "local":
        if "management_address" not in definition:
            raise OperationRejected("a non-local machine needs 'management_address'")
        if "addresses" not in definition:
            raise OperationRejected("a non-local machine needs 'addresses' {network: address}")

    if machine_id in _section(session.raw, "machines"):
        raise OperationRejected(f"machine '{machine_id}' already exists")
    _commit(session, "machines", lambda raw: raw["machines"].__setitem__(machine_id, definition))


def _remove_machine(session: DraftSession, params: dict) -> None:
    machine_id = _identifier(params, "id")
    _known(session.raw, "machines", machine_id, "machine")
    _commit(session, "machines", lambda raw: raw["machines"].pop(machine_id))


def _set_machine_address(session: DraftSession, params: dict) -> None:
    machine_id = _identifier(params, "id")
    network = _identifier(params, "network")
    address = _text(params, "address")
    _known(session.raw, "machines", machine_id, "machine")
    _known(session.raw, "networks", network, "network")

    def change(raw: dict) -> None:
        machine = raw["machines"][machine_id]
        if machine.get("execution") == "local":
            raise OperationRejected(f"machine '{machine_id}' is local and needs no addresses")
        machine.setdefault("addresses", {})[network] = address

    _commit(session, "machines", change)


def _add_network(session: DraftSession, params: dict) -> None:
    network_id = _identifier(params, "id")
    kind = _choice(params, "kind", NETWORK_KINDS)
    cidr = _text(params, "cidr")
    if network_id in _section(session.raw, "networks"):
        raise OperationRejected(f"network '{network_id}' already exists")
    _commit(
        session, "networks",
        lambda raw: raw["networks"].__setitem__(network_id, {"kind": kind, "cidr": cidr}),
    )


def _remove_network(session: DraftSession, params: dict) -> None:
    network_id = _identifier(params, "id")
    _known(session.raw, "networks", network_id, "network")
    _commit(session, "networks", lambda raw: raw["networks"].pop(network_id))


def _routes_of(raw: dict) -> list:
    value = raw.get("routes")
    return value if isinstance(value, list) else []


def _add_route(session: DraftSession, params: dict) -> None:
    """Declare that a machine on one network can reach another network."""
    source = _identifier(params, "from")
    target = _identifier(params, "to")
    _known(session.raw, "networks", source, "network")
    _known(session.raw, "networks", target, "network")
    if source == target:
        raise OperationRejected("a route must connect two different networks")
    via = params.get("via")
    via = _identifier(params, "via") if isinstance(via, str) and via.strip() else None
    for entry in _routes_of(session.raw):
        if isinstance(entry, dict) and entry.get("from") == source and entry.get("to") == target:
            raise OperationRejected(f"a route {source} -> {target} is already declared")

    def change(raw: dict) -> None:
        entry: dict[str, Any] = {"from": source, "to": target}
        if via:
            entry["via"] = via
        raw.setdefault("routes", []).append(entry)

    _commit(session, "routes", change)


def _remove_route(session: DraftSession, params: dict) -> None:
    source = _identifier(params, "from")
    target = _identifier(params, "to")
    present = any(
        isinstance(entry, dict) and entry.get("from") == source and entry.get("to") == target
        for entry in _routes_of(session.raw)
    )
    if not present:
        raise OperationRejected(f"no route {source} -> {target} is declared")

    def change(raw: dict) -> None:
        raw["routes"] = [
            entry for entry in _routes_of(raw)
            if not (isinstance(entry, dict) and entry.get("from") == source and entry.get("to") == target)
        ]
        if not raw["routes"]:
            raw.pop("routes", None)

    _commit(session, "routes", change)


def _blockchain(raw: dict) -> dict:
    return raw.setdefault("blockchain", {})


def _set_validator_count(session: DraftSession, params: dict) -> None:
    group = _identifier(params, "group")
    count = _count(params, "count")
    _known(_section(session.raw, "blockchain"), "validator_groups", group, "validator group")
    _commit(
        session, "blockchain",
        lambda raw: _blockchain(raw)["validator_groups"][group].__setitem__("validator_count", count),
    )


def _set_observer_count(session: DraftSession, params: dict) -> None:
    group = _identifier(params, "group")
    count = _count(params, "count")
    _known(_section(session.raw, "blockchain"), "observer_groups", group, "observer group")
    _commit(
        session, "blockchain",
        lambda raw: _blockchain(raw)["observer_groups"][group].__setitem__("observer_count", count),
    )


def _add_group(session: DraftSession, params: dict, *, section: str, count_field: str, stage: str) -> None:
    group = _identifier(params, "group")
    machine = _identifier(params, "machine")
    count = _count(params, "count")
    if group in _section(_section(session.raw, "blockchain"), section):
        raise OperationRejected(f"group '{group}' already exists")
    _known(session.raw, "machines", machine, "machine")
    if group in _section(session.raw, "placement"):
        raise OperationRejected(f"placement already declares '{group}'")

    def change(raw: dict) -> None:
        _blockchain(raw).setdefault(section, {})[group] = {count_field: count}
        raw.setdefault("placement", {})[group] = machine

    _commit(session, stage, change)


def _add_validator_group(session: DraftSession, params: dict) -> None:
    _add_group(session, params, section="validator_groups", count_field="validator_count", stage="blockchain")


def _add_observer_group(session: DraftSession, params: dict) -> None:
    _add_group(session, params, section="observer_groups", count_field="observer_count", stage="blockchain")


def _remove_group(session: DraftSession, params: dict, *, section: str, stage: str) -> None:
    group = _identifier(params, "group")
    _known(_section(session.raw, "blockchain"), section, group, "group")

    def change(raw: dict) -> None:
        raw["blockchain"][section].pop(group, None)
        raw.get("placement", {}).pop(group, None)

    _commit(session, stage, change)


def _remove_validator_group(session: DraftSession, params: dict) -> None:
    _remove_group(session, params, section="validator_groups", stage="blockchain")


def _remove_observer_group(session: DraftSession, params: dict) -> None:
    _remove_group(session, params, section="observer_groups", stage="blockchain")


def _add_rpc_node(session: DraftSession, params: dict) -> None:
    node = _identifier(params, "id")
    group = _identifier(params, "group")
    _known(session.raw, "placement", group, "group")
    nodes = _section(_section(session.raw, "blockchain"), "rpc").get("nodes") or {}
    if node in nodes:
        raise OperationRejected(f"RPC node '{node}' already exists")
    _commit(
        session, "blockchain",
        lambda raw: raw["blockchain"]["rpc"].setdefault("nodes", {}).__setitem__(node, {"group": group}),
    )


def _remove_rpc_node(session: DraftSession, params: dict) -> None:
    node = _identifier(params, "id")
    rpc = _section(_section(session.raw, "blockchain"), "rpc")
    if node not in (rpc.get("nodes") or {}):
        raise OperationRejected(f"unknown RPC node '{node}'")

    def change(raw: dict) -> None:
        raw["blockchain"]["rpc"]["nodes"].pop(node)
        if raw["blockchain"]["rpc"].get("primary") == node:
            remaining = list(raw["blockchain"]["rpc"]["nodes"])
            if not remaining:
                raise OperationRejected("the blockchain needs at least one RPC node")
            raw["blockchain"]["rpc"]["primary"] = remaining[0]
        bindings = raw["blockchain"]["rpc"].get("bindings") or {}
        for consumer in [key for key, value in bindings.items() if value == node]:
            bindings.pop(consumer)

    _commit(session, "blockchain", change)


def _set_primary_rpc(session: DraftSession, params: dict) -> None:
    node = _identifier(params, "id")
    nodes = _section(_section(session.raw, "blockchain"), "rpc").get("nodes") or {}
    if node not in nodes:
        raise OperationRejected(f"unknown RPC node '{node}'")
    _commit(session, "blockchain", lambda raw: raw["blockchain"]["rpc"].__setitem__("primary", node))


def _set_binding(session: DraftSession, params: dict) -> None:
    """Point one consumer's RPC connection at an RPC node or an observer."""
    consumer = _identifier(params, "consumer")
    provider = params.get("provider")
    if provider is not None and not isinstance(provider, str):
        raise OperationRejected("'provider' must be a string identifier, or empty to clear")
    blockchain = _section(session.raw, "blockchain")
    rpc = _section(blockchain, "rpc")
    observers = _section(blockchain, "observer_groups") or {}
    observer_count = sum(
        int((definition or {}).get("observer_count") or 0)
        for definition in observers.values()
        if isinstance(definition, dict)
    )
    available = set(rpc.get("nodes") or {}) | {
        f"observer{index:02d}" for index in range(1, observer_count + 1)
    }

    def change(raw: dict) -> None:
        bindings = raw["blockchain"]["rpc"].setdefault("bindings", {})
        if not provider:
            bindings.pop(consumer, None)
            if not bindings:
                raw["blockchain"]["rpc"].pop("bindings", None)
            return
        if provider not in available:
            raise OperationRejected(
                f"'{provider}' is not an RPC node or observer; available: "
                + ", ".join(sorted(available))
            )
        bindings[consumer] = provider

    _commit(session, "blockchain", change)


def _set_routing(session: DraftSession, params: dict) -> None:
    role = _choice(params, "role", ROUTING_ROLES)
    network = _identifier(params, "network")
    _known(session.raw, "networks", network, "network")
    _commit(session, "routing", lambda raw: raw.setdefault("routing", {}).__setitem__(role, network))


def _add_storage_peer(session: DraftSession, params: dict) -> None:
    peer = _identifier(params, "id")
    group = _identifier(params, "group")
    _known(session.raw, "placement", group, "group")
    peers = _section(_section(session.raw, "storage"), "peers")
    if peer in peers:
        raise OperationRejected(f"storage peer '{peer}' already exists")
    _commit(
        session, "storage",
        lambda raw: raw["storage"].setdefault("peers", {}).__setitem__(peer, {"group": group}),
    )


def _remove_storage_peer(session: DraftSession, params: dict) -> None:
    peer = _identifier(params, "id")
    _known(_section(session.raw, "storage"), "peers", peer, "storage peer")
    _commit(session, "storage", lambda raw: raw["storage"]["peers"].pop(peer))


def _set_replication(session: DraftSession, params: dict) -> None:
    publish = _count(params, "publish_after_replicas")
    target = _count(params, "target_replicas")

    def change(raw: dict) -> None:
        replication = raw["storage"].setdefault("replication", {})
        replication["publish_after_replicas"] = publish
        replication["target_replicas"] = target

    _commit(session, "storage", change)


def _set_override(session: DraftSession, params: dict) -> None:
    """Move explorer or resolver out of the apps group on its own."""
    service = _choice(params, "service", OVERRIDE_SERVICES)
    group = _identifier(params, "group")
    _known(session.raw, "placement", group, "group")
    _commit(
        session, "overrides",
        lambda raw: raw.setdefault("overrides", {}).setdefault(service, {}).__setitem__("group", group),
    )


def _set_profile(session: DraftSession, params: dict) -> None:
    profile = _choice(params, "profile", PROFILES)
    _commit(session, "profile", lambda raw: raw.__setitem__("profile", profile))


def _set_deployment_label(session: DraftSession, params: dict) -> None:
    label = _text(params, "label")
    _commit(session, "deployment", lambda raw: raw["deployment"].__setitem__("label", label))


def _set_acknowledgements(session: DraftSession, params: dict) -> None:
    values = params.get("objectives")
    if not isinstance(values, list) or any(item not in OBJECTIVES for item in values):
        raise OperationRejected("'objectives' must be a list of availability objective names")

    def change(raw: dict) -> None:
        policy = raw.setdefault("availability", {"objectives": []})
        if values:
            policy["acknowledgements"] = list(dict.fromkeys(values))
        else:
            policy.pop("acknowledgements", None)

    _commit(session, "availability", change)


def _optional_port(params: dict, key: str) -> int | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise OperationRejected(f"'{key}' must be a TCP/UDP port, or empty")
    return value


def _optional_address(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    return _text(params, key)


CATALOGUE_SINGLETONS = frozenset({
    "store-api", "minter-api", "minter-postgres", "minter-migrate", "admin-api",
    "resolver-api", "dashboard", "dashboard-mysql", "dashboard-redis",
    "dashboard-migrate", "explorer", "contracts-deploy", "rpc-probe",
    "metadata-worker", "replication-worker", "chain-worker",
})


def _service_ids(raw: dict) -> set[str]:
    """Every service id the compact document can produce."""
    blockchain = _section(raw, "blockchain")
    ids = set(_section(blockchain, "rpc").get("nodes") or {})
    for section, prefix, field in (
        ("validator_groups", "validator", "validator_count"),
        ("observer_groups", "observer", "observer_count"),
    ):
        total = sum(
            int((definition or {}).get(field) or 0)
            for definition in (_section(blockchain, section) or {}).values()
            if isinstance(definition, dict)
        )
        ids |= {f"{prefix}{index:02d}" for index in range(1, total + 1)}
    for peer in (_section(raw, "storage").get("peers") or {}):
        suffix = str(peer).removeprefix("storage-")
        ids |= {f"ipfs-storage-{suffix}", f"cluster-storage-{suffix}"}
    ids |= set(_section(raw, "proxies") or {})
    return ids | set(CATALOGUE_SINGLETONS)


def _set_advertise(session: DraftSession, params: dict) -> None:
    """Override the address or port peers should dial (NAT and similar)."""
    service = _identifier(params, "service")
    address = _optional_address(params, "advertise_address")
    port = _optional_port(params, "advertise_port")
    p2p_port = _optional_port(params, "p2p_advertise_port")
    if service not in _service_ids(session.raw):
        raise OperationRejected(f"unknown service '{service}'")

    def change(raw: dict) -> None:
        services = raw.setdefault("networking", {}).setdefault("services", {})
        entry: dict[str, Any] = {}
        if address:
            entry["advertise_address"] = address
        if port is not None:
            entry["advertise_port"] = port
        if p2p_port is not None:
            entry["p2p_advertise_port"] = p2p_port
        if entry:
            services[service] = entry
        else:
            services.pop(service, None)
        if not services:
            raw.pop("networking", None)

    _commit(session, "networking", change)


# -- the web edge -----------------------------------------------------

ROUTE_TARGETS = ("dashboard", "explorer", "minter-api", "resolver-api")


def _proxy(raw: dict, proxy_id: str) -> dict:
    proxies = _section(raw, "proxies")
    if proxy_id not in proxies:
        raise OperationRejected(f"unknown proxy '{proxy_id}'")
    return proxies[proxy_id]


def _listener(params: dict, raw: dict) -> dict:
    bind = _choice(params, "bind", ("loopback", "private", "public"))
    port = _count(params, "port", maximum=65535)
    listener: dict[str, Any] = {"bind": bind, "port": port}
    if bind == "private":
        network = _identifier(params, "network")
        _known(raw, "networks", network, "network")
        listener["network"] = network
    return listener


def _group_is_apps(raw: dict, group: str) -> bool:
    """Whether a placement group is an application group rather than a chain one."""
    blockchain = _section(raw, "blockchain")
    if group in (_section(blockchain, "validator_groups") or {}):
        return False
    if group in (_section(blockchain, "observer_groups") or {}):
        return False
    for definition in (_section(_section(raw, "storage"), "peers") or {}).values():
        if isinstance(definition, dict) and definition.get("group") == group:
            return False
    return True


def _add_proxy(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    group = _identifier(params, "group")
    _known(session.raw, "placement", group, "group")
    if not _group_is_apps(session.raw, group):
        # The resolver derives a group's kind before hosting proxies, so a proxy
        # dropped into a validators/observers/storage group makes it invalid.
        raise OperationRejected(
            f"a proxy can only live in an application group; '{group}' hosts blockchain or storage"
        )
    if proxy_id in _section(session.raw, "proxies"):
        raise OperationRejected(f"proxy '{proxy_id}' already exists")

    service = _text(params, "service")
    if service not in ROUTE_TARGETS:
        raise OperationRejected("a proxy route must target: " + ", ".join(ROUTE_TARGETS))
    route_id = _identifier(params, "route_id")
    path = _text(params, "path")
    upstream = _text(params, "upstream_path")
    host = params.get("host")
    host = host.strip() if isinstance(host, str) else ""
    origin = _text(params, "public_origin")
    tls_mode = _choice(params, "tls_mode", ("http", "external"))
    listener = _listener(params, session.raw)

    definition = {
        "group": group,
        "listener": listener,
        "tls": {"mode": tls_mode},
        "sites": [{
            "host": host,
            "public_origin": origin,
            "routes": [{"id": route_id, "path": path, "service": service, "upstream_path": upstream}],
        }],
    }
    _commit(session, "proxies", lambda raw: raw.setdefault("proxies", {}).__setitem__(proxy_id, definition))


def _remove_proxy(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    _proxy(session.raw, proxy_id)

    def change(raw: dict) -> None:
        raw["proxies"].pop(proxy_id)
        if not raw["proxies"]:
            raise OperationRejected("the deployment needs at least one public proxy")

    _commit(session, "proxies", change)


def _set_proxy_listener(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    _proxy(session.raw, proxy_id)
    listener = _listener(params, session.raw)
    _commit(session, "proxies", lambda raw: raw["proxies"][proxy_id].__setitem__("listener", listener))


def _set_proxy_tls(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    _proxy(session.raw, proxy_id)
    mode = _choice(params, "mode", ("http", "external", "direct"))
    tls: dict[str, Any] = {"mode": mode}
    if mode == "direct":
        # Direct TLS terminates with a certificate from the secret store.
        tls["certificate_secret"] = _identifier(params, "certificate_secret")
        tls["key_secret"] = _identifier(params, "key_secret")
    _commit(session, "proxies", lambda raw: raw["proxies"][proxy_id].__setitem__("tls", tls))


def _proxy_site(raw: dict, proxy_id: str, params: dict) -> tuple[dict, int]:
    definition = _proxy(raw, proxy_id)
    sites = definition.get("sites") or []
    index = params.get("site_index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(sites):
        raise OperationRejected(f"proxy '{proxy_id}' has no site {params.get('site_index')!r}")
    return definition, index


def _set_proxy_site(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    _proxy_site(session.raw, proxy_id, params)
    host = params.get("host")
    host = host.strip() if isinstance(host, str) else ""
    origin = _text(params, "public_origin")
    index = params.get("site_index", 0)

    def change(raw: dict) -> None:
        raw["proxies"][proxy_id]["sites"][index]["host"] = host
        raw["proxies"][proxy_id]["sites"][index]["public_origin"] = origin

    _commit(session, "proxies", change)


def _add_proxy_route(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    _proxy_site(session.raw, proxy_id, params)
    index = params.get("site_index", 0)
    route_id = _identifier(params, "route_id")
    path = _text(params, "path")
    upstream = _text(params, "upstream_path")
    service = _text(params, "service")
    if service not in ROUTE_TARGETS:
        raise OperationRejected("a proxy route must target: " + ", ".join(ROUTE_TARGETS))
    existing = {route.get("id") for route in _section(session.raw, "proxies")[proxy_id]["sites"][index].get("routes", [])}
    if route_id in existing:
        raise OperationRejected(f"proxy '{proxy_id}' already has a route '{route_id}'")

    def change(raw: dict) -> None:
        raw["proxies"][proxy_id]["sites"][index].setdefault("routes", []).append({
            "id": route_id, "path": path, "service": service, "upstream_path": upstream,
        })

    _commit(session, "proxies", change)


def _remove_proxy_route(session: DraftSession, params: dict) -> None:
    proxy_id = _identifier(params, "id")
    definition, index = _proxy_site(session.raw, proxy_id, params)
    route_id = _identifier(params, "route_id")
    routes = definition["sites"][index].get("routes") or []
    if route_id not in {route.get("id") for route in routes}:
        raise OperationRejected(f"proxy '{proxy_id}' has no route '{route_id}'")

    def change(raw: dict) -> None:
        site = raw["proxies"][proxy_id]["sites"][index]
        site["routes"] = [route for route in site["routes"] if route.get("id") != route_id]
        if not site["routes"]:
            raise OperationRejected("a proxy site needs at least one route")

    _commit(session, "proxies", change)


_HANDLERS: dict[str, Callable[[DraftSession, dict], None]] = {
    "add_route": _add_route,
    "remove_route": _remove_route,
    "set_advertise": _set_advertise,
    "add_proxy": _add_proxy,
    "remove_proxy": _remove_proxy,
    "set_proxy_listener": _set_proxy_listener,
    "set_proxy_tls": _set_proxy_tls,
    "set_proxy_site": _set_proxy_site,
    "add_proxy_route": _add_proxy_route,
    "remove_proxy_route": _remove_proxy_route,
    "move_group": _move_group,
    "add_machine": _add_machine,
    "remove_machine": _remove_machine,
    "set_machine_address": _set_machine_address,
    "add_network": _add_network,
    "remove_network": _remove_network,
    "set_validator_count": _set_validator_count,
    "set_observer_count": _set_observer_count,
    "add_validator_group": _add_validator_group,
    "add_observer_group": _add_observer_group,
    "remove_validator_group": _remove_validator_group,
    "remove_observer_group": _remove_observer_group,
    "add_rpc_node": _add_rpc_node,
    "remove_rpc_node": _remove_rpc_node,
    "set_primary_rpc": _set_primary_rpc,
    "set_binding": _set_binding,
    "set_routing": _set_routing,
    "add_storage_peer": _add_storage_peer,
    "remove_storage_peer": _remove_storage_peer,
    "set_replication": _set_replication,
    "set_override": _set_override,
    "set_profile": _set_profile,
    "set_deployment_label": _set_deployment_label,
    "set_acknowledgements": _set_acknowledgements,
}


def operations() -> list[str]:
    """The names of every supported operation."""
    return sorted(_HANDLERS)


def perform(session: DraftSession, kind: str, params: dict | None = None) -> None:
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise OperationRejected(f"unknown operation '{kind}'")
    handler(session, params if isinstance(params, dict) else {})
