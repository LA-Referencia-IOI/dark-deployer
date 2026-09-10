#!/usr/bin/env python3
"""Stop Docker projects rendered and owned by dark-deployer."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def stacks() -> dict[str, Path]:
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    production = "TYPE=production" in env_text
    suffix = "-production" if production else ""
    return {
        "dark-apps": ROOT / f"compose/apps{suffix}.yml",
        "dark-blockchain-a": ROOT / f"compose/blockchain{suffix}-a.yml"
        if not production else ROOT / "compose/blockchain-production.yml",
    }


def stop(project: str, compose: Path, env_file: Path | None = None) -> bool:
    if not compose.exists():
        return True
    command = ["docker", "compose", "--project-name", project]
    if env_file:
        command += ["--env-file", str(env_file)]
    command += ["-f", str(compose), "stop"]
    print("[RUN]", " ".join(command))
    return subprocess.run(command, cwd=ROOT).returncode == 0


def main() -> int:
    failures = []
    for project, compose in stacks().items():
        if not stop(project, compose):
            failures.append(project)
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    storage = ROOT / ("compose/storage-production.yml" if "TYPE=production" in env_text else "compose/storage.yml")
    for env_file in sorted((ROOT / "components/dark-ipfs").glob(".env.node*")):
        project = f"dark-storage-{env_file.name.removeprefix('.env.node.') or 'storage'}"
        if not stop(project, storage, env_file):
            failures.append(project)
    if failures:
        print("[ERROR] Failed to stop:", ", ".join(failures))
        return 1
    print("[OK] Central Docker projects stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
