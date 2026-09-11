"""Functional verification for a rendered deployment V3 graph."""

from __future__ import annotations

import json
from pathlib import Path

from .executor import resolve_executor
from .runner import _compose_directory, _compose_project, _effective_plan, _machine_directory
from .state import record, run_root, write_status
from .readiness import _host_url, _curl, ReadinessError


class VerifyError(RuntimeError):
    pass


def _compose(plan, machine, root, service_id=None):
    return ("docker", "compose", "--project-name", _compose_project(plan, machine, service_id), "-f", str(_compose_directory(plan, machine, root, service_id) / "compose.yaml"))


def _probe(plan, machine, root, service, command: str):
    return resolve_executor(machine).run((*_compose(plan, machine, root, service), "exec", "-T", service, "sh", "-lc", command), timeout=20)


def _store_health_url(store, machine) -> str:
    return _host_url(store, machine) + "/health?refresh=true"


def _append(report, service, name, result):
    entry = report["services"].setdefault(service.id, {"machine": service.machine_id, "ok": True})
    item = {"ok": result.returncode == 0, "output": result.stdout.strip()[:2000], "error": result.stderr.strip()[:500]}
    entry[name] = item
    entry["ok"] = bool(entry["ok"] and item["ok"])
    report["ok"] = bool(report["ok"] and item["ok"])


def verify(plan, project_root: Path) -> dict:
    root = run_root(project_root, plan.deployment_id)
    if not (root / "status.json").exists():
        raise VerifyError(f"no run status found: {root / 'status.json'}")
    effective = _effective_plan(plan, project_root, root)
    report = {"deployment_id": plan.deployment_id, "ok": True, "services": {}}
    one_shots = {"contracts-deploy", "minter-migrate", "dashboard-migrate", "rpc-probe"}
    for machine in effective.machines:
        groups = [group for group in effective.groups if group.machine_id == machine.id]
        if not groups:
            groups = [None]
        for group in groups:
            assigned = [item for item in effective.services if item.machine_id == machine.id and (group is None or item.id in group.service_ids)]
            if not assigned:
                continue
            service_hint = assigned[0].id if group else None
            result = resolve_executor(machine).run((*_compose(effective, machine, root, service_hint), "ps", "--format", "json"), timeout=30)
            states = {}
            if result.returncode == 0:
                try:
                    states = {json.loads(line).get("Service"): json.loads(line).get("State") for line in result.stdout.splitlines() if line.strip()}
                except json.JSONDecodeError:
                    result = type(result)(result.argv, 1, result.stdout, "invalid compose JSON")
            for service in assigned:
                ok = service.type in one_shots or states.get(service.id) == "running"
                report["services"][service.id] = {"machine": machine.id, "group": group.id if group else None, "state": states.get(service.id, "missing"), "ok": ok}
                report["ok"] = bool(report["ok"] and ok and result.returncode == 0)

    rpc = next(item for item in effective.services if item.type == "besu-rpc")
    rpc_machine = effective.machine(rpc.machine_id)
    chain_id = effective.raw["blockchain"]["chain_id"]
    rpc_url = _host_url(rpc, rpc_machine)
    result = _curl(effective, rpc_machine, rpc_url, '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}')
    _append(report, rpc, "chain_id", result)
    if result.returncode == 0 and f'"0x{chain_id:x}"' not in result.stdout.lower():
        report["services"][rpc.id]["chain_id"]["ok"] = False
        report["services"][rpc.id]["chain_id"]["error"] = f"unexpected chain ID; expected {chain_id}"
        report["ok"] = False
    _append(report, rpc, "validator_peers", _curl(effective, rpc_machine, rpc_url, '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}'))
    kubo_peers = [item for item in effective.services if item.type == "ipfs-kubo"]
    for service in kubo_peers:
        result = _probe(effective, effective.machine(service.machine_id), root, service.id, "ipfs --api /ip4/127.0.0.1/tcp/5001 swarm peers")
        require_peer = len(kubo_peers) > 1
        _append(report, service, "swarm", result)
        if result.returncode == 0 and require_peer and not result.stdout.strip():
            report["services"][service.id]["swarm"]["ok"] = False
            report["services"][service.id]["swarm"]["error"] = "Kubo did not report any swarm peers in a multi-peer cluster"
            report["ok"] = False
    cluster_peers = [item for item in effective.services if item.type == "ipfs-cluster"]
    for service in cluster_peers:
        result = _probe(effective, effective.machine(service.machine_id), root, service.id, "ipfs-cluster-ctl --host /ip4/127.0.0.1/tcp/9094 peers ls")
        _append(report, service, "cluster", result)
        if result.returncode == 0 and len(cluster_peers) > 1 and not result.stdout.strip():
            report["services"][service.id]["cluster"]["ok"] = False
            report["services"][service.id]["cluster"]["error"] = "IPFS Cluster did not report any peers in a multi-peer topology"
            report["ok"] = False
    store = next(item for item in effective.services if item.type == "store-api")
    store_machine = effective.machine(store.machine_id)
    # The Store root deliberately returns 404; verify its write-readiness
    # aggregate and refresh the Cluster peer observation, as v2 did.
    _append(report, store, "health", _curl(effective, store_machine, _store_health_url(store, store_machine)))
    worker_kind_cmd = {
        "metadata": "tr '\\0' ' ' < /proc/1/cmdline | grep -q metadata",
        "replication": "tr '\\0' ' ' < /proc/1/cmdline | grep -q replication",
        "chain": "tr '\\0' ' ' < /proc/1/cmdline | grep -q chain",
    }
    for service in (item for item in effective.services if item.type == "minter-worker"):
        worker = service.configuration.get("worker", "")
        cmd = worker_kind_cmd.get(worker, "true")
        _append(report, service, "heartbeat", _probe(effective, effective.machine(service.machine_id), root, service.id, cmd))
    proxy = next((item for item in effective.services if item.type == "edge-proxy"), None)
    if proxy:
        proxy_machine = effective.machine(proxy.machine_id)
        _append(report, proxy, "http", _curl(effective, proxy_machine, _host_url(proxy, proxy_machine)))
        _append(report, proxy, "explorer", _curl(effective, proxy_machine, _host_url(proxy, proxy_machine) + "/explorer/"))

    status = json.loads((root / "status.json").read_text())
    status["verification"] = report
    status["state"] = "verified" if report["ok"] else "verification_failed"
    write_status(root, status)
    record(root, {"state": status["state"], "verification_ok": report["ok"]})
    return report
