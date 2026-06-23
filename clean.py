#!/usr/bin/env python3
"""
dARK 2.0 - Clean Script
Stops all containers, removes their volumes, and deletes the components/ and venv/ folders.
"""
import subprocess
import shutil
from pathlib import Path

def run_shell(cmd: str, cwd: str = None) -> None:
    print(f"[RUN] {cmd} in {cwd if cwd else 'current dir'}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")

def clean_docker_containers():
    print("=== Removing Docker Containers & Volumes ===\n")
    components = [
        "components/frontend/dashboard-web",
        "components/services/dark-core-admin-api",
        "components/services/dark-core-resolver-api",
        "components/services/dark-store-api",
        "components/services/dark-core-minter-api",
        "components/blockchain/dark-ipfs",
        "components/blockchain/dark-explorador",
        "components/blockchain/dark-env"
    ]

    for rel_path in components:
        target = Path(rel_path).resolve()
        if target.exists() and (target / "docker-compose.yml").exists():
            # Use 'down -v' to stop containers and remove volumes.
            run_shell("docker compose down -v", cwd=str(target))
        else:
            print(f"[SKIP] {rel_path} or its docker-compose.yml not found. skipping...")

def clean_directories():
    print("\n=== Removing Directories ===\n")
    project_root = Path(__file__).resolve().parent
    
    components_dir = project_root / "components"
    venv_dir = project_root / "venv"

    for d in [venv_dir, components_dir]:
        if d.exists():
            print(f"[INFO] Deleting directory: {d}")
            try:
                shutil.rmtree(d)
                print(f"[OK] {d} deleted successfully.")
            except PermissionError:
                print(f"[WARNING] Permission denied deleting {d} (likely Docker root files). Falling back to sudo rm -rf...")
                run_shell(f"sudo rm -rf {d}")
                if d.exists():
                    print(f"[ERROR] Failed to delete {d} even with sudo.")
                else:
                    print(f"[OK] {d} deleted successfully with sudo.")
            except Exception as e:
                print(f"[ERROR] Error deleting {d}: {e}")
        else:
            print(f"[SKIP] Directory {d} does not exist.")
def main():
    print("=== dARK 2.0 Full Cleanup ===\n")
    
    clean_docker_containers()
    clean_directories()
    
    # Remove the shared docker network if it exists
    run_shell("docker network rm dark-net 2>/dev/null || true")
    
    print("\n=== System is clean! ===")

if __name__ == "__main__":
    main()