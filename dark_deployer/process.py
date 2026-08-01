"""Shell and argv process execution helpers."""

import os
import re
import shlex
import subprocess
import sys


def redact_url_credentials(value: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", value)


def strip_url_credentials(value: str) -> str:
    """Remove HTTP(S) user-info before persisting a repository URL."""
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", value)


def run_shell(
    cmd: str,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    compose_plain: bool = False,
) -> None:
    print(f"[RUN] {cmd}")
    run_env = None
    if compose_plain:
        run_env = os.environ.copy()
        run_env.setdefault("COMPOSE_PROGRESS", "plain")
        run_env.setdefault("BUILDKIT_PROGRESS", "plain")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            env=run_env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        print(f"[ERROR] Command timed out after {timeout_seconds}s: {cmd}")
        sys.exit(124)
    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")
        sys.exit(result.returncode)


def run_command(
    args: list[str],
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    display_command = shlex.join([redact_url_credentials(arg) for arg in args])
    print(f"[RUN] {display_command}")
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            timeout=timeout_seconds,
            capture_output=capture_output,
            text=capture_output,
        )
    except subprocess.TimeoutExpired:
        print(f"[ERROR] Command timed out after {timeout_seconds}s: {display_command}")
        sys.exit(124)
    if result.returncode != 0:
        if capture_output:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                print(f"[ERROR] {redact_url_credentials(detail)}")
        print(f"[ERROR] Command failed: {display_command}")
        sys.exit(result.returncode)
    return result
