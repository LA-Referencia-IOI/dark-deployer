#!/usr/bin/env python3
"""Low-impact monitor for the centralized dARK developer/production layout.

Emits one JSON object per sample.  It observes Docker metadata/stats and the
cheap service endpoints; the expensive minter diagnostic is sampled less often.
It never starts, stops, recreates, or changes a container.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HTTP_ENDPOINTS = {
    "admin": "http://127.0.0.1:8000/health",
    "minter": "http://127.0.0.1:8001/health",
    "resolver": "http://127.0.0.1:8002/health",
    "store": "http://127.0.0.1:8003/health",
}
CLUSTER_ENDPOINTS = {
    "storage_1": "http://127.0.0.1:9094/peers",
    "storage_2": "http://127.0.0.1:9194/peers",
}


def run(args: list[str], timeout: float = 8) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, "", str(exc)


def get_json(url: str, *, payload: dict | None = None, timeout: float = 5) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode()
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                # Cluster REST streams /peers as newline-delimited JSON.
                lines = [line for line in raw.splitlines() if line.strip()]
                body = [json.loads(line) for line in lines]
            return {"ok": True, "status": response.status, "body": body}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def containers() -> list[dict]:
    code, out, _ = run(["docker", "ps", "-a", "--format", "{{json .}}"])
    if code:
        return []
    result = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = row.get("Names", "")
        if name.startswith(("dark-", "dashboard-")) or name == "explorer-lite":
            inspect_code, inspect_out, _ = run([
                "docker", "inspect", "-f",
                "{{json .Config.Labels}}|{{json .NetworkSettings.Networks}}|{{.State.Health.Status}}",
                name,
            ])
            project, networks, health = None, {}, None
            if not inspect_code:
                parts = inspect_out.strip().split("|", 2)
                try:
                    labels = json.loads(parts[0]); project = labels.get("com.docker.compose.project")
                    networks = list(json.loads(parts[1]).keys())
                    health = parts[2] or None
                except (IndexError, TypeError, json.JSONDecodeError):
                    pass
            result.append({"name": name, "state": row.get("State"), "status": row.get("Status"),
                           "compose_project": project, "networks": networks, "health": health})
    return result


def stats() -> list[dict]:
    code, out, _ = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=12)
    if code:
        return []
    rows = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("Name", "").startswith(("dark-", "dashboard-")) or row.get("Name") == "explorer-lite":
            rows.append({k: row.get(k) for k in ("Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO")})
    return rows


def sample(full: bool) -> dict:
    worker = get_json("http://127.0.0.1:8001/api/v1/worker/status", timeout=5)
    external = {name: get_json(url) for name, url in HTTP_ENDPOINTS.items()}
    external["workers_lite"] = worker
    clusters = {name: get_json(url) for name, url in CLUSTER_ENDPOINTS.items()}
    external["rpc"] = get_json("http://127.0.0.1:8545", payload={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
    external["txpool"] = get_json("http://127.0.0.1:8545", payload={"jsonrpc": "2.0", "id": 2, "method": "txpool_besuStatistics", "params": []})
    if full:
        external["worker_full"] = get_json("http://127.0.0.1:8001/api/v1/worker/status?detail=full", timeout=15)
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "containers": containers(),
            "docker_stats": stats(), "services": external, "cluster_peers": clusters}


def rates(record: dict, previous: dict | None) -> dict:
    """Derive bounded worker rates from cheap heartbeat counters."""
    if previous is None:
        return {}
    try:
        elapsed = max(
            (datetime.fromisoformat(record["timestamp"]) - datetime.fromisoformat(previous["timestamp"])).total_seconds(),
            0.001,
        )
        current = record["services"]["workers_lite"]["body"]["workers"]
        earlier = previous["services"]["workers_lite"]["body"]["workers"]
    except (KeyError, TypeError, ValueError):
        return {}
    result = {"sample_seconds": round(elapsed, 3), "workers": {}}
    for name, worker in current.items():
        before = earlier.get(name, {})
        totals, old = worker.get("totals", {}), before.get("totals", {})
        if not totals or not old:
            continue
        result["workers"][name] = {
            key + "_per_second": round(max(0, int(totals.get(key, 0)) - int(old.get(key, 0))) / elapsed, 3)
            for key in ("processed", "advanced", "failed")
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=60, help="seconds between samples")
    parser.add_argument("--full-every", type=float, default=300, help="seconds between full worker diagnostics")
    parser.add_argument("--output", default="dark-infrastructure-monitor.jsonl")
    args = parser.parse_args()
    if args.interval <= 0 or args.full_every <= 0:
        parser.error("--interval and --full-every must be positive")
    stream = None if args.output == "-" else open(args.output, "a", encoding="utf-8")
    last_full = 0.0
    previous = None
    try:
        while True:
            now = time.monotonic(); full = now - last_full >= args.full_every
            item = sample(full)
            item["rates"] = rates(item, previous)
            previous = item
            record = json.dumps(item, ensure_ascii=False)
            if full: last_full = now
            print(record, flush=True)
            if stream: stream.write(record + "\n"); stream.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if stream: stream.close()


if __name__ == "__main__":
    main()
