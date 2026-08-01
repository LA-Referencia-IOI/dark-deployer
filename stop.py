#!/usr/bin/env python3
"""Gracefully stop every installed dARK Docker Compose stack."""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
COMPONENTS_TO_STOP = [
    "components/frontend/dashboard-web",
    "components/services/dark-core-admin-api",
    "components/services/dark-core-resolver-api",
    "components/services/dark-store-api",
    "components/services/dark-core-minter-api",
    "components/storage/dark-ipfs",
    "components/blockchain/dark-explorador",
    "components/blockchain/dark-env",
]


def has_compose_file(path: Path) -> bool:
    return any((path / name).exists() for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"))


def run_command(args: list[str], cwd: Path) -> bool:
    print(f"[RUN] {' '.join(args)} in {cwd}")
    result = subprocess.run(args, cwd=str(cwd))
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}: {' '.join(args)}")
        return False
    return True


def main() -> int:
    print("=== Stopping dARK infrastructure ===\n")
    failures = []

    for relative_path in COMPONENTS_TO_STOP:
        target = PROJECT_ROOT / relative_path
        if not target.exists():
            print(f"[SKIP] {relative_path} is not installed.")
            continue
        if not has_compose_file(target):
            print(f"[SKIP] {relative_path} has no Compose file.")
            continue
        if not run_command(["docker", "compose", "stop"], target):
            failures.append(relative_path)

    if failures:
        print(f"\n[ERROR] Failed to stop: {', '.join(failures)}")
        return 1
    print("\n=== Stop complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
