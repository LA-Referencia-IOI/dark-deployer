#!/usr/bin/env python3
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

def run_shell(cmd: str, cwd: str = None) -> None:
    print(f"[RUN] {cmd} in {cwd if cwd else 'current dir'}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")

def test_endpoints():
    import time
    print("\n[INFO] Waiting 20 seconds for services to boot up...")
    time.sleep(20)
    
    endpoints = {
        "ADMIN_API_BASE_URL": ("http://localhost:8000/health", "GET"),
        "MINTER_BASE_URL": ("http://localhost:8001/health", "GET"),
        "RESOLVER_BASE_URL": ("http://localhost:8002/health", "GET"),
        "STORE_API_BASE_URL": ("http://localhost:8003/health", "GET"),
        "IPFS_API_BASE_URL": ("http://localhost:5001/api/v0/version", "POST"),
        "IPFS_CLUSTER_API_URL": ("http://localhost:9094/id", "GET")
    }
    
    print("\n=== Testing Endpoints ===")
    for name, (url, method) in endpoints.items():
        try:
            req = urllib.request.Request(url, method=method)
            with urllib.request.urlopen(req, timeout=3) as response:
                print(f"[{name}] {url} - Status: {response.status}")
        except urllib.error.HTTPError as e:
            print(f"[{name}] {url} - Status: {e.code} (Service is UP)")
        except urllib.error.URLError as e:
            print(f"[{name}] {url} - Failed to connect")
        except Exception as e:
            print(f"[{name}] {url} - Error: {e}")

def main():
    print("=== Restarting Blockchain Infrastructure ===\n")
    
    components_to_restart = [
        "components/blockchain/dark-env",
        "components/blockchain/dark-explorador",
        "components/storage/dark-ipfs",
        "components/services/dark-core-admin-api",
        "components/services/dark-core-resolver-api",
        "components/services/dark-store-api",
        "components/services/dark-core-minter-api"
    ]

    for rel_path in components_to_restart:
        target = Path(rel_path).resolve()
        if target.exists():
            # We use 'restart' to keep containers alive but refresh processes
            # or 'down && up -d' if you want a total reset.
            run_shell("docker compose up -d", cwd=str(target))
        else:
            print(f"[SKIP] {rel_path} not found. skipping...")

    print("\n=== Restart complete ===")
    test_endpoints()

if __name__ == "__main__":
    main()