#!/usr/bin/env python3
"""Monitor dARK Docker containers and worker log health.

The monitor is intentionally independent of the application and uses only the
Docker CLI. It emits one JSON record per sample, making the output suitable for
``jq``, spreadsheets, or later incident analysis.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
import subprocess
import time
from datetime import datetime, timezone


WORKER_MARKERS = ("minter-metadata-worker", "minter-replication-worker", "minter-chain-worker")
LOG_LEVELS = ("ERROR", "WARNING", "WARN", "Traceback", "Timeout", "failed", "paused")


def run(command: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)


def http_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 5.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        return {"ok": True, "status": response.status, "body": json.loads(body)}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def docker_ps() -> tuple[list[dict], str | None]:
    code, out, err = run(["docker", "ps", "-a", "--format", "{{json .}}"])
    if code != 0:
        return [], err or f"docker ps exited with {code}"
    rows = []
    for line in out.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = item.get("Names", "")
        if name.startswith("dark-") or name == "explorer-lite":
            rows.append({"name": name, "state": item.get("State"), "status": item.get("Status")})
    return rows, None


def docker_stats() -> tuple[list[dict], str | None]:
    fmt = "{{json .}}"
    code, out, err = run(["docker", "stats", "--no-stream", "--format", fmt], timeout=10)
    if code != 0:
        return [], err or f"docker stats exited with {code}"
    rows = []
    for line in out.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("Name", "").startswith("dark-") or item.get("Name") == "explorer-lite":
            rows.append(item)
    return rows, None


def worker_log_summary(since: str) -> dict[str, dict]:
    containers, _ = docker_ps()
    summary = {}
    for container in containers:
        name = container["name"]
        if not any(marker in name for marker in WORKER_MARKERS):
            continue
        code, out, err = run(["docker", "logs", "--since", since, "--tail", "200", name], timeout=8)
        text = out + (f"\n[stderr] {err}" if err else "")
        counts = {level: sum(level.lower() in line.lower() for line in text.splitlines()) for level in LOG_LEVELS}
        summary[name] = {"exit_code": code, "lines": len(text.splitlines()), "signals": counts}
    return summary


def sample() -> dict:
    return sample_with_detail(include_full=False, log_since="60s")


def sample_with_detail(*, include_full: bool, log_since: str = "60s") -> dict:
    ps, ps_error = docker_ps()
    stats, stats_error = docker_stats()
    now = datetime.now(timezone.utc).isoformat()
    rpc = http_json(
        "http://127.0.0.1:8545",
        method="POST",
        payload={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
    )
    txpool = http_json(
        "http://127.0.0.1:8545",
        method="POST",
        payload={"jsonrpc": "2.0", "method": "txpool_besuStatistics", "params": [], "id": 2},
    )
    minter_health = http_json("http://127.0.0.1:8001/health")
    worker_lite = http_json("http://127.0.0.1:8001/api/v1/worker/status")
    store_health = http_json("http://127.0.0.1:8003/health")
    external = {
        "rpc": rpc,
        "txpool": txpool,
        "minter_health": minter_health,
        "worker_lite": worker_lite,
        "store_health": store_health,
    }
    if include_full:
        external["worker_full"] = http_json("http://127.0.0.1:8001/api/v1/worker/status?detail=full", timeout=15)
    return {
        "timestamp": now,
        "containers": ps,
        "stats": stats,
        "worker_logs": worker_log_summary(log_since),
        "external": external,
        "errors": {"ps": ps_error, "stats": stats_error},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between samples")
    parser.add_argument("--full-every", type=float, default=300.0, help="Seconds between expensive full status probes")
    parser.add_argument("--output", default="dark-runtime-monitor.jsonl", help="JSONL output path; '-' prints only")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")

    if args.full_every <= 0:
        parser.error("--full-every must be positive")
    stream = None if args.output == "-" else open(args.output, "a", encoding="utf-8")
    last_full = 0.0
    try:
        while True:
            now_monotonic = time.monotonic()
            include_full = now_monotonic - last_full >= args.full_every
            record = sample_with_detail(include_full=include_full, log_since=f"{max(args.interval, 1):g}s")
            if include_full:
                last_full = now_monotonic
            line = json.dumps(record, ensure_ascii=False)
            print(line, flush=True)
            if stream:
                stream.write(line + "\n")
                stream.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if stream:
            stream.close()


if __name__ == "__main__":
    main()
