"""Compose specifications generated from deployment v2 groups.

The renderer owns the service topology.  Paths are already absolute paths on
the destination host; no generated Compose file includes legacy Compose files.
"""

from __future__ import annotations

from .model import DeploymentPlan, Group


def _network(plan: DeploymentPlan, group: Group) -> str:
    return f"{plan.deployment_id}-{group.machine_id}"


def _build(workspace: str, dockerfile: str) -> dict:
    return {"context": f"{workspace}/components", "dockerfile": dockerfile}


def _apps(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    network = _network(plan, group)
    components = machine.workspace_root
    data = f"{machine.data_root}/{plan.deployment_id}/apps"
    bind = "127.0.0.1" if machine.execution == "local" else machine.private_address
    minter_build = _build(components, "services/dark-core-minter-api/Dockerfile")
    common = {"restart": "unless-stopped", "networks": [network]}
    services = {
        "blockchain-rpc": {**common, "image": plan.raw["blockchain"]["besu_image"], "profiles": ["rpc"], "ports": [f"{bind}:8545:8545"], "volumes": [f"{data}/blockchain/rpc01:/data", f"{components}/blockchain/config:/config:ro"]},
        "postgres": {**common, "image": "postgres:15-alpine", "environment": {"POSTGRES_USER": "dark", "POSTGRES_PASSWORD_FILE": "/run/secrets/minter-db-password", "POSTGRES_DB": "minter"}, "volumes": [f"{data}/minter/postgres:/var/lib/postgresql/data"], "secrets": ["minter-db-password"]},
        "admin-api": {**common, "build": _build(components, "services/dark-core-admin-api/Dockerfile"), "env_file": ["./env/admin-api.env"], "ports": [f"{bind}:8000:8000"]},
        "resolver-api": {**common, "build": _build(components, "services/dark-core-resolver-api/Dockerfile"), "env_file": ["./env/resolver-api.env"], "ports": [f"{bind}:8002:8002"]},
        "store-api": {**common, "build": _build(components, "services/dark-store-api/Dockerfile"), "env_file": ["./env/store-api.env"], "ports": [f"{bind}:8003:8003"], "volumes": ["./config/storage-endpoints.json:/config/storage-endpoints.json:ro"]},
        "minter-api": {**common, "build": minter_build, "env_file": ["./env/minter.env"], "ports": [f"{bind}:8001:8001"], "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-metadata-worker": {**common, "build": minter_build, "command": ["metadata-worker"], "env_file": ["./env/minter.env"], "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-replication-worker": {**common, "build": minter_build, "command": ["replication-worker"], "env_file": ["./env/minter.env"], "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "minter-chain-worker": {**common, "build": minter_build, "command": ["chain-worker"], "env_file": ["./env/minter.env"], "depends_on": {"postgres": {"condition": "service_started"}}, "volumes": [f"{data}/minter/metadata:/app/metadata_storage"]},
        "dashboard-mysql": {**common, "image": "mysql:8.0", "env_file": ["./env/dashboard-db.env"], "volumes": [f"{data}/dashboard/mysql:/var/lib/mysql"]},
        "dashboard-redis": {**common, "image": "redis:7-alpine", "command": ["redis-server", "--appendonly", "yes"], "volumes": [f"{data}/dashboard/redis:/data"]},
        "dashboard": {**common, "image": "ambientum/php:8.0-nginx", "env_file": ["./env/dashboard.env"], "ports": [f"{bind}:8081:8080"], "volumes": [f"{components}/components/frontend/dashboard-web:/var/www/app"]},
    }
    return {"name": f"{plan.deployment_id}-{group.id}", "services": services, "networks": {network: {"name": network, "external": True}}, "secrets": {"minter-db-password": {"file": "./secrets/minter-db-password"}}}


def _validators(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    network = _network(plan, group)
    data = f"{machine.data_root}/{plan.deployment_id}/blockchain"
    workspace = machine.workspace_root
    services = {}
    base_port = 30303
    node_order = ["validator01", "validator02", "validator03", "validator04"]
    for node in group.members:
        port = base_port + node_order.index(node)
        services[node] = {"image": plan.raw["blockchain"]["besu_image"], "restart": "unless-stopped", "volumes": [f"{workspace}/blockchain/config:/config:ro", f"{data}/{node}:/data"], "command": ["--config-file=/config/besu-config.toml", "--genesis-file=/config/genesis.json", "--node-private-key-file=/data/nodekey", f"--p2p-port={port}", "--rpc-http-enabled=false"], "ports": [f"{machine.private_address}:{port}:{port}/tcp", f"{machine.private_address}:{port}:{port}/udp"], "networks": [network]}
    if group.explorer:
        services["explorer"] = {"image": "nginx:alpine", "restart": "unless-stopped", "ports": [f"{machine.private_address}:25000:80"], "networks": [network]}
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
    services = {
        "ipfs": {"image": "ipfs/kubo:v0.41.0", "restart": "unless-stopped", "volumes": [f"{workspace}/components/storage/dark-ipfs/scripts/ipfs-entrypoint.sh:/usr/local/bin/ipfs-entrypoint.sh:ro", f"{data}/ipfs:/data/ipfs"], "ports": [f"{machine.private_address}:{ipfs_api_port}:5001", f"{machine.private_address}:{ipfs_swarm_port}:4001/tcp", f"{machine.private_address}:{ipfs_swarm_port}:4001/udp"], "networks": {network: {"aliases": [f"ipfs-{node}"]}}},
        "cluster": {"image": "ipfs/ipfs-cluster:v1.1.6", "restart": "unless-stopped", "depends_on": ["ipfs"], "volumes": [f"{workspace}/components/storage/dark-ipfs/scripts/cluster-entrypoint.sh:/usr/local/bin/cluster-entrypoint.sh:ro", f"{data}/cluster:/data/ipfs-cluster"], "ports": [f"{machine.private_address}:{cluster_rest_port}:9094", f"{machine.private_address}:{cluster_proxy_port}:9095", f"{machine.private_address}:{cluster_swarm_port}:9096/tcp", f"{machine.private_address}:{cluster_swarm_port}:9096/udp"], "networks": {network: {"aliases": [f"cluster-{group.id}"]}}},
    }
    return {"name": f"{plan.deployment_id}-{group.id}", "services": services, "networks": {network: {"name": network, "external": True}}}


def compose_document(plan: DeploymentPlan, group: Group) -> dict:
    if group.kind == "apps":
        return _apps(plan, group)
    if group.kind == "validators":
        return _validators(plan, group)
    return _storage(plan, group)
