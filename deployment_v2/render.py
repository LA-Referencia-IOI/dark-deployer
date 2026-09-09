"""Render public deployment artifacts. Rendering never contacts Docker or SSH."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import yaml

from .model import DeploymentPlan, Group
from .services import compose_document


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _yaml(value: object) -> str:
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False)


def _group_environment(plan: DeploymentPlan, group: Group) -> dict[str, str]:
    """Public values only. Private values are provisioned separately by apply."""
    machine = plan.machine(group.machine_id)
    endpoints = {endpoint.service: endpoint.url for endpoint in plan.endpoints}
    values = {
        "DARK_DEPLOYMENT_ID": plan.deployment_id,
        "DARK_GROUP_ID": group.id,
        "DARK_PRIVATE_ADDRESS": machine.private_address,
        "DARK_RPC_URL": endpoints.get("blockchain-rpc", ""),
        "STORE_API_URL": endpoints.get("store-api", ""),
    }
    for endpoint in plan.endpoints:
        values[f"DARK_ENDPOINT_{endpoint.service.upper().replace('-', '_')}"] = endpoint.url
    return values


def _service_environments(plan: DeploymentPlan, group: Group) -> dict[str, dict[str, str]]:
    """Minimal public runtime contracts, with service defaults kept explicit."""
    common = _group_environment(plan, group)
    chain_id = str(plan.raw["blockchain"]["chain_id"])
    replication = plan.raw["storage"]["replication"]
    minter_settings = plan.raw["settings"].get("minter", {})
    if not isinstance(minter_settings, dict):
        minter_settings = {}
    rpc = "http://blockchain-rpc:8545"
    store = "http://store-api:8003"
    minter = {
        **common,
        "MINTER_API_HOST": "0.0.0.0", "MINTER_API_PORT": "8001",
        "DARK_RPC_URL": rpc, "DARK_CHAIN_ID": chain_id,
        "METADATA_STORAGE_TYPE": "store_api", "METADATA_STORAGE_PATH": "/app/metadata_storage",
        "METADATA_STORE_API_URL": store,
        "REPLICATION_PUBLISH_AFTER_REPLICAS": str(replication["publish_after_replicas"]),
        "REPLICATION_TARGET_REPLICAS": str(replication["target_replicas"]),
        "MINTER_SHOULDER": str(minter_settings.get("shoulder", "200")),
    }
    values = {
        "admin-api": {**common, "ADMIN_API_HOST": "0.0.0.0", "ADMIN_API_PORT": "8000", "DARK_RPC_URL": rpc, "DARK_CHAIN_ID": chain_id},
        "resolver-api": {**common, "RESOLVER_API_HOST": "0.0.0.0", "RESOLVER_API_PORT": "8002", "DARK_RPC_URL": rpc, "DARK_CHAIN_ID": chain_id, "METADATA_STORAGE_TYPE": "store_api", "METADATA_STORE_API_URL": store},
        "store-api": {**common, "STORE_API_HOST": "0.0.0.0", "STORE_API_PORT": "8003", "STORAGE_BACKEND": "ipfs_cluster", "STORAGE_ENDPOINTS_FILE": "/config/storage-endpoints.json", "REPLICATION_TARGET_REPLICAS": str(replication["target_replicas"])},
        "minter": minter,
        "dashboard": {**common, "APP_ENV": "production", "APP_DEBUG": "false", "DB_CONNECTION": "mysql", "DB_HOST": "dashboard-mysql", "DB_PORT": "3306", "DB_DATABASE": "dark", "DB_USERNAME": "dark", "REDIS_HOST": "dashboard-redis", "ADMIN_API_BASE_URL": "http://admin-api:8000", "MINTER_BASE_URL": "http://minter-api:8001", "WORKER_STATUS_URL": "http://minter-api:8001/api/v1/worker/status", "RESOLVER_BASE_URL": "http://resolver-api:8002", "STORE_API_BASE_URL": store, "BLOCK_NUMBER": rpc},
        "dashboard-db": {**common, "MYSQL_DATABASE": "dark", "MYSQL_USER": "dark"},
    }
    return values


def _group_document(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    return {
        "version": 2,
        "deployment_id": plan.deployment_id,
        "group": {"id": group.id, "kind": group.kind, "members": list(group.members), "explorer": group.explorer},
        "machine": {
            "id": machine.id, "execution": machine.execution, "management_address": machine.management_address,
            "private_address": machine.private_address, "workspace_root": machine.workspace_root,
            "data_root": machine.data_root, "secrets_root": machine.secrets_root,
            "docker_subnet": plan.docker_subnets[machine.id],
        },
        "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints],
        "components": plan.raw["components"], "settings": plan.raw["settings"],
        "secrets": plan.raw["secrets"], "exposure": plan.raw["exposure"],
        "blockchain": plan.raw["blockchain"], "storage": plan.raw["storage"],
    }


def render_plan(plan: DeploymentPlan, output: Path) -> Path:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"render output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    shared = output / "shared"
    shared.mkdir()
    shutil.copyfile(plan.inventory_path, shared / "deployment-topology.json")
    (shared / "plan.json").write_text(_json({
        "deployment_id": plan.deployment_id, "docker_subnets": plan.docker_subnets,
        "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints],
        "steps": [step.__dict__ for step in plan.steps],
    }))
    groups_root = output / "groups"
    groups_root.mkdir()
    manifest: dict[str, str] = {}
    for group in plan.groups:
        target = groups_root / group.id
        target.mkdir()
        config = target / "group.json"
        config.write_text(_json(_group_document(plan, group)))
        manifest[str(config.relative_to(output))] = _sha256(config)
        compose = target / "compose.yaml"
        compose.write_text(_yaml(compose_document(plan, group)))
        manifest[str(compose.relative_to(output))] = _sha256(compose)
        env_root = target / "env"
        env_root.mkdir()
        environments = _service_environments(plan, group)
        for name, environment in environments.items():
            target_env = env_root / f"{name}.env"
            target_env.write_text("".join(f"{key}={value}\n" for key, value in sorted(environment.items())))
            manifest[str(target_env.relative_to(output))] = _sha256(target_env)
        config_root = target / "config"
        config_root.mkdir()
        storage_nodes = []
        for storage_group in (candidate for candidate in plan.groups if candidate.kind == "storage"):
            node_id = storage_group.members[0]
            cluster = next(endpoint for endpoint in plan.endpoints if endpoint.service == f"cluster-{storage_group.id}")
            ipfs = next(endpoint for endpoint in plan.endpoints if endpoint.service == f"ipfs-{node_id}")
            storage_nodes.append({"id": node_id, "ipfs_api_url": ipfs.url, "cluster_api_url": cluster.url})
        storage_config = config_root / "storage-endpoints.json"
        storage_config.write_text(_json({"version": 1, "nodes": storage_nodes}))
        manifest[str(storage_config.relative_to(output))] = _sha256(storage_config)
    (output / "manifest.json").write_text(_json({"version": 2, "files": manifest}))
    return output
