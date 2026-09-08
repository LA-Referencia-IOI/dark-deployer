#!/usr/bin/env python3
"""Restart central dark-deployer Compose projects."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def stacks() -> dict[str, Path]:
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    production = "TYPE=production" in env_text
    return {
        "dark-blockchain-a": ROOT / ("compose/blockchain-production.yml" if production else "compose/blockchain-a.yml"),
        "dark-apps": ROOT / ("compose/apps-production.yml" if production else "compose/apps.yml"),
    }


def main() -> int:
    failures = []
    for project, compose in stacks().items():
        if not compose.exists():
            continue
        command = ["docker", "compose", "--project-name", project, "--env-file", str(ROOT / ".env"), "-f", str(compose), "up", "-d", "--build"]
        print("[RUN]", " ".join(command))
        if subprocess.run(command, cwd=ROOT).returncode != 0:
            failures.append(project)
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    storage_compose = ROOT / ("compose/storage-production.yml" if "TYPE=production" in env_text else "compose/storage.yml")
    for env_file in sorted((ROOT / "components/storage/dark-ipfs").glob(".env.node*")):
        project = f"dark-storage-{env_file.name.removeprefix('.env.node.') or 'storage'}"
        command = ["docker", "compose", "--project-name", project, "--env-file", str(env_file), "-f", str(storage_compose), "up", "-d", "--build"]
        print("[RUN]", " ".join(command))
        if subprocess.run(command, cwd=ROOT).returncode != 0:
            failures.append(project)
    if failures:
        print("[ERROR] Failed:", ", ".join(failures))
        return 1
    print("[OK] Central Docker projects restarted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
