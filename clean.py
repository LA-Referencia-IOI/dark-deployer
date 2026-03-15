#!/usr/bin/env python3
import subprocess
import shutil
import sys
from pathlib import Path

def run_shell(cmd: str, cwd: str = None) -> None:
    """Executes a shell command but handles errors gracefully during cleanup."""
    print(f"[RUN] {cmd}")
    try:
        subprocess.run(cmd, shell=True, cwd=cwd, check=False)
    except Exception as e:
        print(f"[WARNING] Could not run command {cmd}: {e}")

def main():
    base_path = Path("components")
    
    if not base_path.exists():
        print("[INFO] 'components/' directory does not exist. Nothing to clean.")
        return

    # Security confirmation
    confirm = input("!!! WARNING: This will STOP all containers and DELETE all components. Are you sure? (y/N): ")
    if confirm.lower() != 'y':
        print("[ABORT] Cleaning cancelled.")
        sys.exit(0)

    # 1. Stop and remove containers
    blockchain_paths = [
        base_path / "blockchain" / "dark-env",
        base_path / "blockchain" / "dark-explorador"
    ]

    print("\n--- Stopping Containers ---")
    for path in blockchain_paths:
        if path.exists():
            print(f"[INFO] Stopping containers in {path}...")
            # 'down' removes containers and networks defined in the compose file
            run_shell("docker compose down", cwd=str(path.resolve()))
        else:
            print(f"[SKIP] Path not found: {path}")

    # 2. Remove the components directory
    print("\n--- Removing Files ---")
    try:
        print(f"[INFO] Deleting directory: {base_path.resolve()}")
        # We use shutil.rmtree for a recursive and complete deletion
        shutil.rmtree(base_path)
        print("[OK] All components and containers have been cleaned.")
    except Exception as e:
        print(f"[ERROR] Failed to delete directory: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()