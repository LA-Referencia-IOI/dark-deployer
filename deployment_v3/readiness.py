"""Functional readiness gates used between declarative deployment phases."""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from .executor import resolve_executor
from .runner import _compose_directory, _compose_project, _effective_plan, _machine_directory
from .network import endpoint_for, internal_port, p2p_endpoint


class ReadinessError(RuntimeError):
    pass


def _compose(plan, machine, root, service_id=None):
    directory = _compose_directory(plan, machine, root, service_id)
    return ("docker", "compose", "--project-name", _compose_project(plan, machine, service_id), "-f", str(directory / "compose.yaml"))


def _run(plan, machine, root, service, shell: str):
    return resolve_executor(machine).run((*_compose(plan, machine, root, service), "exec", "-T", service, "sh", "-lc", shell), timeout=20)


def _host_url(service, machine) -> str:
    """Public/private URL reachable from the service's own host."""
    if not service.exposure or service.exposure.get("mode") == "none":
        raise ReadinessError(f"{service.id} has no host exposure for a local probe")
    mode = service.exposure["mode"]
    port = service.exposure["port"]
    scheme = "https" if service.type == "edge-proxy" and service.configuration.get("tls", {}).get("mode") == "direct" else "http"
    if mode == "public":
        return f"{scheme}://127.0.0.1:{port}"
    return f"{scheme}://127.0.0.1:{port}" if mode == "loopback" else f"{scheme}://{machine.address_on(service.exposure['network'])}:{port}"


def _curl(plan, machine, url, payload: str | None = None):
    argv = ["curl", "-fsS", "--max-time", "5"]
    if url.startswith("https://"):
        argv.append("-k")
    if payload is not None:
        argv.extend(["-H", "Content-Type: application/json", "--data", payload])
    argv.append(url)
    return resolve_executor(machine).run(tuple(argv), timeout=15)


def store_health_probe(plan, machine, root, service, *, refresh: bool = False):
    """Probe Store without requiring an unnecessary host-published port.

    Store is normally consumed only by application containers on the same
    Docker host. In that topology it deliberately has no ``exposure``. Run
    the HTTP request inside its Python image instead of opening a host port
    solely for deployment readiness.
    """
    path = "/health?refresh=true" if refresh else "/health"
    if service.exposure and service.exposure.get("mode") != "none":
        return _curl(plan, machine, _host_url(service, machine) + path)
    url = f"http://127.0.0.1:{internal_port(service)}{path}"
    command = "import urllib.request; urllib.request.urlopen(" + repr(url) + ", timeout=5).read()"
    return _run(plan, machine, root, service.id, "python -c " + shlex.quote(command))


def _rpc(plan, root):
    rpc = next(item for item in plan.services if item.type == "besu-rpc")
    machine = plan.machine(rpc.machine_id)
    payload = '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}'
    result = _curl(plan, machine, _host_url(rpc, machine), payload)
    if result.returncode:
        return False, result.stderr.strip() or result.stdout.strip()
    try:
        peers = int(json.loads(result.stdout)["result"], 16)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, "RPC returned invalid net_peerCount response"
    return peers >= 4, f"RPC peers={peers}; expected at least 4"


def _json_stream(payload: str) -> list[dict]:
    """Decode the consecutive JSON objects emitted by ``peers ls --enc json``."""
    decoder = json.JSONDecoder()
    offset = 0
    values = []
    while offset < len(payload):
        while offset < len(payload) and payload[offset].isspace():
            offset += 1
        if offset >= len(payload):
            break
        value, offset = decoder.raw_decode(payload, offset)
        if not isinstance(value, dict):
            raise ValueError("peer response contains a non-object JSON value")
        values.append(value)
    return values


def _cluster_membership_problem(expected_names: set[str], peers: list[dict]) -> str | None:
    """Return a precise topology error, or ``None`` for a converged cluster."""
    by_name = {str(peer.get("peername", "")): peer for peer in peers}
    observed_names = set(by_name) - {""}
    missing = expected_names - observed_names
    unexpected = observed_names - expected_names
    duplicates = len(by_name) != len(peers)
    if missing or unexpected or duplicates:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unexpected:
            details.append("unexpected " + ", ".join(sorted(unexpected)))
        if duplicates:
            details.append("duplicate or unnamed peer")
        return "IPFS Cluster membership does not match inventory: " + "; ".join(details)

    peer_ids = {str(peer.get("id", "")) for peer in peers}
    if "" in peer_ids or len(peer_ids) != len(peers):
        return "IPFS Cluster membership contains missing or duplicate peer IDs"
    for name, peer in sorted(by_name.items()):
        cluster_error = str(peer.get("error", "")).strip()
        kubo_error = str(peer.get("ipfs", {}).get("error", "")).strip()
        if cluster_error:
            return f"IPFS Cluster peer {name} reports an error: {cluster_error}"
        if kubo_error:
            return f"IPFS Cluster peer {name} cannot reach its Kubo peer: {kubo_error}"
        visible_ids = {str(value) for value in peer.get("cluster_peers", [])}
        if visible_ids != peer_ids:
            return f"IPFS Cluster peer {name} has not converged on all declared peers"
    return None


def _cluster_topology(plan, root, cluster) -> tuple[bool, str]:
    expected_names = {str(service.configuration["peer_name"]) for service in cluster}
    for service in cluster:
        result = _run(
            plan,
            plan.machine(service.machine_id),
            root,
            service.id,
            "ipfs-cluster-ctl --host /ip4/127.0.0.1/tcp/9094 --enc json peers ls",
        )
        if result.returncode:
            return False, f"{service.id} did not answer its Cluster membership probe"
        try:
            peers = _json_stream(result.stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            return False, f"{service.id} returned invalid Cluster membership JSON: {exc}"
        problem = _cluster_membership_problem(expected_names, peers)
        if problem:
            routes = []
            network = plan.raw["infrastructure"]["cluster"]["network"]
            for peer in cluster:
                if peer.id == service.id or peer.machine_id == service.machine_id:
                    continue
                address, port = p2p_endpoint(plan.machine(peer.machine_id), peer, network, plan.raw["infrastructure"]["cluster"]["p2p_port"])
                routes.append(f"{service.id}->{peer.id} {address}:{port}")
            suffix = "; expected P2P route(s): " + ", ".join(routes) if routes else ""
            return False, f"{service.id}: {problem}{suffix}"
    return True, f"IPFS Cluster converged with all declared peers: {', '.join(sorted(expected_names))}"


def check_phase(plan, project_root: Path, phase: str) -> tuple[bool, str]:
    from .state import run_root
    root = run_root(project_root, plan.deployment_id)
    effective = _effective_plan(plan, project_root, root)
    if phase == "validators":
        validators = [item for item in effective.services if item.type == "besu-validator"]
        for service in validators:
            result = resolve_executor(effective.machine(service.machine_id)).run((*_compose(effective, effective.machine(service.machine_id), root, service.id), "ps", "--status", "running", "--services"), timeout=20)
            if service.id not in result.stdout.split():
                return False, f"{service.id} is not running"
        return True, "all validators are running"
    if phase == "rpc":
        return _rpc(effective, root)
    if phase == "storage":
        kubo = [item for item in effective.services if item.type == "ipfs-kubo"]
        cluster = [item for item in effective.services if item.type == "ipfs-cluster"]
        for service in kubo:
            cmd = "ipfs --api /ip4/127.0.0.1/tcp/5001 id"
            result = _run(effective, effective.machine(service.machine_id), root, service.id, cmd)
            if result.returncode:
                return False, f"{service.id} did not answer its local peer probe"
        for consumer in cluster:
            for provider in cluster:
                if provider.machine_id == consumer.machine_id:
                    continue
                connection = {"service": provider.id, "network": plan.raw["infrastructure"]["cluster"]["network"]}
                endpoint = endpoint_for(provider, plan.machine(provider.machine_id), plan.machine(consumer.machine_id), connection)
                result = _curl(effective, effective.machine(consumer.machine_id), endpoint.url + "/id")
                if result.returncode:
                    return False, f"{consumer.id} cannot reach {provider.id} Cluster API at {endpoint.url}"
        return _cluster_topology(effective, root, cluster)
    if phase == "data":
        store = next(item for item in effective.services if item.type == "store-api")
        # Store's root is intentionally not a health endpoint (it returns
        # 404).  Probe the compatibility health aggregate, as the v2 runner
        # did, so a live Store is not misclassified during data readiness.
        result = store_health_probe(effective, effective.machine(store.machine_id), root, store)
        return result.returncode == 0, "Store API health is ready" if result.returncode == 0 else "Store API health did not respond"
    if phase == "applications":
        return True, "application containers started; final verify performs HTTP and worker probes"
    return True, "no readiness gate"


def wait_for_phase(plan, project_root: Path, phase: str, *, attempts: int = 60, interval: float = 2.0) -> str:
    last = "no probe result"
    for _ in range(attempts):
        ok, last = check_phase(plan, project_root, phase)
        if ok:
            return last
        time.sleep(interval)
    raise ReadinessError(f"{phase} readiness failed: {last}")
