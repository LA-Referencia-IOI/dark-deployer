"""Storage topology, runtime resolution and Cluster operational helpers.

The manually maintained production topology deliberately contains only stable
infrastructure facts. Docker names, HTTP endpoints and bootstrap lists are
derived here, rather than copied into several ``.env`` files.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class StorageTopologyError(ValueError):
    """Raised when storage configuration is incomplete or inconsistent."""


@dataclass(frozen=True)
class StorageNode:
    """A physical production host running one Kubo + Cluster pair."""

    id: str
    address: str


@dataclass(frozen=True)
class ReplicationPolicy:
    """Publication and durability thresholds, expressed as pin counts."""

    publish_after_replicas: int
    target_replicas: int


@dataclass(frozen=True)
class StorageTopology:
    """The strict v3 production topology; it contains no runtime URLs."""

    cluster_name: str
    nodes: tuple[StorageNode, ...]
    access_groups: dict[str, tuple[str, ...]]
    configured_publish_after_replicas: int
    configured_target_replicas: int

    @property
    def policy(self) -> ReplicationPolicy:
        return ReplicationPolicy(
            self.configured_publish_after_replicas,
            self.configured_target_replicas,
        )

    def node(self, node_id: str) -> StorageNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise StorageTopologyError(f"unknown storage node {node_id!r}")

    def access_group_nodes(self, group_id: str) -> tuple[StorageNode, ...]:
        if not self.access_groups:
            return self.nodes
        if not group_id:
            raise StorageTopologyError("a storage access group is required")
        try:
            ids = self.access_groups[group_id]
        except KeyError as exc:
            raise StorageTopologyError(f"unknown storage access group {group_id!r}") from exc
        return tuple(self.node(node_id) for node_id in ids)


@dataclass(frozen=True)
class RuntimeStorageNode:
    """Resolved endpoints consumed by Compose and Store API."""

    id: str
    address: str
    ipfs_api_url: str
    cluster_api_url: str
    cluster_proxy_url: str
    docker_ipfs_alias: str = ""
    docker_cluster_alias: str = ""


@dataclass(frozen=True)
class StorageRuntime:
    """Generated runtime configuration for either production or developer."""

    cluster_name: str
    nodes: tuple[RuntimeStorageNode, ...]
    policy: ReplicationPolicy
    source: str

    def node(self, node_id: str) -> RuntimeStorageNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise StorageTopologyError(f"unknown storage node {node_id!r}")

    def endpoint_pool(self, node_ids: Iterable[str] | None = None) -> tuple[RuntimeStorageNode, ...]:
        if node_ids is None:
            return self.nodes
        return tuple(self.node(node_id) for node_id in node_ids)

    def store_document(self, node_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Return the minimal generated document mounted by Store API."""
        return {
            "version": 1,
            "nodes": [
                {
                    "id": node.id,
                    "ipfs_api_url": node.ipfs_api_url,
                    "cluster_api_url": node.cluster_api_url,
                }
                for node in self.endpoint_pool(node_ids)
            ],
        }


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StorageTopologyError(f"{field} must be a non-empty string")
    return value.strip()


def _require_identifier(value: Any, field: str) -> str:
    identifier = _require_string(value, field)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier):
        raise StorageTopologyError(
            f"{field} must use lowercase letters, digits, hyphens or underscores"
        )
    return identifier


def _require_address(value: Any, field: str) -> str:
    address = _require_string(value, field)
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise StorageTopologyError(f"{field} must be a valid IP address") from exc
    if parsed.version != 4:
        raise StorageTopologyError(f"{field} must be an IPv4 private/VPN address")
    if not parsed.is_private:
        raise StorageTopologyError(f"{field} must be a private/VPN address")
    return str(parsed)


def _require_positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StorageTopologyError(f"{field} must be an integer of at least 1")
    return value


def load_storage_topology_document(document: Any) -> StorageTopology:
    """Strictly validate a v3 storage topology embedded in a larger document."""
    if not isinstance(document, dict) or document.get("version") != 3:
        raise StorageTopologyError("storage topology must use schema version 3")
    unknown_root = set(document) - {"version", "cluster_name", "replication", "nodes", "access_groups"}
    if unknown_root:
        raise StorageTopologyError("storage topology contains unsupported field(s): " + ", ".join(sorted(unknown_root)))
    cluster_name = _require_identifier(document.get("cluster_name"), "cluster_name")
    raw_nodes = document.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise StorageTopologyError("nodes must be a non-empty array")
    nodes: list[StorageNode] = []
    ids: set[str] = set()
    addresses: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        field = f"nodes[{index}]"
        if not isinstance(raw_node, dict):
            raise StorageTopologyError(f"{field} must be an object")
        if set(raw_node) != {"id", "address"}:
            raise StorageTopologyError(f"{field} must contain only id and address")
        node_id = _require_identifier(raw_node.get("id"), f"{field}.id")
        address = _require_address(raw_node.get("address"), f"{field}.address")
        if node_id in ids:
            raise StorageTopologyError(f"duplicate node id {node_id!r}")
        if address in addresses:
            raise StorageTopologyError(f"duplicate node address {address!r}")
        ids.add(node_id)
        addresses.add(address)
        nodes.append(StorageNode(node_id, address))
    raw_replication = document.get("replication")
    if not isinstance(raw_replication, dict):
        raise StorageTopologyError("replication must be an object")
    if set(raw_replication) != {"publish_after_replicas", "target_replicas"}:
        raise StorageTopologyError("replication must contain publish_after_replicas and target_replicas")
    publish_after = _require_positive_integer(raw_replication.get("publish_after_replicas"), "replication.publish_after_replicas")
    target = _require_positive_integer(raw_replication.get("target_replicas"), "replication.target_replicas")
    if publish_after > target:
        raise StorageTopologyError("replication.publish_after_replicas cannot exceed target_replicas")
    if target > len(nodes):
        raise StorageTopologyError("replication.target_replicas cannot exceed the total number of nodes")
    raw_groups = document.get("access_groups", {})
    if not isinstance(raw_groups, dict):
        raise StorageTopologyError("access_groups must be an object")
    groups: dict[str, tuple[str, ...]] = {}
    for raw_name, raw_members in raw_groups.items():
        name = _require_identifier(raw_name, "access_groups key")
        if not isinstance(raw_members, list) or not raw_members:
            raise StorageTopologyError(f"access group {name!r} must be a non-empty array")
        members = tuple(_require_identifier(value, f"access_groups.{name}") for value in raw_members)
        if len(set(members)) != len(members):
            raise StorageTopologyError(f"access group {name!r} contains duplicate nodes")
        unknown = set(members) - ids
        if unknown:
            raise StorageTopologyError(f"access group {name!r} references unknown node(s): {', '.join(sorted(unknown))}")
        groups[name] = members
    return StorageTopology(cluster_name, tuple(nodes), groups, publish_after, target)


def load_storage_topology(path: Path) -> StorageTopology:
    """Load and strictly validate a standalone v3 storage topology file."""
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise StorageTopologyError(f"storage topology file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageTopologyError(f"invalid storage topology JSON: {exc}") from exc
    return load_storage_topology_document(document)


def production_runtime(topology: StorageTopology) -> StorageRuntime:
    """Derive private service URLs from the production node addresses."""
    return StorageRuntime(
        topology.cluster_name,
        tuple(RuntimeStorageNode(node.id, node.address, f"http://{node.address}:5001", f"http://{node.address}:9094", f"http://{node.address}:9095") for node in topology.nodes),
        topology.policy,
        "production-topology",
    )


def developer_runtime(mode: str) -> StorageRuntime:
    """Create the regenerable Docker-only simple or HA developer preset."""
    normalized = mode.strip().lower()
    if normalized not in {"simple", "ha"}:
        raise StorageTopologyError("DEVELOPER_STORAGE_MODE must be 'simple' or 'ha'")
    count = 1 if normalized == "simple" else 2
    nodes = tuple(
        RuntimeStorageNode(
            id=f"developer-storage-{index}",
            address="",
            ipfs_api_url="http://dark-ipfs-local:5001" if count == 1 else f"http://dark-ipfs-developer-storage-{index}:5001",
            cluster_api_url="http://dark-ipfs-cluster-local:9094" if count == 1 else f"http://dark-ipfs-cluster-developer-storage-{index}:9094",
            cluster_proxy_url="http://dark-ipfs-cluster-local:9095" if count == 1 else f"http://dark-ipfs-cluster-developer-storage-{index}:9095",
            docker_ipfs_alias="dark-ipfs-local" if count == 1 else f"dark-ipfs-developer-storage-{index}",
            docker_cluster_alias="dark-ipfs-cluster-local" if count == 1 else f"dark-ipfs-cluster-developer-storage-{index}",
        )
        for index in range(1, count + 1)
    )
    return StorageRuntime("dark-developer", nodes, ReplicationPolicy(1, count), f"developer-{normalized}")


def runtime_document(runtime: StorageRuntime, node_ids: Iterable[str] | None = None) -> str:
    """Serialize a generated document; callers own its destination/lifetime."""
    return json.dumps(runtime.store_document(node_ids), indent=2, sort_keys=True) + "\n"


def node_environment(runtime: StorageRuntime, node_id: str, swarm_key_file: str, cluster_secret_file: str) -> dict[str, str]:
    """Generate one Kubo + Cluster Compose environment from resolved runtime."""
    local = runtime.node(node_id)
    remote = [node for node in runtime.nodes if node.id != node_id]
    is_developer = runtime.source.startswith("developer-")
    if is_developer:
        # The shared Docker network resolves these aliases for every local
        # development peer.  Kubo rejects an empty AppendAnnounce entry, so
        # developer nodes must advertise a real DNS multiaddress too.
        announce_ipfs = f"/dns4/{local.docker_ipfs_alias}/tcp/4001"
        announce_cluster = f"/dns4/{local.docker_cluster_alias}/tcp/9096"
        bootstrap_hosts = " ".join(node.docker_ipfs_alias for node in remote)
        cluster_bootstrap = " ".join(node.docker_cluster_alias for node in remote)
        host_bind = "127.0.0.1"
    else:
        announce_ipfs = f"/ip4/{local.address}/tcp/4001"
        announce_cluster = f"/ip4/{local.address}/tcp/9096"
        bootstrap_hosts = " ".join(node.address for node in remote)
        cluster_bootstrap = bootstrap_hosts
        host_bind = local.address
    return {
        "NODE_ID": local.id, "CLUSTER_NAME": runtime.cluster_name,
        "CLUSTER_SEED": "true" if local == runtime.nodes[0] else "false",
        "IPFS_SWARM_KEY_FILE": swarm_key_file, "IPFS_CLUSTER_SECRET_FILE": cluster_secret_file,
        "IPFS_ANNOUNCE_MULTIADDRESS": announce_ipfs, "CLUSTER_ANNOUNCE_MULTIADDRESS": announce_cluster,
        "IPFS_BOOTSTRAP_HOSTS": bootstrap_hosts, "CLUSTER_BOOTSTRAP_HOSTS": cluster_bootstrap,
        "CLUSTER_REPLICATION_MIN": "1", "CLUSTER_REPLICATION_MAX": str(runtime.policy.target_replicas),
        "CLUSTER_PINTRACKER_CONCURRENTPINS": "20",
        "HOST_BIND_ADDRESS": host_bind, "IPFS_API_HOST_PORT": "5001", "IPFS_SWARM_HOST_PORT": "4001",
        "CLUSTER_REST_HOST_PORT": "9094", "CLUSTER_PROXY_HOST_PORT": "9095", "CLUSTER_SWARM_HOST_PORT": "9096",
        "IPFS_DARK_NET_ALIAS": local.docker_ipfs_alias or f"dark-ipfs-{local.id}",
        "CLUSTER_DARK_NET_ALIAS": local.docker_cluster_alias or f"dark-ipfs-cluster-{local.id}",
    }


def minter_replication_environment(runtime: StorageRuntime) -> dict[str, str]:
    return {
        "REPLICATION_PUBLISH_AFTER_REPLICAS": str(runtime.policy.publish_after_replicas),
        "REPLICATION_TARGET_REPLICAS": str(runtime.policy.target_replicas),
    }


def parse_json_stream(raw: str) -> list[Any]:
    raw = raw.strip()
    if not raw:
        return []
    values: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        value, index = decoder.raw_decode(raw, index)
        values.extend(value if isinstance(value, list) else [value])
    return values


def cluster_request(base_urls: Iterable[str], method: str, path: str, *, query: dict[str, str] | None = None, timeout: float = 15.0) -> str:
    suffix = path + (f"?{urllib.parse.urlencode(query)}" if query else "")
    failures: list[str] = []
    for base_url in base_urls:
        request = urllib.request.Request(f"{base_url.rstrip('/')}{suffix}", method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            failures.append(f"{base_url}: {exc}")
    raise RuntimeError("all Cluster APIs failed: " + "; ".join(failures))


def pin_entries(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for value in parse_json_stream(raw):
        if isinstance(value, dict):
            nested = value.get("pins")
            if isinstance(nested, list):
                entries.extend(item for item in nested if isinstance(item, dict))
            else:
                entries.append(value)
    return entries


def entry_cid(entry: dict[str, Any]) -> str:
    value = entry.get("cid")
    return str(value.get("/") if isinstance(value, dict) else value or "")


def pinned_peer_ids(entry: dict[str, Any]) -> set[str]:
    peer_map = entry.get("peer_map")
    if not isinstance(peer_map, dict):
        return set()
    return {str(peer_id) for peer_id, value in peer_map.items() if isinstance(value, dict) and str(value.get("status", "")).lower() == "pinned"}
