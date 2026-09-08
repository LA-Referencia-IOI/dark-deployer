"""Deterministic production deployment inventory and host bundles."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .files import parse_env_file, write_env_secure, write_text_secure
from .storage import StorageTopologyError, load_storage_topology_document


class DeploymentError(ValueError):
    """Raised when a deployment inventory or rendered bundle is invalid."""


COMPONENTS_BY_ROLE = {
    "blockchain-a": {"blockchain-runtime", "dark-explorador"},
    "blockchain-b": {"blockchain-runtime"},
    "apps": {
        "blockchain-runtime",
        "dark-dapp",
        "dark-core-lib",
        "dark-core-admin-api",
        "dark-core-resolver-api",
        "dark-store-api",
        "dark-core-minter-api",
        "dashboard-web",
    },
    "storage-node": {"dark-ipfs"},
}
ALL_ROLES = frozenset(COMPONENTS_BY_ROLE)
APP_ENDPOINT_PORTS = {
    "admin": 8000,
    "minter": 8001,
    "resolver": 8002,
    "store_api": 8003,
    "dashboard": 8081,
}
COMPONENT_ENV_PREFIX = {
    "dark-dapp": "PRODUCTION_BLOCKCHAIN_DARK_DAPP",
    "dark-explorador": "PRODUCTION_BLOCKCHAIN_DARK_EXPLORADOR",
    "dark-core-lib": "PRODUCTION_CORE_LIB",
    "dark-core-admin-api": "PRODUCTION_CORE_ADMIN_API",
    "dark-core-resolver-api": "PRODUCTION_RESOLVER",
    "dark-store-api": "PRODUCTION_STORE_API",
    "dark-core-minter-api": "PRODUCTION_MINTER",
    "dashboard-web": "PRODUCTION_DASHBOARD",
    "dark-ipfs": "PRODUCTION_IPFS",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_-]*", value
    ):
        raise DeploymentError(
            f"{field} must use lowercase letters, digits, hyphens or underscores"
        )
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"{field} must be a non-empty string")
    return value.strip()


def _ipv4(value: Any, field: str) -> str:
    raw = _string(value, field)
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise DeploymentError(f"{field} must be an IPv4 address") from exc
    if address.version != 4:
        raise DeploymentError(f"{field} must be an IPv4 address")
    return str(address)


def _absolute_path(value: Any, field: str) -> str:
    raw = _string(value, field)
    if not Path(raw).is_absolute():
        raise DeploymentError(f"{field} must be an absolute path")
    return raw


def _http_url(value: Any, field: str) -> str:
    raw = _string(value, field).rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DeploymentError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise DeploymentError(f"{field} must not contain credentials")
    return raw


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise DeploymentError(f"{description} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"invalid {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"{description} must be a JSON object")
    return value


def current_deployer_branch(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _load_component_references(document: dict, project_root: Path) -> dict[str, str]:
    """Validate component branches from the canonical deployment topology."""
    deployment = document.get("deployment")
    if not isinstance(deployment, dict):
        raise DeploymentError("deployment must be an object")
    deployer_branch = _string(deployment.get("deployer_branch"), "deployment.deployer_branch")
    current_branch = current_deployer_branch(project_root)
    if current_branch != deployer_branch:
        raise DeploymentError(
            f"configured deployer branch {deployer_branch!r} does not match "
            f"checkout {current_branch or 'detached HEAD'!r}"
        )

    components = document.get("components")
    if not isinstance(components, dict):
        raise DeploymentError("components must be an object")
    values = {"DEPLOYER_BRANCH": deployer_branch}
    required_components = set().union(*COMPONENTS_BY_ROLE.values()) - {"blockchain-runtime"}
    for component in sorted(required_components):
        definition = components.get(component)
        if not isinstance(definition, dict):
            raise DeploymentError(f"components.{component} must be an object")
        env_prefix = COMPONENT_ENV_PREFIX[component]
        repository_key = f"{env_prefix}_REPOSITORY_URL"
        branch_key = f"{env_prefix}_REPOSITORY_BRANCH"
        repository = _string(definition.get("repository_url"), f"components.{component}.repository_url")
        branch = _string(definition.get("branch"), f"components.{component}.branch")
        values[repository_key] = repository
        values[branch_key] = branch
        parsed_repository = urlparse(repository)
        if parsed_repository.username or parsed_repository.password:
            raise DeploymentError(f"{repository_key} must not contain credentials")
    return values


def load_deployment_topology(path: Path, project_root: Path) -> dict:
    """Load and validate the canonical five-host production topology."""
    inventory = _read_json(path, "deployment topology")
    if inventory.get("version") != 1:
        raise DeploymentError("deployment topology must use schema version 1")
    if inventory.get("environment") != "production":
        raise DeploymentError("deployment inventory environment must be 'production'")
    deployment = inventory.get("deployment")
    if not isinstance(deployment, dict):
        raise DeploymentError("deployment must be an object")
    inventory["deployment_id"] = _identifier(deployment.get("id"), "deployment.id")
    ssh = deployment.get("ssh")
    if not isinstance(ssh, dict):
        raise DeploymentError("deployment.ssh must be an object")
    _string(ssh.get("user"), "deployment.ssh.user")
    if not isinstance(ssh.get("port"), int) or not 1 <= ssh["port"] <= 65535:
        raise DeploymentError("deployment.ssh.port must be a TCP port")
    _absolute_path(ssh.get("private_key_file"), "deployment.ssh.private_key_file")
    _absolute_path(ssh.get("remote_directory"), "deployment.ssh.remote_directory")

    network = inventory.get("network")
    if not isinstance(network, dict) or network.get("trust_boundary") != "vpn":
        raise DeploymentError("network.trust_boundary must be 'vpn'")
    try:
        vpn_network = ipaddress.ip_network(
            _string(network.get("vpn_cidr"), "network.vpn_cidr"), strict=False
        )
    except ValueError as exc:
        raise DeploymentError("network.vpn_cidr must be a valid IPv4 CIDR") from exc
    if vpn_network.version != 4:
        raise DeploymentError("network.vpn_cidr must be an IPv4 CIDR")
    trusted = network.get("trusted_minter_clients")
    if not isinstance(trusted, list) or len(trusted) != 1:
        raise DeploymentError("network.trusted_minter_clients must contain exactly one CIDR")
    try:
        trusted_network = ipaddress.ip_network(_string(trusted[0], "trusted client"), strict=False)
    except ValueError as exc:
        raise DeploymentError("trusted minter client must be a valid CIDR") from exc
    if not trusted_network.subnet_of(vpn_network):
        raise DeploymentError("trusted minter client must be inside network.vpn_cidr")

    signing = inventory.get("signing")
    if not isinstance(signing, dict) or signing.get("mode") != "shared":
        raise DeploymentError("signing.mode must be 'shared'")
    _absolute_path(signing.get("platform_key_target"), "signing.platform_key_target")

    blockchain = inventory.get("blockchain")
    if not isinstance(blockchain, dict):
        raise DeploymentError("blockchain must be an object")
    _absolute_path(blockchain.get("chain_artifact_target"), "blockchain.chain_artifact_target")

    storage = inventory.get("storage")
    if not isinstance(storage, dict):
        raise DeploymentError("storage must be an object")
    topology_document = {
        "version": 3,
        "cluster_name": storage.get("cluster_name"),
        "replication": storage.get("replication"),
        "nodes": storage.get("nodes"),
        "access_groups": storage.get("access_groups", {}),
    }
    try:
        topology = load_storage_topology_document(topology_document)
    except StorageTopologyError as exc:
        raise DeploymentError(f"invalid storage topology: {exc}") from exc
    _absolute_path(storage.get("swarm_key_target"), "storage.swarm_key_target")
    _absolute_path(storage.get("cluster_secret_target"), "storage.cluster_secret_target")
    if storage["swarm_key_target"] == storage["cluster_secret_target"]:
        raise DeploymentError("storage secret files must use different paths")

    hosts = inventory.get("hosts")
    if not isinstance(hosts, list) or len(hosts) != 5:
        raise DeploymentError("the production site must define exactly five hosts")

    seen_ids: set[str] = set()
    seen_addresses: set[str] = set()
    seen_storage_nodes: set[str] = set()
    role_counts = {role: 0 for role in ALL_ROLES}
    for index, host in enumerate(hosts):
        field = f"hosts[{index}]"
        if not isinstance(host, dict):
            raise DeploymentError(f"{field} must be an object")
        host_id = _identifier(host.get("id"), f"{field}.id")
        if host_id in seen_ids:
            raise DeploymentError(f"duplicate host id {host_id!r}")
        seen_ids.add(host_id)
        _string(host.get("management_address"), f"{field}.management_address")
        role = host.get("role")
        if role not in ALL_ROLES:
            raise DeploymentError(f"{field}.role must name one supported role")
        role_counts[role] += 1
        vpn_address = _ipv4(host.get("vpn_address"), f"{field}.vpn_address")
        if ipaddress.ip_address(vpn_address) not in vpn_network:
            raise DeploymentError(f"{field}.vpn_address is outside network.vpn_cidr")
        if vpn_address in seen_addresses:
            raise DeploymentError(f"duplicate host VPN address {vpn_address!r}")
        seen_addresses.add(vpn_address)
        if role == "storage-node":
            node_id = _identifier(host.get("storage_node_id"), f"{field}.storage_node_id")
            try:
                topology.node(node_id)
            except StorageTopologyError as exc:
                raise DeploymentError(str(exc)) from exc
            if node_id in seen_storage_nodes:
                raise DeploymentError(f"duplicate storage node selector {node_id!r}")
            seen_storage_nodes.add(node_id)
            if topology.node(node_id).address != vpn_address:
                raise DeploymentError(f"{field}.vpn_address must match storage.nodes.{node_id}.address")
        if role == "apps":
            group = _identifier(host.get("storage_access_group"), f"{field}.storage_access_group")
            try:
                topology.access_group_nodes(group)
            except StorageTopologyError as exc:
                raise DeploymentError(str(exc)) from exc

    if role_counts != {"blockchain-a": 1, "blockchain-b": 1, "apps": 1, "storage-node": 2}:
        raise DeploymentError(
            "host roles must contain one apps, one blockchain-a, one blockchain-b and two storage-node hosts"
        )

    inventory["_repository_env"] = _load_component_references(inventory, project_root)
    inventory["_topology_path"] = str(path.resolve())
    inventory["_storage_topology"] = topology
    return inventory


def _host_for_role(inventory: dict, role: str) -> dict:
    return next(
        host
        for host in inventory["hosts"]
        if host["role"] == role
    )


def _endpoints(inventory: dict) -> dict:
    apps = _host_for_role(inventory, "apps")
    overrides = apps.get("advertised_urls", {})
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise DeploymentError("apps advertised_urls must be an object")
    endpoints = {
        "rpc": f"http://{apps['vpn_address']}:8545",
        **{
            name: f"http://{apps['vpn_address']}:{port}"
            for name, port in APP_ENDPOINT_PORTS.items()
        },
    }
    unknown = set(overrides) - set(endpoints)
    if unknown:
        raise DeploymentError("unknown advertised URL names: " + ", ".join(sorted(unknown)))
    for name, value in overrides.items():
        endpoints[name] = _http_url(value, f"advertised_urls.{name}")
    return endpoints


def _firewall_policy(inventory: dict) -> dict:
    vpn_cidr = inventory["network"]["vpn_cidr"]
    trusted_client = inventory["network"]["trusted_minter_clients"][0]
    return {
        "version": 1,
        "trust_boundary": "vpn",
        "rules": [
            {"role": "apps", "protocol": "tcp", "ports": [8545, 8546], "sources": [_host_for_role(inventory, "blockchain-a")["vpn_address"]]},
            {"role": "apps", "protocol": "tcp", "ports": [30307], "sources": [vpn_cidr]},
            {"role": "apps", "protocol": "udp", "ports": [30307], "sources": [vpn_cidr]},
            {"role": "blockchain-a", "protocol": "tcp", "ports": [30303, 30304], "sources": [vpn_cidr]},
            {"role": "blockchain-a", "protocol": "udp", "ports": [30303, 30304], "sources": [vpn_cidr]},
            {"role": "blockchain-b", "protocol": "tcp", "ports": [30305, 30306], "sources": [vpn_cidr]},
            {"role": "blockchain-b", "protocol": "udp", "ports": [30305, 30306], "sources": [vpn_cidr]},
            {"role": "apps", "protocol": "tcp", "ports": [8000, 8002, 8003, 8081], "sources": [vpn_cidr]},
            {"role": "apps", "protocol": "tcp", "ports": [8001], "sources": [trusted_client]},
            {"role": "storage-node", "protocol": "tcp", "ports": [4001, 5001, 9094, 9095, 9096], "sources": [vpn_cidr]},
            {"role": "storage-node", "protocol": "udp", "ports": [4001], "sources": [vpn_cidr]},
        ],
        "host_only": [5433, 3307, 6380],
        "default": "deny non-VPN ingress",
    }


def _host_env(
    inventory: dict,
    host: dict,
    endpoints: dict,
    repository_env: dict[str, str],
    topology_hash: str,
) -> dict[str, str]:
    role = host["role"]
    apps = _host_for_role(inventory, "apps")
    blockchain_a = _host_for_role(inventory, "blockchain-a")
    blockchain_b = _host_for_role(inventory, "blockchain-b")
    dark_env_role = {"apps": "rpc", "blockchain-a": "validators-a", "blockchain-b": "validators-b"}.get(role, "")
    backbone_ips = ",".join((
        blockchain_a["vpn_address"], blockchain_a["vpn_address"],
        blockchain_b["vpn_address"], blockchain_b["vpn_address"], apps["vpn_address"],
    ))
    values = {
        "TYPE": "production",
        "PRODUCTION_INSTALL_COMPONENTS": role,
        "DEPLOYMENT_ID": inventory["deployment_id"],
        "DEPLOYER_BRANCH": repository_env["DEPLOYER_BRANCH"],
        "SIGNER_MODE": "shared",
        "PLATFORM_PRIVATE_KEY_FILE": inventory["signing"]["platform_key_target"],
        "PLATFORM_ADDRESS": "",
        "RPC_URL": endpoints["rpc"],
        "RPC_PUBLIC_URL": endpoints["rpc"],
        "CHAIN_ID": str(inventory.get("chain_id", 2025)),
        "PRODUCTION_DEPLOYMENT_TOPOLOGY_FILE": ".generated/deployment-topology.json",
        "PRODUCTION_DEPLOYMENT_TOPOLOGY_SHA256": sha256_file(Path(inventory["_topology_path"])),
        "PRODUCTION_STORAGE_ACCESS_GROUP": host.get("storage_access_group", "") if role == "apps" else "",
        "PRODUCTION_STORAGE_NODE_ID": host.get("storage_node_id", "") if role == "storage-node" else "",
        "PRODUCTION_IPFS_SWARM_KEY_FILE": inventory["storage"]["swarm_key_target"],
        "PRODUCTION_IPFS_CLUSTER_SECRET_FILE": inventory["storage"]["cluster_secret_target"],
        "PRODUCTION_BLOCKCHAIN_HOST": "",
        "BLOCKCHAIN_RUNTIME_ROLE": dark_env_role,
        "BLOCKCHAIN_RUNTIME_CHAIN_ARTIFACT_DIR": inventory["blockchain"]["chain_artifact_target"] if dark_env_role else "",
        "BESU_IMAGE": _string(inventory["blockchain"].get("besu_image"), "blockchain.besu_image"),
        "BLOCKCHAIN_BACKBONE_IPS": backbone_ips,
        "PRODUCTION_STORE_API_URL": "http://store-api:8003",
        "APPS_BIND_ADDRESS": host["vpn_address"] if role == "apps" else "",
        "BLOCKCHAIN_BIND_ADDRESS": host["vpn_address"] if role in {"apps", "blockchain-a", "blockchain-b"} else "",
        "PRODUCTION_ADMIN_PUBLIC_URL": endpoints["admin"],
        "PRODUCTION_MINTER_PUBLIC_URL": endpoints["minter"],
        "PRODUCTION_RESOLVER_PUBLIC_URL": endpoints["resolver"],
        "PRODUCTION_STORE_API_PUBLIC_URL": endpoints["store_api"],
        "PRODUCTION_DASHBOARD_PUBLIC_URL": endpoints["dashboard"],
    }
    for component in sorted(COMPONENTS_BY_ROLE[role]):
        if component == "blockchain-runtime":
            continue
        env_prefix = COMPONENT_ENV_PREFIX[component]
        repository_key = f"{env_prefix}_REPOSITORY_URL"
        branch_key = f"{env_prefix}_REPOSITORY_BRANCH"
        values[repository_key] = _string(repository_env.get(repository_key), repository_key)
        values[branch_key] = _string(repository_env.get(branch_key), branch_key)
        values[f"{env_prefix}_SETUP"] = "True"
        commands_key = f"{env_prefix}_COMMANDS_JSON"
        values[commands_key] = repository_env.get(commands_key, "[]")
    return values


def _write_json(path: Path, value: Any, mode: int = 0o644) -> None:
    write_text_secure(path, json.dumps(value, indent=2, sort_keys=True) + "\n", mode=mode)


def render_deployment(topology_path: Path, output_dir: Path, project_root: Path) -> Path:
    """Validate a topology and render deterministic, secret-free host bundles."""
    inventory = load_deployment_topology(topology_path.resolve(), project_root.resolve())
    public_inventory = {key: value for key, value in inventory.items() if not key.startswith("_")}
    inventory_hash = sha256_json(public_inventory)
    endpoints = _endpoints(inventory)
    repository_env = inventory["_repository_env"]
    deployer_branch = repository_env["DEPLOYER_BRANCH"]

    bundle = output_dir.resolve()
    if bundle.exists() and any(bundle.iterdir()):
        raise DeploymentError(f"output directory is not empty: {bundle}")
    shared = bundle / "shared"
    hosts_dir = bundle / "hosts"
    shared.mkdir(parents=True, exist_ok=True)
    hosts_dir.mkdir(parents=True, exist_ok=True)

    topology_output = shared / "deployment-topology.json"
    endpoints_path = shared / "deployment-endpoints.json"
    firewall_path = shared / "firewall-policy.json"
    shutil.copyfile(topology_path, topology_output)
    _write_json(endpoints_path, {"version": 1, "advertised": endpoints})
    _write_json(firewall_path, _firewall_policy(inventory))
    topology_hash = sha256_file(topology_output)
    for host in inventory["hosts"]:
        host_dir = hosts_dir / host["id"]
        host_dir.mkdir(parents=True, exist_ok=True)
        env_values = _host_env(
            inventory,
            host,
            endpoints,
            repository_env,
            topology_hash,
        )
        environment_path = host_dir / ".env.public"
        write_env_secure(environment_path, env_values, mode=0o644)
        host_config = {
            "version": 1,
            "deployment_id": inventory["deployment_id"],
            "environment": "production",
            "host": {
                "id": host["id"],
                "role": host["role"],
                "management_address": host["management_address"],
                "vpn_address": host.get("vpn_address", ""),
                "storage_node_id": host.get("storage_node_id", ""),
                "storage_access_group": host.get("storage_access_group", ""),
            },
            "release": {
                "deployer_branch": deployer_branch,
                "inventory_sha256": inventory_hash,
                "topology_sha256": topology_hash,
                "environment_sha256": sha256_file(environment_path),
            },
            "delivery": {
                "ssh": inventory["deployment"]["ssh"],
                "apply_order": ["blockchain-a", "blockchain-b", "apps", "storage-node"],
            },
            "files": {
                "environment": ".env.public",
                "topology": "../../shared/deployment-topology.json",
            },
            "secrets": {
                "platform_key": inventory["signing"]["platform_key_target"],
                "ipfs_swarm_key": inventory["storage"]["swarm_key_target"],
                "ipfs_cluster_secret": inventory["storage"]["cluster_secret_target"],
            },
        }
        _write_json(host_dir / "host.json", host_config)

    checklist = f"""# Deployment checklist: {inventory['deployment_id']}

1. Verify the deployer checkout uses branch `{deployer_branch}`.
2. Copy the public bundle to each host; do not add secrets to this directory.
3. Provision the platform key on Apps with mode 0600.
4. Securely distribute the generated dARK blockchain runtime artifact: genesis and the
   selected node key material only to each matching node host.
5. Provision both IPFS secrets on both storage hosts with mode 0600.
6. Run `host validate`, then `host plan`, on every host.
7. Apply blockchain-a, then blockchain-b, then Apps/RPC and contracts.
8. Apply both storage hosts, run `storage audit`, then apply Apps services.
9. Verify that published services bind only to VPN or loopback addresses.
10. Stop either IPFS host and verify minting and metadata resolution.
"""
    write_text_secure(bundle / "CHECKLIST.md", checklist, mode=0o644)

    file_hashes = {
        str(path.relative_to(bundle)): sha256_file(path)
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "version": 1,
        "deployment_id": inventory["deployment_id"],
        "deployer_branch": deployer_branch,
        "deployment_topology_sha256": sha256_file(topology_path),
        "inventory_sha256": inventory_hash,
        "topology_sha256": topology_hash,
        "files": file_hashes,
    }
    _write_json(bundle / "manifest.json", manifest)
    return bundle


def load_host_config(path: Path) -> tuple[dict, Path]:
    config_path = path.resolve()
    config = _read_json(config_path, "host configuration")
    if config.get("version") != 1 or config.get("environment") != "production":
        raise DeploymentError("host configuration must use production schema version 1")
    if config.get("host", {}).get("role") not in ALL_ROLES:
        raise DeploymentError("host configuration contains an unsupported role")
    return config, config_path


def validate_host_bundle(path: Path, require_secrets: bool = True) -> dict:
    config, config_path = load_host_config(path)
    files = config.get("files", {})
    resolved = {
        name: (config_path.parent / relative).resolve()
        for name, relative in files.items()
    }
    for name, file_path in resolved.items():
        if not file_path.is_file():
            raise DeploymentError(f"host {name} file not found: {file_path}")
    release = config["release"]
    expected = {
        "environment": release["environment_sha256"],
        "topology": release["topology_sha256"],
    }
    for name, digest in expected.items():
        if sha256_file(resolved[name]) != digest:
            raise DeploymentError(f"host {name} hash does not match the deployment")
    env = parse_env_file(resolved["environment"], required=True)
    if env.get("DEPLOYMENT_ID") != config["deployment_id"]:
        raise DeploymentError("host environment deployment ID does not match")
    if env.get("DEPLOYER_BRANCH") != release["deployer_branch"]:
        raise DeploymentError("host environment deployer branch does not match")

    if require_secrets:
        role = config["host"]["role"]
        required_names = (
            {"platform_key"} if role == "apps" else
            {"ipfs_swarm_key", "ipfs_cluster_secret"}
        )
        for name in required_names:
            secret_path = Path(config["secrets"][name])
            try:
                mode = secret_path.stat().st_mode & 0o777
            except OSError as exc:
                raise DeploymentError(f"required secret {name} is unavailable: {exc}") from exc
            if mode & 0o077:
                raise DeploymentError(f"required secret {name} must have mode 0600 or stricter")
    return {"config": config, "config_path": config_path, "files": resolved, "env": env}


def materialize_host_bundle(path: Path, project_root: Path) -> dict:
    """Install verified public configuration into a deployer checkout."""
    validated = validate_host_bundle(path, require_secrets=True)
    current = current_deployer_branch(project_root)
    expected = validated["config"]["release"]["deployer_branch"]
    if current != expected:
        raise DeploymentError(
            f"deployer branch {current or 'detached HEAD'} does not match {expected}"
        )
    shutil.copyfile(validated["files"]["environment"], project_root / ".env")
    os.chmod(project_root / ".env", 0o600)
    generated_topology = project_root / ".generated" / "deployment-topology.json"
    generated_topology.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(validated["files"]["topology"], generated_topology)
    os.chmod(generated_topology, 0o644)
    return validated


def host_status(path: Path, project_root: Path) -> dict:
    validated = validate_host_bundle(path, require_secrets=False)
    config = validated["config"]
    return {
        "deployment_id": config["deployment_id"],
        "host": config["host"],
        "expected_deployer_branch": config["release"]["deployer_branch"],
        "installed_deployer_branch": current_deployer_branch(project_root),
        "inventory_sha256": config["release"]["inventory_sha256"],
        "topology_sha256": config["release"]["topology_sha256"],
    }


def deployment_host_configs(bundle: Path) -> list[Path]:
    """Return host configs in the safe, fixed deployment order."""
    role_order = {"blockchain-a": 0, "blockchain-b": 1, "apps": 2, "storage-node": 3}
    configs = sorted((bundle / "hosts").glob("*/host.json"))
    return sorted(
        configs,
        key=lambda item: (role_order[load_host_config(item)[0]["host"]["role"]], item.parent.name),
    )


def deployment_delivery_plan(bundle: Path) -> list[dict[str, str]]:
    """Build the SSH delivery plan; callers decide whether to execute it."""
    plan: list[dict[str, str]] = []
    for config_path in deployment_host_configs(bundle):
        config, _ = load_host_config(config_path)
        ssh = config["delivery"]["ssh"]
        host = config["host"]
        plan.append({
            "host_id": host["id"],
            "role": host["role"],
            "destination": f"{ssh['user']}@{host['management_address']}",
            "port": str(ssh["port"]),
            "private_key_file": ssh["private_key_file"],
            "remote_directory": ssh["remote_directory"],
        })
    return plan


def verify_deployment_bundle(bundle: Path) -> list[dict]:
    """Validate all public host bundles without contacting remote hosts."""
    return [validate_host_bundle(config, require_secrets=False) for config in deployment_host_configs(bundle)]
