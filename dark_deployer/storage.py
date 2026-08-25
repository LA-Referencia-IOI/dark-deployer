"""Global multi-site IPFS Cluster topology and operational helpers."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class StorageTopologyError(ValueError):
    """Raised when a storage topology is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class StoragePeer:
    """One paired Kubo and IPFS Cluster peer on a storage server."""

    id: str
    site: str
    vpn_address: str
    ipfs_api_url: str
    cluster_api_url: str
    cluster_proxy_url: str
    host_ipfs_api_url: str = ""
    host_cluster_api_url: str = ""
    host_cluster_proxy_url: str = ""

@dataclass(frozen=True)
class ReplicationPolicy:
    """Cluster allocation thresholds."""

    replication_min: int
    replication_max: int


@dataclass(frozen=True)
class StorageTopology:
    """Validated description of every peer in one global Cluster."""

    cluster_name: str
    peers: tuple[StoragePeer, ...]
    development_single_node: bool = False

    @property
    def sites(self) -> tuple[str, ...]:
        return tuple(sorted({peer.site for peer in self.peers}))

    @property
    def policy(self) -> ReplicationPolicy:
        expected = len(self.peers)
        if self.development_single_node:
            return ReplicationPolicy(1, 1)
        if len(self.sites) == 1:
            return ReplicationPolicy(1, expected)
        return ReplicationPolicy(3, expected)

    def peer(self, peer_id: str) -> StoragePeer:
        for peer in self.peers:
            if peer.id == peer_id:
                return peer
        raise StorageTopologyError(f"unknown storage node {peer_id!r}")

    def site_peers(self, site_id: str) -> tuple[StoragePeer, ...]:
        peers = tuple(peer for peer in self.peers if peer.site == site_id)
        if not peers:
            raise StorageTopologyError(f"unknown storage site {site_id!r}")
        return peers

    def local_endpoints(self, site_id: str) -> dict[str, list[str]]:
        peers = self.site_peers(site_id)
        return {
            "ipfs": [peer.ipfs_api_url for peer in peers],
            "cluster": [peer.cluster_api_url for peer in peers],
            "proxy": [peer.cluster_proxy_url for peer in peers],
        }

    def peer_sites(self) -> dict[str, str]:
        return {peer.id: peer.site for peer in self.peers}


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


def _http_url(value: Any, field: str, default: str) -> str:
    url = default if value in (None, "") else _require_string(value, field)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StorageTopologyError(f"{field} must be an absolute HTTP(S) URL")
    return url.rstrip("/")


def _optional_http_url(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    return _http_url(value, field, "")


def load_storage_topology(path: Path) -> StorageTopology:
    """Load and strictly validate the global storage topology JSON file."""
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise StorageTopologyError(f"storage topology file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StorageTopologyError(f"invalid storage topology JSON: {exc}") from exc

    if not isinstance(document, dict) or document.get("version") != 1:
        raise StorageTopologyError("storage topology must use schema version 1")
    cluster_name = _require_identifier(document.get("cluster_name"), "cluster_name")
    sites = document.get("sites")
    if not isinstance(sites, list) or not sites:
        raise StorageTopologyError("sites must be a non-empty array")
    development_single_node = document.get("development_single_node", False)
    if not isinstance(development_single_node, bool):
        raise StorageTopologyError("development_single_node must be a boolean")
    if development_single_node and len(sites) != 1:
        raise StorageTopologyError(
            "development_single_node requires exactly one site"
        )

    peers: list[StoragePeer] = []
    seen_sites: set[str] = set()
    seen_values: dict[str, set[str]] = {
        "peer id": set(),
        "VPN address": set(),
    }
    for site_index, site_value in enumerate(sites):
        if not isinstance(site_value, dict):
            raise StorageTopologyError(f"sites[{site_index}] must be an object")
        site_id = _require_identifier(site_value.get("id"), f"sites[{site_index}].id")
        if site_id in seen_sites:
            raise StorageTopologyError(f"duplicate site id {site_id!r}")
        seen_sites.add(site_id)
        site_peers = site_value.get("peers")
        expected_peer_count = 1 if development_single_node else 2
        if not isinstance(site_peers, list) or len(site_peers) != expected_peer_count:
            if development_single_node:
                raise StorageTopologyError(
                    "development_single_node requires exactly one peer"
                )
            raise StorageTopologyError(f"site {site_id!r} must define exactly two peers")

        for peer_index, peer_value in enumerate(site_peers):
            field = f"sites[{site_index}].peers[{peer_index}]"
            if not isinstance(peer_value, dict):
                raise StorageTopologyError(f"{field} must be an object")
            peer_id = _require_identifier(peer_value.get("id"), f"{field}.id")
            vpn_address = _require_string(peer_value.get("vpn_address"), f"{field}.vpn_address")
            try:
                octets = [int(part) for part in vpn_address.split(".")]
            except ValueError as exc:
                raise StorageTopologyError(f"{field}.vpn_address must be an IPv4 address") from exc
            if len(octets) != 4 or any(part < 0 or part > 255 for part in octets):
                raise StorageTopologyError(f"{field}.vpn_address must be an IPv4 address")
            for label, value in (
                ("peer id", peer_id),
                ("VPN address", vpn_address),
            ):
                if value in seen_values[label]:
                    raise StorageTopologyError(f"duplicate {label} {value!r}")
                seen_values[label].add(value)

            ipfs_api_url = _http_url(
                peer_value.get("ipfs_api_url"),
                f"{field}.ipfs_api_url",
                f"http://{vpn_address}:5001",
            )
            cluster_api_url = _http_url(
                peer_value.get("cluster_api_url"),
                f"{field}.cluster_api_url",
                f"http://{vpn_address}:9094",
            )
            cluster_proxy_url = _http_url(
                peer_value.get("cluster_proxy_url"),
                f"{field}.cluster_proxy_url",
                f"http://{vpn_address}:9095",
            )
            peers.append(
                StoragePeer(
                    id=peer_id,
                    site=site_id,
                    vpn_address=vpn_address,
                    ipfs_api_url=ipfs_api_url,
                    cluster_api_url=cluster_api_url,
                    cluster_proxy_url=cluster_proxy_url,
                    host_ipfs_api_url=_optional_http_url(
                        peer_value.get("host_ipfs_api_url"),
                        f"{field}.host_ipfs_api_url",
                    ),
                    host_cluster_api_url=_optional_http_url(
                        peer_value.get("host_cluster_api_url"),
                        f"{field}.host_cluster_api_url",
                    ),
                    host_cluster_proxy_url=_optional_http_url(
                        peer_value.get("host_cluster_proxy_url"),
                        f"{field}.host_cluster_proxy_url",
                    ),
                )
            )

    # Compatibility with topology files generated before the one-pin write
    # policy.  The old availability mode (`false`) already has the same
    # foreground-write behavior, so it is safe to ignore.  Silently accepting
    # `true` would weaken an explicitly requested durability guarantee.
    if "strict_single_site" in document:
        legacy_strict = document["strict_single_site"]
        if not isinstance(legacy_strict, bool):
            raise StorageTopologyError("strict_single_site must be a boolean")
        if legacy_strict:
            raise StorageTopologyError(
                "strict_single_site=true is no longer supported; remove the field "
                "to use one-pin write confirmation"
            )
    return StorageTopology(
        cluster_name,
        tuple(peers),
        development_single_node,
    )


def node_environment(
    topology: StorageTopology,
    node_id: str,
    swarm_key_file: str,
    cluster_secret_file: str,
) -> dict[str, str]:
    """Generate the production one-pair Compose environment for a node."""
    local = topology.peer(node_id)
    # The first peer may seed a brand-new deployment after a bounded wait. It
    # still knows every other address so it can rejoin them after disaster
    # recovery instead of accidentally creating an isolated replacement state.
    remote = [peer for peer in topology.peers if peer.id != node_id]
    policy = topology.policy
    return {
        "NODE_ID": local.id,
        "SITE_ID": local.site,
        "VPN_ADDRESS": local.vpn_address,
        "CLUSTER_NAME": topology.cluster_name,
        "CLUSTER_SEED": "true" if local == topology.peers[0] else "false",
        "IPFS_SWARM_KEY_FILE": swarm_key_file,
        "IPFS_CLUSTER_SECRET_FILE": cluster_secret_file,
        "IPFS_ANNOUNCE_MULTIADDRESS": f"/ip4/{local.vpn_address}/tcp/4001",
        "CLUSTER_ANNOUNCE_MULTIADDRESS": f"/ip4/{local.vpn_address}/tcp/9096",
        "IPFS_BOOTSTRAP_HOSTS": " ".join(peer.vpn_address for peer in remote),
        "CLUSTER_BOOTSTRAP_HOSTS": " ".join(peer.vpn_address for peer in remote),
        "CLUSTER_REPLICATION_MIN": str(policy.replication_min),
        "CLUSTER_REPLICATION_MAX": str(policy.replication_max),
        "HOST_BIND_ADDRESS": local.vpn_address,
        "IPFS_API_HOST_PORT": "5001",
        "IPFS_SWARM_HOST_PORT": "4001",
        "CLUSTER_REST_HOST_PORT": "9094",
        "CLUSTER_PROXY_HOST_PORT": "9095",
        "CLUSTER_SWARM_HOST_PORT": "9096",
        "IPFS_DARK_NET_ALIAS": f"dark-ipfs-{local.id}",
        "CLUSTER_DARK_NET_ALIAS": f"dark-ipfs-cluster-{local.id}",
    }


def store_api_environment(topology: StorageTopology, site_id: str) -> dict[str, str]:
    """Generate local endpoint pools and the site used for derived purge policy."""
    endpoints = topology.local_endpoints(site_id)
    return {
        "IPFS_API_URLS_JSON": json.dumps(endpoints["ipfs"], separators=(",", ":")),
        "IPFS_CLUSTER_API_URLS_JSON": json.dumps(endpoints["cluster"], separators=(",", ":")),
        "IPFS_CLUSTER_PROXY_API_URLS_JSON": json.dumps(endpoints["proxy"], separators=(",", ":")),
        "IPFS_CLUSTER_PEER_SITES_JSON": json.dumps(topology.peer_sites(), separators=(",", ":")),
        "IPFS_CLUSTER_LOCAL_SITE_ID": site_id,
    }


def parse_json_stream(raw: str) -> list[Any]:
    """Parse either a JSON array/object or consecutive JSON objects."""
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
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def cluster_request(
    base_urls: Iterable[str],
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> str:
    """Call the first reachable local Cluster REST endpoint."""
    suffix = path
    if query:
        suffix = f"{path}?{urllib.parse.urlencode(query)}"
    failures: list[str] = []
    for base_url in base_urls:
        request = urllib.request.Request(f"{base_url.rstrip('/')}{suffix}", method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            failures.append(f"{base_url}: {exc}")
    raise RuntimeError("all local Cluster APIs failed: " + "; ".join(failures))


def pin_entries(raw: str) -> list[dict[str, Any]]:
    """Normalize Cluster allocation/status response shapes to pin objects."""
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
    if isinstance(value, dict):
        value = value.get("/")
    return str(value or "")


def pinned_peer_ids(entry: dict[str, Any]) -> set[str]:
    """Extract peers reporting PINNED from a global status entry."""
    peer_map = entry.get("peer_map")
    if not isinstance(peer_map, dict):
        return set()
    return {
        str(peer_id)
        for peer_id, status_value in peer_map.items()
        if isinstance(status_value, dict)
        and str(status_value.get("status", "")).lower() == "pinned"
    }
