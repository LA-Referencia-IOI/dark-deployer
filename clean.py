#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run_shell(cmd: str, cwd: str = None) -> int:
    """Execute a shell command and return its exit code."""
    print(f"[RUN] {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, check=False)
    except Exception as exc:
        print(f"[ERROR] Could not run command {cmd}: {exc}")
        return 1

    if result.returncode != 0:
        print(f"[WARNING] Command failed with exit code {result.returncode}: {cmd}")
    return result.returncode


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Stop local containers and remove the components directory.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    return parser.parse_args()


def confirm_cleanup(auto_confirm: bool) -> bool:
    """Ask for confirmation unless --yes was provided."""
    if auto_confirm:
        return True

    try:
        confirm = input(
            "!!! WARNING: This will STOP all containers and DELETE all components. "
            "Are you sure? (y/N): "
        )
    except EOFError:
        print("[ABORT] No interactive input available. Re-run with --yes to confirm.")
        return False

    return confirm.lower() == "y"


def main() -> None:
    args = parse_args()
    base_path = Path("components")

    if not base_path.exists():
        print("[INFO] 'components/' directory does not exist. Nothing to clean.")
        return

    if not confirm_cleanup(auto_confirm=args.yes):
        print("[ABORT] Cleaning cancelled.")
        sys.exit(0)

    failures: list[str] = []

    blockchain_paths = [
        base_path / "blockchain" / "dark-env",
        base_path / "blockchain" / "dark-explorador",
    ]

    print("\n--- Stopping Containers ---")
    for path in blockchain_paths:
        if path.exists():
            print(f"[INFO] Stopping containers in {path}...")
            rc = run_shell("docker compose down", cwd=str(path.resolve()))
            if rc != 0:
                failures.append(f"docker compose down failed in {path} (exit code {rc})")
        else:
            print(f"[SKIP] Path not found: {path}")

    print("\n--- Removing Files ---")
    try:
        print(f"[INFO] Deleting directory: {base_path.resolve()}")
        shutil.rmtree(base_path)
        print("[OK] All components have been removed.")
    except Exception as exc:
        print(f"[ERROR] Failed to delete directory: {exc}")
        sys.exit(1)

    if failures:
        print("\n[WARNING] Cleanup finished with some issues:")
        for failure in failures:
            print(f"- {failure}")
        sys.exit(1)

    print("[OK] Containers stopped and components cleaned successfully.")


if __name__ == "__main__":
    main()
