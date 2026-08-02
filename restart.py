#!/usr/bin/env python3
"""Start installed dARK stacks and verify their configured health endpoints."""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
STACKS = [
    ("components/blockchain/dark-env", None),
    ("components/blockchain/dark-explorador", None),
    ("components/storage/dark-ipfs", None),
    ("components/services/dark-core-admin-api", "ADMIN_API_BASE_URL"),
    ("components/services/dark-core-resolver-api", "RESOLVER_BASE_URL"),
    ("components/services/dark-store-api", "STORE_API_BASE_URL"),
    ("components/services/dark-core-minter-api", "MINTER_BASE_URL"),
    ("components/frontend/dashboard-web", None),
]
DEFAULT_ENDPOINTS = {
    "ADMIN_API_BASE_URL": "http://localhost:8000/health",
    "MINTER_BASE_URL": "http://localhost:8001/health",
    "RESOLVER_BASE_URL": "http://localhost:8002/health",
    "STORE_API_BASE_URL": "http://localhost:8003/health",
    "IPFS_API_BASE_URL": "http://localhost:5001/api/v0/version",
    "IPFS_CLUSTER_API_URL": "http://localhost:9094/id",
}


def load_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def has_compose_file(path: Path) -> bool:
    return any((path / name).exists() for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"))


def storage_env_files(path: Path) -> list[Path]:
    """Return every HA node file, or the legacy/simple node file."""
    ha_files = sorted(path.glob(".env.node.*"))
    if ha_files:
        return ha_files
    simple = path / ".env.node"
    return [simple] if simple.exists() else []


def wait_for_health(url: str, method: str = "GET", timeout: int = 60) -> bool:
    deadline = time.monotonic() + timeout
    last_error = "not ready"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(request, timeout=3) as response:
                if 200 <= response.status < 400:
                    print(f"[OK] {url} returned HTTP {response.status}")
                    return True
                last_error = f"HTTP {response.status}"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(2)
    print(f"[ERROR] {url} did not become healthy: {last_error}")
    return False


def main() -> int:
    print("=== Restarting dARK infrastructure ===\n")
    env = load_env(PROJECT_ROOT / ".env")
    failures = []
    health_checks = []

    for relative_path, endpoint_key in STACKS:
        target = PROJECT_ROOT / relative_path
        if not target.exists():
            print(f"[SKIP] {relative_path} is not installed.")
            continue
        if not has_compose_file(target):
            print(f"[SKIP] {relative_path} has no Compose file.")
            continue
        if relative_path == "components/storage/dark-ipfs":
            node_files = storage_env_files(target)
            if not node_files:
                print(f"[ERROR] No generated IPFS node environment found in {target}.")
                failures.append(relative_path)
                continue
            failed = False
            for node_file in node_files:
                command = ["make", "up", f"ENV_FILE={node_file.name}"]
                print(f"[RUN] {' '.join(command)} in {target}")
                result = subprocess.run(command, cwd=str(target))
                if result.returncode != 0:
                    failures.append(f"{relative_path}:{node_file.name}")
                    failed = True
                    continue
                node_env = load_env(node_file)
                bind_address = node_env.get("HOST_BIND_ADDRESS", "127.0.0.1")
                ipfs_port = node_env.get("IPFS_API_HOST_PORT", "5001")
                cluster_port = node_env.get("CLUSTER_REST_HOST_PORT", "9094")
                health_checks.extend([
                    (
                        f"{relative_path}:{node_file.name}",
                        f"http://{bind_address}:{ipfs_port}/api/v0/version",
                        "POST",
                    ),
                    (
                        f"{relative_path}:{node_file.name}",
                        f"http://{bind_address}:{cluster_port}/id",
                        "GET",
                    ),
                ])
            if failed:
                continue
        else:
            command = ["docker", "compose", "up", "-d"]
            print(f"[RUN] {' '.join(command)} in {target}")
            result = subprocess.run(command, cwd=str(target))
            if result.returncode != 0:
                failures.append(relative_path)
                continue
        if endpoint_key:
            configured = env.get(endpoint_key, "").rstrip("/")
            default = DEFAULT_ENDPOINTS[endpoint_key]
            url = (
                configured + "/health"
                if configured and not configured.endswith("/health")
                else configured or default
            )
            health_checks.append((relative_path, url, "GET"))

    for relative_path, url, method in health_checks:
        if not wait_for_health(url, method=method):
            failures.append(relative_path)

    if failures:
        print(f"\n[ERROR] Restart/health failures: {', '.join(sorted(set(failures)))}")
        return 1
    print("\n=== Restart complete and healthy ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
