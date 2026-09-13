"""Render deterministic public deployment artifacts from inventory v3."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from .model import DeploymentPlan, Machine, ServiceInstance
from .network import endpoint_for, has_remote_peer, p2p_endpoint, internal_port
from .services import compose_document
from .inventory_resolver import resolve_inventory_path


def _json(value): return json.dumps(value, indent=2, sort_keys=True) + "\n"
def _yaml(value): return yaml.safe_dump(value, sort_keys=False, default_flow_style=False)
def _sha256(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def _nginx_proxy_config(plan: DeploymentPlan, proxy: ServiceInstance) -> str:
    """Render one or more virtual hosts from the validated proxy contract."""
    tls = proxy.configuration.get("tls", {"mode": "http"})
    mode = tls.get("mode", "http")
    port = internal_port(proxy)
    servers: list[str] = []
    for site in proxy.configuration["sites"]:
        host = site.get("host", "")
        lines = ["server {", f"  listen {port}" + (" ssl;" if mode == "direct" else ";"), f"  server_name {host or '_'};"]
        if mode == "direct":
            lines.extend((
                f"  ssl_certificate /run/secrets/{tls['certificate_secret']};",
                f"  ssl_certificate_key /run/secrets/{tls['key_secret']};",
            ))
        # Longest paths first makes the intent evident and protects a future
        # switch to regex locations; nginx itself also prefers the longer
        # prefix over the catch-all resolver route.
        for route in sorted(site["routes"], key=lambda item: len(item["path"]), reverse=True):
            upstream = _url(plan, proxy, proxy.connections[route["connection"]]["service"], proxy.connections[route["connection"]]) + route["upstream_path"]
            location = f"location = {route['path']}" if not route["path"].endswith("/") else f"location {route['path']}"
            forwarded_prefix = route["path"].rstrip("/") or "/"
            redirect_rules = ["    proxy_redirect off;"]
            cookie_rules: list[str] = []
            if forwarded_prefix != "/":
                # Applications run at their internal root and can therefore
                # send Location: /login (or an absolute internal URL). Keep
                # those redirects within the public route without requiring
                # every backend framework to understand the gateway layout.
                prefix_pattern = re.escape(forwarded_prefix.lstrip("/"))
                redirect_rules = [
                    f"    proxy_redirect ~^https?://[^/]+/(?!{prefix_pattern}(?:/|$))(.*)$ {forwarded_prefix}/$1;",
                    f"    proxy_redirect ~^/(?!{prefix_pattern}(?:/|$))(.*)$ {forwarded_prefix}/$1;",
                ]
                # Cookie isolation belongs to the mount contract, not to a
                # particular framework. Backends receive their requests at
                # /, so their default cookie Path must be made public here.
                cookie_rules = [f"    proxy_cookie_path / {forwarded_prefix}/;"]
            lines.extend((
                f"  {location} {{",
                f"    proxy_pass {upstream};",
                "    proxy_http_version 1.1;",
                "    proxy_set_header Host $host;",
                "    proxy_set_header X-Real-IP $remote_addr;",
                "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "    proxy_set_header X-Forwarded-Host $host;",
                "    proxy_set_header X-Forwarded-Port $server_port;",
                "    proxy_set_header X-Forwarded-Proto $scheme;",
                f"    proxy_set_header X-Forwarded-Prefix {forwarded_prefix};",
                *redirect_rules,
                *cookie_rules,
                "  }",
            ))
        lines.append("}")
        servers.append("\n".join(lines))
    return "\n\n".join(servers) + "\n"


def _url(plan: DeploymentPlan, consumer: ServiceInstance, provider_id: str, connection: dict[str, str] | None = None) -> str:
    provider = plan.service(provider_id)
    if connection is None and provider.machine_id != consumer.machine_id:
        network = provider.exposure.get("network") if provider.exposure else None
        connection = {"service": provider_id, **({"network": network} if network else {})}
    return endpoint_for(provider, plan.machine(provider.machine_id), plan.machine(consumer.machine_id), connection or {"service": provider_id}).url


def _dashboard_public_url(plan: DeploymentPlan, dashboard: ServiceInstance) -> str:
    """Return the declared gateway URL used for Laravel absolute URLs."""
    dashboard_id = dashboard.id
    if dashboard.type == "dashboard-migrate":
        dashboard_id = next(item.id for item in plan.services if item.type == "dashboard")
    for proxy in (item for item in plan.services if item.type == "edge-proxy"):
        for site in proxy.configuration.get("sites", []):
            for route in site.get("routes", []):
                connection = proxy.connections.get(route.get("connection", ""), {})
                if connection.get("service") != dashboard_id:
                    continue
                origin = site.get("public_origin")
                if origin:
                    return origin.rstrip("/") + route["path"].rstrip("/")
    # An empty-host Lima listener intentionally follows the browser Host
    # header.  Relative links still work through X-Forwarded-Prefix.
    return "http://localhost:8081"


def _dashboard_public_path(plan: DeploymentPlan, dashboard: ServiceInstance) -> str:
    """Return the upstream cookie path; the edge proxy adds the public mount."""
    # The dashboard is served behind an edge-proxy location such as /admin/.
    # nginx applies proxy_cookie_path once to the upstream root cookie path.
    # Returning /admin here would therefore produce /admin/admin in Set-Cookie.
    return "/"


def _env(plan: DeploymentPlan, service: ServiceInstance) -> dict[str, str]:
    connection = lambda name: _url(plan, service, service.connections[name]["service"], service.connections[name])
    host = lambda name: connection(name).split("//", 1)[1].rsplit(":", 1)[0]
    values = {"DARK_DEPLOYMENT_ID": plan.deployment_id}
    if service.type in {"minter-api", "minter-worker"}:
        replication = plan.raw["settings"]["minter"]["replication"]
        metadata = plan.raw["settings"]["minter"]["metadata"]
        chain = plan.raw["settings"]["minter"]["chain"]
        values |= {
            "DATABASE_URL": f"postgresql://dark:dark@{host('database')}:5432/minter",
            "METADATA_STORE_API_URL": connection("store_api"),
            "DARK_RPC_URL": connection("rpc"),
            "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"]),
            "METADATA_STORAGE_PATH": "/app/metadata_storage",
            "MINTER_SHOULDER": plan.raw["settings"]["minter"]["shoulder"],
            "METADATA_WORKER_PAGE_SIZE": str(metadata["page_size"]),
            "METADATA_WORKER_CONCURRENCY": str(metadata["concurrency"]),
            "METADATA_WORKER_MIN_CONCURRENCY": str(metadata["min_concurrency"]),
            "REPLICATION_WORKER_ENABLED": str(replication["enabled"]).lower(),
            "REPLICATION_WORKER_PAGE_SIZE": str(replication["page_size"]),
            "REPLICATION_STATUS_BATCH_SIZE": str(replication["status_batch_size"]),
            "REPLICATION_PROMOTION_BATCH_SIZE": str(replication["promotion_batch_size"]),
            "REPLICATION_IDLE_SLEEP_SECONDS": str(replication["idle_sleep_seconds"]),
            "CHAIN_WORKER_PAGE_SIZE": str(chain["page_size"]),
            "CHAIN_WORKER_RPC_BATCH_SIZE": str(chain["rpc_batch_size"]),
        }
    elif service.type == "minter-migrate":
        values |= {"DATABASE_URL": f"postgresql://dark:dark@{host('database')}:5432/minter", "METADATA_STORAGE_PATH": "/app/metadata_storage"}
    elif service.type == "store-api":
        values |= {"STORE_API_HOST": "0.0.0.0", "STORE_API_PORT": "8003", "STORAGE_BACKEND": "ipfs_cluster", "STORAGE_ENDPOINTS_FILE": "/config/storage-endpoints.json"}
    elif service.type == "admin-api":
        values |= {"ADMIN_API_HOST": "0.0.0.0", "ADMIN_API_PORT": "8000", "DARK_RPC_URL": connection("rpc"), "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"])}
    elif service.type == "resolver-api":
        values |= {"RESOLVER_API_HOST": "0.0.0.0", "RESOLVER_API_PORT": "8002", "DARK_RPC_URL": connection("rpc"), "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"]), "METADATA_STORE_API_URL": connection("store_api")}
    elif service.type in {"dashboard", "dashboard-migrate"}:
        rpc = next(item for item in plan.services if item.type == "besu-rpc")
        kubo = next(item for item in plan.services if item.type == "ipfs-kubo")
        cluster = next(item for item in plan.services if item.type == "ipfs-cluster")
        rpc_url = _url(plan, service, rpc.id)
        values |= {
            "APP_ENV": "production", "APP_DEBUG": "false", "APP_URL": _dashboard_public_url(plan, service),
            "ASSET_URL": _dashboard_public_url(plan, service), "SESSION_PATH": _dashboard_public_path(plan, service), "FORCE_HTTPS": "false",
            "DB_CONNECTION": "mysql", "DB_HOST": host("database"), "DB_DATABASE": "dark", "DB_USERNAME": "dark", "REDIS_HOST": host("redis"),
            "ADMIN_API_BASE_URL": connection("admin_api"), "MINTER_BASE_URL": connection("minter_api"), "RESOLVER_BASE_URL": connection("resolver_api"), "STORE_API_BASE_URL": connection("store_api"),
            "WORKER_STATUS_URL": connection("minter_api") + "/api/v1/worker/status",
            # These names are consumed by the dashboard's API Health Check
            # and landing page. Keep the legacy contract while deriving every
            # endpoint from the inventory graph.
            "BLOCK_NUMBER": rpc_url,
            "LIVENESS": rpc_url + "/liveness",
            "IPFS_API_BASE_URL": _url(plan, service, kubo.id),
            "IPFS_CLUSTER_API_URL": _url(plan, service, cluster.id),
        }
    elif service.type == "minter-postgres":
        values |= {"POSTGRES_USER": "dark", "POSTGRES_DB": "minter", "POSTGRES_PASSWORD": "dark"}
    elif service.type == "dashboard-mysql":
        values |= {"MYSQL_DATABASE": "dark", "MYSQL_USER": "dark", "MYSQL_PASSWORD": "dark", "MYSQL_ROOT_PASSWORD": "dark"}
    elif service.type == "ipfs-kubo":
        peers = [item for item in plan.services if item.type == "ipfs-kubo" and item.id != service.id]
        network = plan.raw["infrastructure"]["ipfs"]["network"]
        bootstrap = []
        for peer in peers:
            if peer.machine_id == service.machine_id:
                # Same Docker network: address the peer by its service name.
                bootstrap.append(f"/dns4/{peer.id}/tcp/{plan.raw['infrastructure']['ipfs']['api_port']}")
            else:
                machine = plan.machine(peer.machine_id)
                address, port = p2p_endpoint(machine, peer, network, plan.raw['infrastructure']['ipfs']['swarm_port'])
                api_port = int((peer.exposure or {}).get("advertise_port") or plan.raw['infrastructure']['ipfs']['api_port'])
                bootstrap.append(f"/ip4/{address}/tcp/{api_port}@{port}")
        kubo_services = sorted((item for item in plan.services if item.type == "ipfs-kubo"), key=lambda item: item.id)
        is_seed = kubo_services and service.id == kubo_services[0].id
        if not has_remote_peer(plan, {"ipfs-kubo"}):
            announce = f"/dns4/{service.id}/tcp/{plan.raw['infrastructure']['ipfs']['swarm_port']}"
        else:
            address, port = p2p_endpoint(plan.machine(service.machine_id), service, network, plan.raw['infrastructure']['ipfs']['swarm_port'])
            announce = f"/ip4/{address}/tcp/{port}"
        values |= {"NODE_NAME": str(service.configuration["peer_name"]), "IPFS_SWARM_KEY_FILE": "/run/secrets/ipfs-swarm-key", "IPFS_BOOTSTRAP_ENDPOINTS": " ".join(bootstrap), "IPFS_BOOTSTRAP_P2P_PORT": str(plan.raw["infrastructure"]["ipfs"]["swarm_port"]), "IPFS_ANNOUNCE_MULTIADDRESS": announce, "CLUSTER_SEED": "true" if is_seed else "false"}
    elif service.type == "ipfs-cluster":
        peers = [item for item in plan.services if item.type == "ipfs-cluster" and item.id != service.id]
        network = plan.raw["infrastructure"]["cluster"]["network"]
        bootstrap = []
        for peer in peers:
            if peer.machine_id == service.machine_id:
                bootstrap.append(f"/dns4/{peer.id}/tcp/{plan.raw['infrastructure']['cluster']['api_port']}")
            else:
                machine = plan.machine(peer.machine_id)
                address, port = p2p_endpoint(machine, peer, network, plan.raw['infrastructure']['cluster']['p2p_port'])
                api_port = int((peer.exposure or {}).get("advertise_port") or plan.raw['infrastructure']['cluster']['api_port'])
                bootstrap.append(f"/ip4/{address}/tcp/{api_port}@{port}")
        cluster_services = sorted((item for item in plan.services if item.type == "ipfs-cluster"), key=lambda item: item.id)
        is_seed = cluster_services and service.id == cluster_services[0].id
        if not has_remote_peer(plan, {"ipfs-cluster"}):
            announce = f"/dns4/{service.id}/tcp/{plan.raw['infrastructure']['cluster']['p2p_port']}"
        else:
            address, port = p2p_endpoint(plan.machine(service.machine_id), service, network, plan.raw['infrastructure']['cluster']['p2p_port'])
            announce = f"/ip4/{address}/tcp/{port}"
        values |= {"CLUSTER_PEERNAME": str(service.configuration["peer_name"]), "IPFS_DARK_NET_ALIAS": service.connections["kubo"]["service"], "CLUSTER_SECRET_FILE": "/run/secrets/ipfs-cluster-secret", "CLUSTER_BOOTSTRAP_ENDPOINTS": " ".join(bootstrap), "CLUSTER_BOOTSTRAP_P2P_PORT": str(plan.raw["infrastructure"]["cluster"]["p2p_port"]), "CLUSTER_ANNOUNCE_MULTIADDRESS": announce, "CLUSTER_SEED": "true" if is_seed else "false"}
    elif service.type == "explorer":
        values |= {"RPC_HTTP_URL": connection("rpc")}
    elif service.type == "contracts-deploy":
        values |= {"DARK_RPC_URL": connection("rpc"), "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"]), "CONTRACT_ARTIFACTS_DIR": "/contracts", "CONTRACT_HANDOFF_FILE": "/runtime/contracts.json", "CONTRACT_RUNTIME_ENV_FILE": "/runtime/contracts.env", "CONTRACT_SIGNER_FILE": "/run/secrets/contract-signer"}
    return values


def render_plan(plan: DeploymentPlan, output: Path) -> Path:
    if output.exists() and any(output.iterdir()): raise ValueError(f"render output must be empty: {output}")
    output.mkdir(parents=True); shared = output / "shared"; shared.mkdir()
    # `inventory_path` may be an operator inventory.  The bundle contract is
    # always the fully resolved v3 document consumed by renderer and runner.
    (shared / "deployment-topology.json").write_text(_json(plan.raw))
    try:
        original = json.loads(plan.inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        original = {}
    if original.get("format") == "dark-operator-inventory":
        resolution = resolve_inventory_path(plan.inventory_path)
        resolution_path = shared / "inventory-resolution.json"
        resolution_path.write_text(_json({"metadata": resolution.metadata, "provenance": resolution.provenance, "warnings": resolution.warnings}))
    (shared / "plan.json").write_text(_json({"deployment_id": plan.deployment_id, "groups": [item.__dict__ for item in plan.groups], "steps": [item.__dict__ for item in plan.steps]}))
    root = output / "machines"; root.mkdir(); manifest = {}
    if (shared / "inventory-resolution.json").exists():
        manifest["shared/inventory-resolution.json"] = _sha256(shared / "inventory-resolution.json")
    for machine in plan.machines:
        assigned = tuple(service for service in plan.services if service.machine_id == machine.id)
        target = root / machine.id; target.mkdir(); (target / "env").mkdir(); (target / "config").mkdir()
        nodes = []
        store = next(item for item in plan.services if item.type == "store-api")
        for cluster_connection in store.connections.values():
            cluster = plan.service(cluster_connection["service"])
            kubo = plan.service(cluster.connections["kubo"]["service"])
            nodes.append({"id": cluster.configuration["peer_name"], "ipfs_api_url": _url(plan, store, kubo.id, cluster_connection), "cluster_api_url": _url(plan, store, cluster.id, cluster_connection)})
        storage_payload = _json({"version": 1, "nodes": nodes})

        # Keep the aggregate machine bundle for compatibility, and also emit
        # one self-contained Compose project per logical server group. Group
        # projects share the machine bridge, so local Docker can simulate the
        # five-server topology while remote hosts retain one group per host.
        targets = [(target, assigned)]
        for group in (item for item in plan.groups if item.machine_id == machine.id):
            group_dir = target / "groups" / group.id
            targets.append((group_dir, tuple(plan.service(service_id) for service_id in group.service_ids)))
        for target_dir, target_services in targets:
            target_dir.mkdir(parents=True, exist_ok=True); (target_dir / "env").mkdir(exist_ok=True); (target_dir / "config").mkdir(exist_ok=True)
            config = {"machine": {**machine.__dict__, "ssh": machine.ssh.__dict__}, "groups": [group.__dict__ for group in plan.groups if group.machine_id == machine.id], "services": [service.__dict__ for service in target_services], "components": plan.raw["components"], "secrets": plan.raw["secrets"]}
            config_path = target_dir / "machine.json"; config_path.write_text(_json(config)); manifest[str(config_path.relative_to(output))] = _sha256(config_path)
            group_id = target_dir.name if target_dir.parent.name == "groups" else None
            compose_path = target_dir / "compose.yaml"; compose_path.write_text(_yaml(compose_document(plan, machine, target_services, group_id))); manifest[str(compose_path.relative_to(output))] = _sha256(compose_path)
            (target_dir / "config" / "storage-endpoints.json").write_text(storage_payload)
            storage_path = target_dir / "config" / "storage-endpoints.json"; manifest[str(storage_path.relative_to(output))] = _sha256(storage_path)
            for service in target_services:
                path = target_dir / "env" / f"{service.id}.env"; path.write_text("".join(f"{key}={value}\n" for key, value in sorted(_env(plan, service).items()))); manifest[str(path.relative_to(output))] = _sha256(path)
                if service.type == "edge-proxy":
                    nginx_path = target_dir / "config" / f"nginx-{service.id}.conf"
                    nginx_path.write_text(_nginx_proxy_config(plan, service))
                    manifest[str(nginx_path.relative_to(output))] = _sha256(nginx_path)
    firewall = {}
    for machine in plan.machines:
        entries = []
        for service in (entry for entry in plan.services if entry.machine_id == machine.id):
            if service.exposure and service.exposure.get("mode") != "none":
                entries.append({"service": service.id, "bind": service.exposure["mode"], "network": service.exposure.get("network"), "port": service.exposure["port"], "protocols": service.exposure.get("protocols", ["tcp"])})
            if service.type in {"besu-validator", "besu-rpc"}:
                node_id = service.configuration["node_id"]
                node_order = list(plan.raw["blockchain"]["nodes"]).index(node_id)
                p2p_port = 30303 if machine.execution == "local" else plan.raw["infrastructure"]["besu"]["p2p_port_start"] + node_order
                entries.append({"service": service.id, "bind": "private", "network": plan.raw["infrastructure"]["besu"]["network"], "port": p2p_port, "protocols": ["tcp", "udp"], "purpose": "besu-p2p"})
            if service.type == "ipfs-kubo":
                entries.append({"service": service.id, "bind": "private", "network": plan.raw["infrastructure"]["ipfs"]["network"], "port": plan.raw["infrastructure"]["ipfs"]["swarm_port"], "protocols": ["tcp", "udp"], "purpose": "kubo-swarm"})
            if service.type == "ipfs-cluster":
                entries.append({"service": service.id, "bind": "private", "network": plan.raw["infrastructure"]["cluster"]["network"], "port": plan.raw["infrastructure"]["cluster"]["p2p_port"], "protocols": ["tcp"], "purpose": "cluster-p2p"})
        firewall[machine.id] = entries
    (output / "shared" / "firewall-suggestion.json").write_text(_json(firewall))
    (output / "manifest.json").write_text(_json({"version": 3, "files": manifest, "firewall": firewall})); return output
