#!/usr/bin/env python3
"""Remove dARK containers, volumes, generated components, and virtualenvs."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
COMPONENTS = [
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


def run_command(args: list[str], cwd: Path | None = None) -> bool:
    location = str(cwd) if cwd else str(PROJECT_ROOT)
    print(f"[RUN] {' '.join(args)} in {location}")
    result = subprocess.run(args, cwd=str(cwd or PROJECT_ROOT))
    if result.returncode != 0:
        print(f"[ERROR] Command failed with exit code {result.returncode}: {' '.join(args)}")
        return False
    return True


def clean_docker_stacks() -> list[str]:
    failures = []
    print("=== Removing Docker containers and volumes ===\n")
    for relative_path in COMPONENTS:
        target = PROJECT_ROOT / relative_path
        if not target.exists() or not has_compose_file(target):
            print(f"[SKIP] {relative_path} is not an installed Compose stack.")
            continue
        if not run_command(["docker", "compose", "down", "--volumes"], target):
            failures.append(relative_path)
    return failures


def remove_generated_directories(allow_sudo: bool) -> list[str]:
    failures = []
    print("\n=== Removing generated directories ===\n")
    for target in (PROJECT_ROOT / "venv", PROJECT_ROOT / "components"):
        if not target.exists():
            print(f"[SKIP] {target} does not exist.")
            continue
        try:
            shutil.rmtree(target)
            print(f"[OK] Removed {target}")
        except PermissionError:
            if allow_sudo and run_command(["sudo", "rm", "-rf", "--", str(target)]):
                print(f"[OK] Removed {target} with sudo")
            else:
                print(f"[ERROR] Permission denied removing {target}")
                failures.append(str(target))
        except OSError as exc:
            print(f"[ERROR] Could not remove {target}: {exc}")
            failures.append(str(target))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Skip the destructive confirmation prompt.")
    parser.add_argument("--sudo", action="store_true", help="Allow sudo fallback for root-owned generated files.")
    args = parser.parse_args()

    if not args.yes:
        try:
            answer = input(
                "This removes all dARK containers, volumes, components, and the shared venv. "
                "Continue? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO] Cleanup cancelled.")
            return 0
        if answer not in {"y", "yes"}:
            print("[INFO] Cleanup cancelled.")
            return 0

    failures = clean_docker_stacks()
    failures.extend(remove_generated_directories(args.sudo))

    network = subprocess.run(
        ["docker", "network", "inspect", "dark-net"],
        capture_output=True,
    )
    if network.returncode == 0 and not run_command(["docker", "network", "rm", "dark-net"]):
        failures.append("docker network dark-net")

    if failures:
        print(f"\n[ERROR] Cleanup incomplete: {', '.join(failures)}")
        return 1
    print("\n=== System is clean ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
