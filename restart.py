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
    print("=== Restarting Blockchain Infrastructure ===\n")
    
    components_to_restart = [
        "components/blockchain/dark-env",
        "components/blockchain/dark-explorador"
    ]

    for rel_path in components_to_restart:
        target = Path(rel_path).resolve()
        if target.exists():
            # We use 'restart' to keep containers alive but refresh processes
            # or 'down && up -d' if you want a total reset.
            run_shell("docker compose restart", cwd=str(target))
        else:
            print(f"[SKIP] {rel_path} not found. skipping...")

    print("\n=== Restart complete ===")

if __name__ == "__main__":
    main()