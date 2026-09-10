#!/usr/bin/env python3
"""Remove containers and volumes for central dark-deployer Compose projects."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def stacks() -> dict[str, Path]:
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    production = "TYPE=production" in env_text
    return {
        "dark-apps": ROOT / ("compose/apps-production.yml" if production else "compose/apps.yml"),
        "dark-blockchain-a": ROOT / ("compose/blockchain-production.yml" if production else "compose/blockchain-a.yml"),
    }


def down(project: str, compose: Path, env_file: Path | None = None) -> bool:
    if not compose.exists():
        return True
    command = ["docker", "compose", "--project-name", project]
    if env_file:
        command += ["--env-file", str(env_file)]
    command += ["-f", str(compose), "down", "--volumes"]
    print("[RUN]", " ".join(command))
    return subprocess.run(command, cwd=ROOT).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--sudo", action="store_true")
    args = parser.parse_args()
    if not args.yes and input("Remove central containers and volumes? [y/N] ").strip().lower() not in {"y", "yes"}:
        return 0
    failures = []
    for project, compose in stacks().items():
        if not down(project, compose):
            failures.append(project)
    env_text = (ROOT / ".env").read_text(errors="ignore") if (ROOT / ".env").exists() else ""
    storage = ROOT / ("compose/storage-production.yml" if "TYPE=production" in env_text else "compose/storage.yml")
    for env_file in sorted((ROOT / "components/dark-ipfs").glob(".env.node*")):
        project = f"dark-storage-{env_file.name.removeprefix('.env.node.') or 'storage'}"
        if not down(project, storage, env_file):
            failures.append(project)
    generated = ROOT / ".generated"
    if generated.exists():
        shutil.rmtree(generated)
    if failures:
        print("[ERROR] Failed:", ", ".join(failures))
        return 1
    print("[OK] Central Docker projects and generated state removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
