"""Compose specifications generated from deployment v2 groups.

The renderer owns the service topology.  Paths are already absolute paths on
the destination host; no generated Compose file includes legacy Compose files.
"""

from __future__ import annotations

from .model import DeploymentPlan, Group
from .network import (
    chain_node_addresses,
    exposure_bind_address,
    exposed_service_port,
    host_bind_address,
    storage_announce_address,
    storage_container_addresses,
)


def _network(plan: DeploymentPlan, group: Group) -> str:
    return f"{plan.deployment_id}-{group.machine_id}"


def _build(workspace: str, dockerfile: str) -> dict:
    return {"context": f"{workspace}/components", "dockerfile": dockerfile}


def _secret_file(plan: DeploymentPlan, machine_id: str, secret_id: str) -> str:
    """Resolve a destination-local secret path without putting it in a bundle."""
    machine = plan.machine(machine_id)
    definition = plan.raw["secrets"].get(secret_id, {})
    relative_path = definition.get("path", secret_id)
    return f"{machine.secrets_root}/{relative_path}"


def _exposed_ports(plan: DeploymentPlan, machine, service: str, container_port: int) -> list[str]:
    """Publish a UI/API only when the inventory explicitly permits it."""
    bind = exposure_bind_address(machine, plan.raw, service)
    port = exposed_service_port(plan.raw, service)
    return [f"{bind}:{port}:{container_port}"] if bind is not None and port else []


def _storage_hosts(plan: DeploymentPlan, group: Group, service_prefix: str) -> list[str]:
    """Addresses usable by this group for bootstrap, derived from placement."""
    hosts: list[str] = []
    for candidate in sorted((item for item in plan.groups if item.kind == "storage" and item.id != group.id), key=lambda item: item.id):
        provider = plan.machine(candidate.machine_id)
        if provider.id == group.machine_id:
            hosts.append(f"{service_prefix}-{candidate.members[0] if service_prefix == 'ipfs' else candidate.id}")
        else:
            hosts.append(provider.private_address)
    return hosts


def _rpc_url_for_group(plan: DeploymentPlan, group: Group) -> str:
    """Use Docker DNS only when both groups live on the same Docker host."""
    apps = next(item for item in plan.groups if item.kind == "apps")
    provider = plan.machine(apps.machine_id)
    consumer = plan.machine(group.machine_id)
    if provider.id == consumer.id:
        return "http://blockchain-rpc:8545"
    return f"http://{provider.private_address}:{exposed_service_port(plan.raw, 'blockchain-rpc')}"


def _apps(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    network = _network(plan, group)
    components = machine.workspace_root
    data = f"{machine.data_root}/{plan.deployment_id}/apps"
    bind = host_bind_address(machine)
    node_addresses = chain_node_addresses(plan)
    minter_build = _build(components, "services/dark-core-minter-api/Dockerfile")
    contracts_build = {"context": machine.workspace_root, "dockerfile": "deployment_v2/Dockerfile.contracts"}
    # `required: false` keeps `docker compose config` usable for a public
    # bundle.  The runner separately requires the file before `apply` starts
    # any apps service, so this never weakens runtime secret validation.
    contract_env = f"{data}/contracts/contracts.env"
    minter_env = ["./env/minter.env", {"path": contract_env, "required": False}, {"path": _secret_file(plan, machine.id, "minter-runtime-env"), "required": False}]
    admin_env = ["./env/admin-api.env", {"path": contract_env, "required": False}, {"path": _secret_file(plan, machine.id, "minter-runtime-env"), "required": False}]
    resolver_env = ["./env/resolver-api.env", {"path": contract_env, "required": False}]
    dashboard_env = ["./env/dashboard.env", {"path": _secret_file(plan, machine.id, "dashboard-runtime-env"), "required": False}]
    dashboard_db_env = ["./env/dashboard-db.env", {"path": _secret_file(plan, machine.id, "dashboard-runtime-env"), "required": False}]
    common = {"restart": "unless-stopped", "networks": [network]}
    services = {
        "blockchain-rpc": {
            **common,
            "image": plan.raw["blockchain"]["besu_image"],
            "profiles": ["rpc"],
            "ports": [*_exposed_ports(plan, machine, "blockchain-rpc", 8545), f"{bind}:30307:30307/tcp", f"{bind}:30307:30307/udp"],
            "volumes": [f"{data}/blockchain/rpc01:/data", f"{data}/blockchain/config:/config:ro"],
            "command": [
                "--config-file=/config/besu-config.toml", "--genesis-file=/config/genesis.json",
                "--node-private-key-file=/data/nodekey", "--p2p-port=30307",
                f"--p2p-host={node_addresses['rpc01']}", "--rpc-http-enabled=true",
                "--rpc-http-host=0.0.0.0",
            ],
            "networks": {network: {"ipv4_address": node_addresses["rpc01"]}},
        },
        # Health means the PostgreSQL server is accepting connections.  The
        # application database may be created idempotently during bootstrap,
        # so probing `minter` here would make a fresh bind mount unhealthy.
        "postgres": {**common, "image": "postgres:15-alpine", "environment": {"POSTGRES_USER": "dark", "POSTGRES_PASSWORD_FILE": "/run/secrets/minter-db-password", "POSTGRES_DB": "minter"}, "volumes": [f"{data}/minter/postgres:/var/lib/postgresql/data"], "secrets": ["minter-db-password"], "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U dark -d postgres"], "interval": "3s", "timeout": "3s", "retries": 20}},
        "admin-api": {**common, "build": _build(components, "services/dark-core-admin-api/Dockerfile"), "env_file": admin_env, "ports": _exposed_ports(plan, machine, "admin-api", 8000)},
        "resolver-api": {**common, "build": _build(components, "services/dark-core-resolver-api/Dockerfile"), "env_file": resolver_env, "ports": _exposed_ports(plan, machine, "resolver-api", 8002)},
        "store-api": {**common, "build": _build(components, "services/dark-store-api/Dockerfile"), "env_file": ["./env/store-api.env"], "ports": _exposed_ports(plan, machine, "store-api", 8003), "volumes": ["./config/storage-endpoints.json:/config/storage-endpoints.json:ro"]},
        "minter-api": {**common, "build": minter_build, "env_file": minter_env, "ports": _exposed_ports(plan, machine, "minter-api", 8001), "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-migrate": {**common, "profiles": ["setup"], "build": minter_build, "command": ["migrate"], "env_file": minter_env, "depends_on": {"postgres": {"condition": "service_healthy"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "rpc-probe": {**common, "profiles": ["setup"], "build": contracts_build, "entrypoint": ["python"], "environment": {"DARK_RPC_URL": "http://blockchain-rpc:8545", "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"]) }},
        "contracts-deploy": {**common, "profiles": ["setup"], "build": contracts_build, "environment": {"DARK_RPC_URL": "http://blockchain-rpc:8545", "DARK_CHAIN_ID": str(plan.raw["blockchain"]["chain_id"]), "CONTRACT_SIGNER_FILE": "/run/dark-secrets/contract-signer", "CONTRACT_HANDOFF_FILE": "/state/handoff.json", "CONTRACT_RUNTIME_ENV_FILE": "/state/contracts.env"}, "volumes": [f"{_secret_file(plan, machine.id, 'contract-signer')}:/run/dark-secrets/contract-signer:ro", f"{data}/contracts:/state"]},
        "minter-metadata-worker": {**common, "build": minter_build, "command": ["metadata-worker"], "env_file": minter_env, "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-replication-worker": {**common, "build": minter_build, "command": ["replication-worker"], "env_file": minter_env, "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-chain-worker": {**common, "build": minter_build, "command": ["chain-worker"], "env_file": minter_env, "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "dashboard-mysql": {**common, "image": "mysql:8.0", "env_file": dashboard_db_env, "volumes": [f"{data}/dashboard/mysql:/var/lib/mysql"], "healthcheck": {"test": ["CMD-SHELL", "mysqladmin ping -h localhost -u root -p\"$$MYSQL_ROOT_PASSWORD\""], "interval": "3s", "timeout": "3s", "retries": 20}},
        "dashboard-redis": {**common, "image": "redis:7-alpine", "command": ["redis-server", "--appendonly", "yes"], "volumes": [f"{data}/dashboard/redis:/data"]},
        "dashboard": {**common, "image": "ambientum/php:8.0-nginx", "env_file": dashboard_env, "ports": _exposed_ports(plan, machine, "dashboard", 8080), "volumes": [f"{components}/components/dashboard-web:/var/www/app"]},
        "dashboard-migrate": {**common, "profiles": ["setup"], "image": "ambientum/php:8.0-nginx", "env_file": dashboard_env, "depends_on": {"dashboard-mysql": {"condition": "service_healthy"}}, "command": ["sh", "-lc", "cd /var/www/app && php composer.phar install --no-interaction --prefer-dist --no-dev && php artisan migrate --force --seed && php artisan storage:link --force"], "volumes": [f"{components}/components/dashboard-web:/var/www/app"]},
    }
    return {
        "name": f"{plan.deployment_id}-{group.id}", "services": services,
        "networks": {network: {"name": network, "external": True}},
        "secrets": {"minter-db-password": {"file": _secret_file(plan, machine.id, "minter-db-password")}},
    }


def _validators(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    network = _network(plan, group)
    data = f"{machine.data_root}/{plan.deployment_id}/blockchain"
    bind = host_bind_address(machine)
    node_addresses = chain_node_addresses(plan)
    services = {}
    base_port = 30303
    node_order = ["validator01", "validator02", "validator03", "validator04"]
    for node in group.members:
        port = base_port + node_order.index(node)
        services[node] = {"image": plan.raw["blockchain"]["besu_image"], "restart": "unless-stopped", "volumes": [f"{data}/config:/config:ro", f"{data}/{node}:/data"], "command": ["--config-file=/config/besu-config.toml", "--genesis-file=/config/genesis.json", "--node-private-key-file=/data/nodekey", f"--p2p-port={port}", f"--p2p-host={node_addresses[node]}", "--rpc-http-enabled=false"], "ports": [f"{bind}:{port}:{port}/tcp", f"{bind}:{port}:{port}/udp"], "networks": {network: {"ipv4_address": node_addresses[node]}}}
    if group.explorer:
        explorer_root = f"{machine.workspace_root}/components/dark-explorador"
        services["explorer"] = {
            # The explorer Dockerfile copies its pre-built dist/ relative to
            # the component root, not the monorepo components directory.
            "build": {"context": explorer_root, "dockerfile": "Dockerfile"},
            "restart": "unless-stopped", "ports": _exposed_ports(plan, machine, "explorer", 80),
            "environment": {"RPC_HTTP_URL": _rpc_url_for_group(plan, group)},
            "volumes": [
                f"{explorer_root}/default.conf.template:/etc/nginx/templates/default.conf.template:ro",
                f"{explorer_root}/docker-entrypoint.sh:/docker-entrypoint.d/40-overwrite-infura.sh:ro",
            ],
            "networks": [network],
        }
    return {"name": f"{plan.deployment_id}-{group.id}", "services": services, "networks": {network: {"name": network, "external": True}}}


def _storage(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    network = _network(plan, group)
    node = group.members[0]
    data = f"{machine.data_root}/{plan.deployment_id}/storage/{node}"
    workspace = machine.workspace_root
    colocated = sorted(
        (candidate for candidate in plan.groups if candidate.kind == "storage" and candidate.machine_id == machine.id),
        key=lambda candidate: candidate.id,
    )
    slot = colocated.index(group)
    ipfs_api_port = 5001 + slot
    ipfs_swarm_port = 4001 + slot
    cluster_rest_port = 9094 + (slot * 3)
    cluster_proxy_port = 9095 + (slot * 3)
    cluster_swarm_port = 9096 + (slot * 3)
    ipfs_bootstrap_hosts = " ".join(_storage_hosts(plan, group, "ipfs"))
    cluster_bootstrap_hosts = " ".join(_storage_hosts(plan, group, "cluster"))
    seed = group.id == min(candidate.id for candidate in plan.groups if candidate.kind == "storage")
    swarm_key = _secret_file(plan, machine.id, "ipfs-swarm-key")
    cluster_secret = _secret_file(plan, machine.id, "ipfs-cluster-secret")
    container_addresses = storage_container_addresses(plan).get(group.id, {})
    ipfs_network = {network: {"aliases": [f"ipfs-{node}"]}}
    cluster_network = {network: {"aliases": [f"cluster-{group.id}"]}}
    if container_addresses:
        ipfs_network[network]["ipv4_address"] = container_addresses["ipfs"]
        cluster_network[network]["ipv4_address"] = container_addresses["cluster"]
    services = {
        "ipfs": {
            "image": "ipfs/kubo:v0.41.0", "restart": "unless-stopped", "entrypoint": ["/usr/local/bin/ipfs-entrypoint.sh"],
            "environment": {"NODE_NAME": node, "IPFS_SWARM_KEY_FILE": "/run/dark-secrets/ipfs-swarm.key", "IPFS_BOOTSTRAP_HOSTS": ipfs_bootstrap_hosts, "CLUSTER_SEED": str(seed).lower(), "IPFS_ANNOUNCE_MULTIADDRESS": f"/ip4/{storage_announce_address(plan, group)}/tcp/{ipfs_swarm_port}"},
            "volumes": [f"{workspace}/components/dark-ipfs/scripts/ipfs-entrypoint.sh:/usr/local/bin/ipfs-entrypoint.sh:ro", f"{swarm_key}:/run/dark-secrets/ipfs-swarm.key:ro", f"{data}/ipfs:/data/ipfs"],
            "ports": [f"{host_bind_address(machine)}:{ipfs_api_port}:5001", f"{host_bind_address(machine)}:{ipfs_swarm_port}:4001/tcp", f"{host_bind_address(machine)}:{ipfs_swarm_port}:4001/udp"], "networks": ipfs_network,
        },
        "cluster": {
            "image": "ipfs/ipfs-cluster:v1.1.6", "restart": "unless-stopped", "entrypoint": ["/usr/local/bin/cluster-entrypoint.sh"], "depends_on": ["ipfs"],
            "environment": {"CLUSTER_PEERNAME": node, "CLUSTER_SECRET_FILE": "/run/dark-secrets/ipfs-cluster-secret", "CLUSTER_BOOTSTRAP_HOSTS": cluster_bootstrap_hosts, "CLUSTER_SEED": str(seed).lower(), "CLUSTER_CRDT_CLUSTERNAME": plan.raw["storage"]["cluster_name"], "IPFS_DARK_NET_ALIAS": f"ipfs-{node}"},
            "volumes": [f"{workspace}/components/dark-ipfs/scripts/cluster-entrypoint.sh:/usr/local/bin/cluster-entrypoint.sh:ro", f"{cluster_secret}:/run/dark-secrets/ipfs-cluster-secret:ro", f"{data}/cluster:/data/ipfs-cluster"],
            "ports": [f"{host_bind_address(machine)}:{cluster_rest_port}:9094", f"{host_bind_address(machine)}:{cluster_proxy_port}:9095", f"{host_bind_address(machine)}:{cluster_swarm_port}:9096/tcp", f"{host_bind_address(machine)}:{cluster_swarm_port}:9096/udp"], "networks": cluster_network,
        },
    }
    return {"name": f"{plan.deployment_id}-{group.id}", "services": services, "networks": {network: {"name": network, "external": True}}}


def compose_document(plan: DeploymentPlan, group: Group) -> dict:
    if group.kind == "apps":
        return _apps(plan, group)
    if group.kind == "validators":
        return _validators(plan, group)
    return _storage(plan, group)
