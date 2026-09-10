"""Read-only verification for a rendered deployment v2 run."""

from __future__ import annotations

import json
from pathlib import Path

from .executor import SshExecutor, resolve_executor
from .model import DeploymentPlan
from .runner import _effective_plan_for_apply
from .state import record, run_root, write_status


class VerifyError(RuntimeError):
    """Live deployment evidence is missing or unhealthy."""


def _group_directory(plan: DeploymentPlan, group, root: Path) -> Path:
    machine = plan.machine(group.machine_id)
    if isinstance(resolve_executor(machine), SshExecutor):
        return Path(machine.workspace_root) / ".generated" / "deployment-v2" / root.name / "groups" / group.id
    return root / "local" / machine.id / "groups" / group.id


def _expected(group) -> set[str]:
    if group.kind == "storage":
        return {"ipfs", "cluster"}
    if group.kind == "validators":
        return set(group.members) | ({"explorer"} if group.explorer else set())
    return {"blockchain-rpc", "postgres", "admin-api", "resolver-api", "store-api", "minter-api", "minter-metadata-worker", "minter-replication-worker", "minter-chain-worker", "dashboard-mysql", "dashboard-redis", "dashboard"}


def verify(plan: DeploymentPlan, project_root: Path) -> dict:
    """Inspect Compose runtime state without changing containers or data."""
    root = run_root(project_root, plan.deployment_id)
    status_path = root / "status.json"
    if not status_path.exists():
        raise VerifyError(f"no run status found: {status_path}")
    effective = _effective_plan_for_apply(plan, project_root, root, stage_sources=False)
    report: dict[str, object] = {"deployment_id": plan.deployment_id, "ok": True, "groups": {}}
    for group in effective.groups:
        machine = effective.machine(group.machine_id)
        executor = resolve_executor(machine)
        directory = _group_directory(effective, group, root)
        command = ("docker", "compose", "--project-name", f"{plan.deployment_id}-{group.id}", "-f", str(directory / "compose.yaml"), "ps", "--format", "json")
        result = executor.run(command, timeout=30.0)
        entries: list[dict] = []
        if result.returncode == 0:
            payload = result.stdout.strip()
            try:
                if payload.startswith("["):
                    entries.extend(json.loads(payload))
                else:
                    for line in payload.splitlines():
                        if line.strip():
                            entries.append(json.loads(line))
            except json.JSONDecodeError:
                result = type(result)(result.argv, 1, result.stdout, "docker compose returned invalid JSON")
        states = {entry.get("Service"): entry.get("State") for entry in entries}
        missing = sorted(name for name in _expected(group) if states.get(name) != "running")
        group_report = {"services": states, "missing_or_not_running": missing, "ok": result.returncode == 0 and not missing}
        report["groups"][group.id] = group_report  # type: ignore[index]
        if not group_report["ok"]:
            report["ok"] = False
    if report["ok"]:
        apps = effective.group("apps")
        machine = effective.machine(apps.machine_id)
        executor = resolve_executor(machine)
        directory = _group_directory(effective, apps, root)
        base = ("docker", "compose", "--project-name", f"{plan.deployment_id}-apps", "-f", str(directory / "compose.yaml"), "exec", "-T")
        probes = {
            "rpc": (*base, "minter-api", "python", "-c", "import urllib.request; request=urllib.request.Request('http://blockchain-rpc:8545', data=b'{\"jsonrpc\":\"2.0\",\"method\":\"eth_chainId\",\"params\":[],\"id\":1}', headers={'Content-Type':'application/json'}); urllib.request.urlopen(request, timeout=5).read()"),
            "minter": (*base, "minter-api", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/live', timeout=5).read()"),
            "store": (*base, "store-api", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health/live', timeout=5).read()"),
        }
        probe_report = {name: executor.run(command, timeout=15.0).returncode == 0 for name, command in probes.items()}
        report["probes"] = probe_report
        report["ok"] = all(probe_report.values())
    status = json.loads(status_path.read_text())
    status["verification"] = report
    status["state"] = "verified" if report["ok"] else "verification_failed"
    write_status(root, status)
    record(root, {"state": status["state"], "verification_ok": report["ok"]})
    return report
