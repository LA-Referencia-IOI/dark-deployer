#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

def run_shell(cmd: str, cwd: str = None) -> None:
    print(f"[RUN] {cmd} in {cwd if cwd else 'current dir'}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")

def main():
    print("=== Stopping Blockchain Infrastructure ===\n")
    
    components_to_stop = [
        # It's usually a good practice to stop services before the core infrastructure (blockchain/ipfs)
        "components/frontend/dashboard-web",
        "components/services/dark-core-admin-api",
        "components/services/dark-core-resolver-api",
        "components/services/dark-store-api",
        "components/services/dark-core-minter-api",
        "components/blockchain/dark-ipfs",
        "components/blockchain/dark-explorador",
        "components/blockchain/dark-env"
    ]

    for rel_path in components_to_stop:
        target = Path(rel_path).resolve()
        if target.exists():
            # Use 'stop' to gracefully stop containers without removing them.
            # If you want to remove them, change this to 'docker compose down'
            run_shell("docker compose stop", cwd=str(target))
        else:
            print(f"[SKIP] {rel_path} not found. skipping...")

    print("\n=== Stop complete ===")

if __name__ == "__main__":
    main()
