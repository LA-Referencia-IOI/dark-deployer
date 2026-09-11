"""Functional readiness gates used between declarative deployment phases."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .executor import resolve_executor
from .runner import _compose_directory, _compose_project, _effective_plan, _machine_directory


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
    if mode == "public":
        return f"http://127.0.0.1:{port}"
    return f"http://127.0.0.1:{port}" if mode == "loopback" else f"http://{machine.address_on(service.exposure['network'])}:{port}"


def _curl(plan, machine, url, payload: str | None = None):
    argv = ["curl", "-fsS", "--max-time", "5"]
    if payload is not None:
        argv.extend(["-H", "Content-Type: application/json", "--data", payload])
    argv.append(url)
    return resolve_executor(machine).run(tuple(argv), timeout=15)


def _rpc(plan, root):
    rpc = next(item for item in plan.services if item.type == "besu-rpc")
    machine = plan.machine(rpc.machine_id)
    url = _host_url(rpc, machine)
    result = _curl(plan, machine, url, '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}')
    if result.returncode:
        return False, result.stderr.strip() or result.stdout.strip()
    try:
        peers = int(json.loads(result.stdout)["result"], 16)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False, "RPC returned invalid net_peerCount response"
    return peers >= 4, f"RPC peers={peers}; expected at least 4"


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
        for service in [*kubo, *cluster]:
            cmd = "ipfs --api /ip4/127.0.0.1/tcp/5001 id" if service.type == "ipfs-kubo" else "ipfs-cluster-ctl --host /ip4/127.0.0.1/tcp/9094 id"
            result = _run(effective, effective.machine(service.machine_id), root, service.id, cmd)
            if result.returncode:
                return False, f"{service.id} did not answer its local peer probe"
        return True, "Kubo and Cluster peers answer local probes"
    if phase == "data":
        store = next(item for item in effective.services if item.type == "store-api")
        # Store's root is intentionally not a health endpoint (it returns
        # 404).  Probe the compatibility health aggregate, as the v2 runner
        # did, so a live Store is not misclassified during data readiness.
        result = _curl(effective, effective.machine(store.machine_id), _host_url(store, effective.machine(store.machine_id)) + "/health")
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
