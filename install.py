#!/usr/bin/env python3
"""
dark-developer installer.

Reads the ``TYPE`` variable from the ``.env`` file and clones all
subcomponent repositories into the ``components/`` directory, then
runs the per-repo commands defined in the ``COMMANDS`` variables.

Usage::

    python install.py
    python install.py rebuild store-api

Supported profiles:
    - ``developer``  — installs the full developer infrastructure.
    - ``sandbox``    — installs the sandbox stack.
    - ``production`` — installs the production stack.

Components installed (per profile):
    - **blockchain** — three sub-repos: ``dark-env``, ``dark-dapp``, ``dark-explorador``
    - **dark-ipfs**
    - **core-lib**
    - **admin**
    - **store-api**
    - **resolver**
    - **minter**
    - **dashboard**

Command format in ``.env``::

    {PREFIX}_{COMPONENT}_COMMANDS=cmd1|cmd2|cmd3

Commands are separated by ``|`` and executed sequentially inside the
cloned repository directory. Shell syntax (globs, redirects) is supported.

Selective rebuilds::

    python install.py rebuild store-api
    python install.py rebuild minter
    python install.py rebuild minter --skip-migrate
    python install.py rebuild core-lib --with-dependents
"""

import argparse
import grp
import os
import platform
import shlex
import subprocess
import sys
import time
from pathlib import Path
import configparser
import re
from typing import Optional
import json
import http.client
import urllib.error
import urllib.request

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _ensure_docker_group() -> None:
    """Re-exec with the docker group if the user is a member but the current
    session doesn't have it active (common right after a fresh Docker install).

    sg docker sets the *effective* GID, not a supplementary group, so we check
    getgid()/getegid() in addition to getgroups(). A _DARK_DOCKER_REEXEC env
    flag prevents looping when sg still can't provide docker access.
    """
    if os.environ.get("_DARK_DOCKER_REEXEC"):
        return  # already tried once — avoid infinite loop
    try:
        docker_gid = grp.getgrnam("docker").gr_gid
    except KeyError:
        return  # docker group doesn't exist
    if docker_gid in (os.getgid(), os.getegid(), *os.getgroups()):
        return  # already active in this session
    cmd = " ".join(shlex.quote(a) for a in [sys.executable] + sys.argv)
    print("[INFO] docker group not active in this session — re-launching automatically...")
    os.environ["_DARK_DOCKER_REEXEC"] = "1"
    os.execvp("sg", ["sg", "docker", "-c", cmd])

PROJECT_ROOT = Path(__file__).resolve().parent
SHARED_VENV_DIR = PROJECT_ROOT / "venv"
MIN_PYTHON_VERSION = (3, 10)


def ensure_supported_python() -> None:
    """Fail early when the installer is run on an unsupported Python version."""
    if sys.version_info >= MIN_PYTHON_VERSION:
        return

    detected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    required = ".".join(str(part) for part in MIN_PYTHON_VERSION)
    print(
        "[ERROR] Unsupported Python runtime. "
        f"Detected Python {detected}, but dark-developer requires Python {required}+."
    )
    sys.exit(1)


def parse_env_file(filepath: Path, required: bool = True) -> dict:
    """Parse an env-style file and return its contents as a dictionary.

    Lines starting with ``#`` and empty lines are ignored.
    Only lines containing ``=`` are parsed.

    :param filepath: Path to the env-style file.
    :type filepath: Path
    :param required: Whether missing files should raise a fatal error.
    :type required: bool
    :returns: Dictionary mapping variable names to their values.
    :rtype: dict
    :raises SystemExit: If the file is required but does not exist.
    """
    env: dict = {}

    if not filepath.exists():
        if required:
            print(
                f"[ERROR] '{filepath}' not found. "
                "Copy .env.example to .env and fill in the values."
            )
            sys.exit(1)
        return env

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    return env


def load_env(filepath: str = ".env") -> dict:
    """Parse a required ``.env`` file and return its contents."""
    return parse_env_file(Path(filepath), required=True)


def load_optional_env(filepath: Path) -> dict:
    """Parse an optional env-style file, returning an empty dict if absent."""
    return parse_env_file(filepath, required=False)


def run_shell(
    cmd: str,
    cwd: str = None,
    timeout_seconds: int | None = None,
    compose_plain: bool = False,
) -> None:
    """Execute a single shell command string and exit the process on failure.

    The command is executed through the shell, so globs, pipes, and other
    shell syntax are supported within each individual command string.

    :param cmd: Shell command to execute.
    :type cmd: str
    :param cwd: Working directory for the command. Defaults to ``None``.
    :type cwd: str, optional
    :param timeout_seconds: Optional timeout for commands that should not run forever.
    :type timeout_seconds: int, optional
    :param compose_plain: Force non-interactive Docker Compose progress output.
    :type compose_plain: bool
    :raises SystemExit: If the command returns a non-zero exit code.
    """
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


def run_compose_up_detached(cmd: str, cwd: str, timeout_seconds: int = 180) -> None:
    """Run a detached Docker Compose startup without interactive progress output."""
    run_shell(
        cmd,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        compose_plain=True,
    )


def ensure_docker_running() -> None:
    """Fail early when Docker is not available, the daemon is stopped, or
    the Docker Compose v2 plugin is missing."""
    result = subprocess.run(
        "docker info",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
        print("[ERROR] Docker does not appear to be running or accessible.")
        print(f"[ERROR] {stderr}")
        if "permission denied" in stderr.lower():
            print()
            print("  If you were just added to the 'docker' group, log out and back in,")
            print("  or run:  newgrp docker  — then retry.")
        sys.exit(1)

    compose_result = subprocess.run(
        "docker compose version",
        shell=True,
        capture_output=True,
        text=True,
    )
    if compose_result.returncode != 0:
        print("[ERROR] Docker Compose v2 plugin not found.")
        print("[ERROR] Install it with:")
        print("        sudo apt-get install docker-compose-plugin")
        print("        # or follow https://docs.docker.com/compose/install/")
        sys.exit(1)


def wait_for_rpc(
    rpc_url: str,
    timeout_seconds: int = 120,
    poll_interval: float = 2.0,
) -> None:
    """Wait until the configured JSON-RPC endpoint responds successfully."""
    print(f"[INFO] Waiting for blockchain RPC at '{rpc_url}'...")
    deadline = time.time() + timeout_seconds
    last_error = "RPC not ready yet"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1,
    }

    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                rpc_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
            block_number = body.get("result")
            if block_number and not body.get("error"):
                print(f"[OK] Blockchain RPC is ready at block {block_number}.")
                return
            last_error = f"Unexpected RPC response: {body}"
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            last_error = str(exc)

        time.sleep(poll_interval)

    print(
        "[ERROR] Blockchain RPC did not become ready within "
        f"{timeout_seconds} seconds: {last_error}"
    )
    sys.exit(1)


def wait_for_http_ready(
    url: str,
    service_name: str,
    timeout_seconds: int = 120,
    poll_interval: float = 2.0,
) -> None:
    """Wait until an HTTP health endpoint responds with a 2xx status code."""
    print(f"[INFO] Waiting for {service_name} health endpoint at '{url}'...")
    deadline = time.time() + timeout_seconds
    last_error = "service not ready yet"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                status_code = getattr(response, "status", None) or response.getcode()
                if 200 <= status_code < 300:
                    print(f"[OK] {service_name} is healthy at '{url}'.")
                    return
                last_error = f"unexpected HTTP status {status_code}"
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            last_error = str(exc)

        time.sleep(poll_interval)

    print(
        f"[ERROR] {service_name} did not become healthy within "
        f"{timeout_seconds} seconds: {last_error}"
    )
    sys.exit(1)


def probe_http_status(url: str) -> str:
    """Return a short status string for an HTTP endpoint."""
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            status_code = getattr(response, "status", None) or response.getcode()
        return f"up ({status_code})"
    except Exception:
        return "down"


def probe_minter_worker_status(minter_url: str) -> str:
    """Return a short status string for the split minter workers."""
    try:
        with urllib.request.urlopen(f"{minter_url.rstrip('/')}/api/v1/worker/status", timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return "down"

    workers = body.get("workers") or {}
    metadata_running = bool((workers.get("metadata") or {}).get("running"))
    chain_running = bool((workers.get("chain") or {}).get("running"))
    return (
        f"metadata={'up' if metadata_running else 'down'}, "
        f"chain={'up' if chain_running else 'down'}"
    )


def probe_rpc_block(rpc_url: str) -> str:
    """Return the current block number for a JSON-RPC endpoint or 'down'."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1,
    }
    try:
        request = urllib.request.Request(
            rpc_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        block_number = body.get("result")
        if not block_number:
            return "up"
        return f"up (block {int(block_number, 16)})"
    except Exception:
        return "down"


def probe_ipfs_api_status(ipfs_api_url: str) -> str:
    """Return a short status string for the configured IPFS API endpoint."""
    try:
        request = urllib.request.Request(
            f"{ipfs_api_url.rstrip('/')}/api/v0/id",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status_code = getattr(response, "status", None) or response.getcode()
        return f"up ({status_code})"
    except Exception:
        return "down"


def docker_network_exists(network_name: str) -> bool:
    """Return True when the named Docker network exists."""
    result = subprocess.run(
        f"docker network inspect {network_name}",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_dark_net() -> None:
    """Create the shared Docker network 'dark-net' if it doesn't already exist.

    In a full install dark-env creates this network. In decoupled/storage-only
    installs dark-env is absent, so we create the network here so that other
    components (dark-ipfs, app services) can join it.
    """
    if docker_network_exists("dark-net"):
        return
    print("[INFO] Docker network 'dark-net' not found — creating it...")
    result = subprocess.run(
        "docker network create dark-net",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] Failed to create Docker network 'dark-net': {result.stderr.strip()}")
        sys.exit(1)
    print("[OK] Docker network 'dark-net' created.")


def compose_up_stack(
    compose_dir: Path,
    stack_name: str,
    health_url: Optional[str] = None,
) -> None:
    """Start a Docker Compose stack and optionally wait for its health endpoint."""
    ensure_docker_running()
    compose_file = compose_dir / "docker-compose.yml"
    if not compose_file.exists():
        print(f"[WARNING] '{compose_file}' not found. Skipping {stack_name} Docker startup.")
        return

    ensure_dark_net()

    print(f"[INFO] Starting {stack_name} Docker stack...")
    run_compose_up_detached(
        "docker compose up -d --build",
        cwd=str(compose_dir),
        timeout_seconds=900,
    )

    if health_url:
        wait_for_http_ready(health_url, service_name=stack_name)


def print_install_summary(prefix: str, env: dict) -> None:
    """Print a concise summary of the installed local service endpoints."""
    print("\n=== Service Summary ===")

    rpc_url = env.get("RPC_URL", "http://localhost:8545").strip() or "http://localhost:8545"
    explorer_port = env.get("EXPLORER_PORT", "25000").strip() or "25000"
    explorer_url = f"http://localhost:{explorer_port}"
    ipfs_api_url    = env.get(f"{prefix}_IPFS_API_URL",     "http://localhost:5001").strip() or "http://localhost:5001"
    cluster_api_url = env.get(f"{prefix}_IPFS_CLUSTER_URL", "http://localhost:9094").strip() or "http://localhost:9094"
    admin_url = "http://localhost:8000"
    resolver_url = "http://localhost:8002"
    store_api_url = "http://localhost:8003"
    minter_url = "http://localhost:8001"

    print(f"- Blockchain RPC: {rpc_url} [{probe_rpc_block(rpc_url)}]")

    explorer_selected = bool(get_url(env, f"{prefix}_BLOCKCHAIN_DARK_EXPLORADOR_REPOSITORY_URL"))
    if explorer_selected:
        print(f"- Block Explorer: {explorer_url} [{probe_http_status(explorer_url)}]")

    admin_selected = bool(get_url(env, f"{prefix}_CORE_ADMIN_API_REPOSITORY_URL"))
    if admin_selected:
        print(
            f"- Core Admin API: {admin_url} "
            f"[health: {probe_http_status(f'{admin_url}/health')}] "
            f"[docs: {admin_url}/docs]"
        )

    ipfs_selected = (
        bool(get_url(env, f"{prefix}_IPFS_REPOSITORY_URL"))
        or bool(env.get(f"{prefix}_IPFS_HOST", "").strip())
        or bool(env.get(f"{prefix}_IPFS_API_URL", "").strip())
    )
    if ipfs_selected:
        print(f"- IPFS API: {ipfs_api_url} [{probe_ipfs_api_status(ipfs_api_url)}]")
        print(f"- IPFS Cluster: {cluster_api_url} [{probe_http_status(f'{cluster_api_url}/id')}]")

    store_api_selected = bool(get_url(env, f"{prefix}_STORE_API_REPOSITORY_URL"))
    if store_api_selected:
        print(
            f"- Store API: {store_api_url} "
            f"[health: {probe_http_status(f'{store_api_url}/health')}] "
            f"[docs: {store_api_url}/docs]"
        )

    resolver_selected = bool(get_url(env, f"{prefix}_RESOLVER_REPOSITORY_URL"))
    if resolver_selected:
        print(
            f"- Core Resolver API: {resolver_url} "
            f"[health: {probe_http_status(f'{resolver_url}/health')}] "
            f"[docs: {resolver_url}/docs]"
        )

    minter_selected = bool(get_url(env, f"{prefix}_MINTER_REPOSITORY_URL"))
    if minter_selected:
        print(
            f"- Core Minter API: {minter_url} "
            f"[health: {probe_http_status(f'{minter_url}/health')}] "
            f"[workers: {probe_minter_worker_status(minter_url)}] "
            f"[docs: {minter_url}/docs]"
        )

    core_lib_selected = bool(
        get_url(env, f"{prefix}_CORE_LIB_REPOSITORY_URL")
        or get_url(env, f"{prefix}_ORCHESTRATOR_REPOSITORY_URL")
    )
    if core_lib_selected:
        core_env_path = Path("components/libraries/dark-core-lib/.env.integration")
        print(f"- Core Lib env: {core_env_path}")


def run_commands(commands_str: str, cwd: str) -> None:
    """Parse and execute a ``|``-separated list of shell commands.

    Each segment is stripped of leading/trailing whitespace before
    being passed to the shell.

    :param commands_str: Commands separated by ``|``.
    :type commands_str: str
    :param cwd: Working directory in which every command is executed.
    :type cwd: str
    """
    for cmd in commands_str.split("|"):
        cmd = cmd.strip()
        if cmd:
            run_shell(cmd, cwd=cwd)


def ensure_shared_venv() -> Path:
    """Create or reuse a shared root virtual environment.

    :returns: Absolute path to the shared root venv.
    :rtype: Path
    """
    if not SHARED_VENV_DIR.exists():
        print(f"[INFO] Creating shared venv at '{SHARED_VENV_DIR}'...")
        python = find_web3_compatible_python()
        run_shell(f"{python} -m venv {SHARED_VENV_DIR}")
    else:
        print(f"[INFO] Shared venv already exists at '{SHARED_VENV_DIR}', reusing it.")

    return SHARED_VENV_DIR


def shared_venv_bin(bin_name: str) -> str:
    """Return absolute path to a binary inside the shared root venv."""
    venv_dir = ensure_shared_venv()
    return str(venv_dir / "bin" / bin_name)


def find_web3_compatible_python() -> str:
    """Return a Python 3.10–3.12 interpreter path suitable for web3/eth venvs.

    web3 and several of its dependencies (pyunormalize, eth-hash, …) do not
    yet support Python 3.13+.  If the current interpreter is too new, try to
    find an older one installed on the system.  Falls back to sys.executable
    when nothing better is available, letting pip report the incompatibility.
    """
    if sys.version_info < (3, 13):
        return sys.executable

    for candidate in ("python3.12", "python3.11", "python3.10"):
        result = subprocess.run(
            ["which", candidate],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip()
            print(f"[INFO] Using {candidate} ({path}) for component venv (web3 compat).")
            return path

    print(
        f"[WARNING] Python {sys.version_info.major}.{sys.version_info.minor} is not "
        "supported by web3 / eth packages.  Install python3.12 or python3.11 to avoid "
        "build errors:\n"
        "    sudo apt-get install python3.12 python3.12-venv"
    )
    return sys.executable


def ensure_component_venv(venv_dir: Path) -> Path:
    """Create or reuse a component-local virtual environment.

    This is used for components whose pinned dependencies would otherwise
    downgrade packages in the shared developer venv.
    Uses a Python ≤ 3.12 interpreter when the system Python is newer,
    because web3 and its dependencies do not yet support Python 3.13+.
    """
    if not venv_dir.exists():
        print(f"[INFO] Creating component venv at '{venv_dir}'...")
        python = find_web3_compatible_python()
        run_shell(f"{python} -m venv {venv_dir}")
    else:
        print(f"[INFO] Component venv already exists at '{venv_dir}', reusing it.")

    return venv_dir


def component_venv_bin(venv_dir: Path, bin_name: str) -> str:
    """Return absolute path to a binary inside a component-local venv."""
    resolved_venv = ensure_component_venv(venv_dir)
    return str(resolved_venv / "bin" / bin_name)


def get_url(env: dict, key: str) -> str:
    """Read a repository URL from ``env``, returning an empty string if unset.

    Unlike a hard exit, an empty return value signals the caller to skip
    the component gracefully.

    :param env: Dictionary of environment variables.
    :type env: dict
    :param key: Variable name to look up.
    :type key: str
    :returns: The URL value, or an empty string if missing/empty.
    :rtype: str
    """
    return env.get(key, "").strip()


def extract_wallet_info(wallet_path: str) -> dict:
    """Extract wallet address, public key, and private key from master-wallet.txt.

    :param wallet_path: Path to the master-wallet.txt file.
    :type wallet_path: str
    :returns: Dictionary with MASTER_WALLET_ADDRESS, MASTER_PUBLIC_KEY, and MASTER_PRIVATE_KEY.
    :rtype: dict
    """
    info = {}
    try:
        with open(wallet_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("Address"):
                    parts = stripped.split(":", 1)
                    if len(parts) == 2:
                        info["MASTER_WALLET_ADDRESS"] = parts[1].strip()
                elif stripped.startswith("Public Key"):
                    parts = stripped.split(":", 1)
                    if len(parts) == 2:
                        info["MASTER_PUBLIC_KEY"] = parts[1].strip()
                elif stripped.startswith("Private Key"):
                    parts = stripped.split(":", 1)
                    if len(parts) == 2:
                        info["MASTER_PRIVATE_KEY"] = parts[1].strip()
    except FileNotFoundError:
        pass
    return info


def update_env_file(env_path: str, updates: dict) -> None:
    """Update specific keys in a .env file, preserving comments and other variables.

    :param env_path: Path to the .env file.
    :type env_path: str
    :param updates: Dictionary mapping keys to their new values.
    :type updates: dict
    """
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    new_lines = []
    updated_keys = set()
    
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)


# ─── Repository installer ─────────────────────────────────────────────────────

#: Default commands used when a repo does not define its own COMMANDS variable.
DEFAULT_COMMANDS: str = (
    "chmod +x setup.sh|chmod +x scripts/*.sh|./setup.sh|docker compose up -d"
)


def install_repo(name: str, repo_url: str, branch: str, target_dir: str) -> None:
    """Clone a repository or pull the latest changes if it already exists.

    If ``target_dir`` already exists, a ``git pull`` is performed on the
    specified branch. Otherwise, the repository is cloned from ``repo_url``.

    :param name: Human-readable name of the component (used for logging).
    :type name: str
    :param repo_url: URL of the Git repository to clone.
    :type repo_url: str
    :param branch: Branch to clone or pull.
    :type branch: str
    :param target_dir: Local directory where the repository will be placed.
    :type target_dir: str
    """
    def github_https_to_ssh(url: str) -> Optional[str]:
        """Convert https GitHub URL to SSH URL when possible."""
        match = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if not match:
            return None
        owner, repo = match.groups()
        return f"git@github.com:{owner}/{repo}.git"

    def github_ssh_to_https(url: str) -> Optional[str]:
        """Convert SSH GitHub URL to https URL when possible."""
        match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
        if not match:
            return None
        owner, repo = match.groups()
        return f"https://github.com/{owner}/{repo}.git"

    def github_repo_slug(url: str) -> Optional[str]:
        """Return GitHub owner/repo slug for HTTPS or SSH URLs."""
        https_match = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if https_match:
            return f"{https_match.group(1)}/{https_match.group(2)}"

        ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
        if ssh_match:
            return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

        return None

    def build_repo_url_candidates(url: str) -> list[str]:
        """Build preferred URL candidates, prioritizing SSH for GitHub repos."""
        candidates = []
        ssh_url = github_https_to_ssh(url)
        https_url = github_ssh_to_https(url)

        if ssh_url:
            # Input was HTTPS GitHub URL: prefer SSH first to avoid auth errors.
            candidates.extend([ssh_url, url])
        elif https_url:
            # Input was SSH GitHub URL: keep SSH first, then HTTPS fallback.
            candidates.extend([url, https_url])
        else:
            candidates.append(url)

        # Deduplicate while preserving order.
        seen = set()
        ordered = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
        return ordered

    target = Path(target_dir)
    repo_candidates = build_repo_url_candidates(repo_url)

    if target.exists():
        print(f"[INFO] '{target_dir}' already exists — pulling latest changes...")
        # Keep origin aligned with configured URL before pulling.
        current_origin = subprocess.run(
            "git remote get-url origin",
            shell=True,
            cwd=str(target),
            capture_output=True,
            text=True,
        )
        current_origin_url = current_origin.stdout.strip() if current_origin.returncode == 0 else ""
        same_repo = (
            current_origin_url
            and github_repo_slug(current_origin_url)
            and github_repo_slug(current_origin_url) == github_repo_slug(repo_url)
        )
        if same_repo and current_origin_url:
            # Prefer the URL already known to work for this local clone.
            if current_origin_url in repo_candidates:
                repo_candidates = [current_origin_url] + [
                    c for c in repo_candidates if c != current_origin_url
                ]
            else:
                repo_candidates = [current_origin_url] + repo_candidates

        pull_success = False
        last_exc: Optional[SystemExit] = None
        for candidate_url in repo_candidates:
            if not current_origin_url or current_origin_url != candidate_url:
                print(f"[INFO] Updating origin URL to '{candidate_url}'...")
                run_shell(f"git remote set-url origin {candidate_url}", cwd=str(target))
                current_origin_url = candidate_url

            try:
                run_shell(f"git pull origin {branch}", cwd=str(target))
                pull_success = True
                break
            except SystemExit as exc:
                last_exc = exc
                print(
                    "[WARNING] Pull failed for remote "
                    f"'{candidate_url}'. Trying next candidate..."
                )

        if not pull_success and last_exc is not None:
            raise last_exc
    else:
        print(f"[INFO] Cloning '{name}' from {repo_url} (branch: {branch})...")
        clone_success = False
        last_exc: Optional[SystemExit] = None
        for candidate_url in repo_candidates:
            try:
                run_shell(f"git clone --branch {branch} {candidate_url} {str(target)}")
                clone_success = True
                break
            except SystemExit as exc:
                last_exc = exc
                print(
                    "[WARNING] Clone failed for URL "
                    f"'{candidate_url}'. Trying next candidate..."
                )

        if not clone_success and last_exc is not None:
            raise last_exc

    print(f"[OK] '{name}' ready at '{target_dir}'.\n")


def setup_repo(target_dir: str, commands_str: str) -> None:
    """Run the post-clone setup for a repository.

    If a ``requirements.txt`` is present in ``target_dir``, dependencies are
    installed into the shared root virtual environment (``./venv``) before
    any other commands run.

    The remaining setup commands are then executed in sequence as defined
    by ``commands_str``.

    :param target_dir: Path to the cloned repository directory.
    :type target_dir: str
    :param commands_str: ``|``-separated commands to run inside ``target_dir``.
    :type commands_str: str
    :raises SystemExit: If any step fails.
    """
    print(f"[INFO] Setting up '{target_dir}'...")

    # Resolve to absolute path so all sub-paths are unambiguous
    # regardless of the working directory used by run_shell.
    abs_target = Path(target_dir).resolve()

    # ── Python dependencies (optional, shared root venv) ────────────────────
    requirements = abs_target / "requirements.txt"

    if requirements.exists():
        print("[INFO] requirements.txt found — installing into shared root venv...")
        pip = shared_venv_bin("pip")
        run_shell(f"{pip} install -r requirements.txt", cwd=str(abs_target))
        print(f"[OK] Python dependencies installed into '{SHARED_VENV_DIR}'.\n")

    # ── Run per-repo commands ─────────────────────────────────────────────────
    run_commands(commands_str, cwd=str(abs_target))

    print(f"[OK] '{target_dir}' is up.\n")


# ─── dark-dapp specific installer ────────────────────────────────────────────


def install_dark_dapp(target_dir: str, env: dict) -> None:
    """Run the full setup sequence for the ``dark-dapp`` component.

    Performs the following steps inside ``target_dir``:

    1. Installs Python dependencies into a component-local venv
       (``<target_dir>/.venv``).
    2. Generates ``config.ini`` from the global blockchain variables in ``.env``
       (``RPC_URL``, ``CHAIN_ID``, ``MASTER_PRIVATE_KEY``).
    3. Compiles Solidity contracts via ``dARK_dapp/compile.py``.
    4. Deploys contracts to the configured network via ``dARK_dapp/deploy.py``.

    :param target_dir: Path to the cloned ``dark-dapp`` directory.
    :type target_dir: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    :raises SystemExit: If any required variable is missing or a step fails.
    """
    abs_target = Path(target_dir).resolve()
    component_venv = abs_target / ".venv"
    pip            = component_venv_bin(component_venv, "pip")
    python         = component_venv_bin(component_venv, "python3")

    # ── Step 1: isolated venv + pip install ──────────────────────────────────
    print("[INFO] Installing dark-dapp Python dependencies into component-local venv...")
    run_shell(f"{pip} install -r requirements.txt", cwd=str(abs_target))
    print(f"[OK] Python dependencies installed into '{component_venv}'.\n")

    # Pre-download solc on the host so the AMD64 container can reuse the cache
    # via a volume mount, bypassing SSL issues inside the emulated environment.
    # On non-x86_64 hosts (e.g. aarch64) the AMD64 binary cannot execute on the
    # host — skip the pre-download and let the emulated container handle it.
    if platform.machine() == "x86_64":
        print("[INFO] Pre-downloading solc v0.8.17 on host...")
        # Remove any stale binary from a previous run before re-downloading.
        solcx_cache = Path.home() / ".solcx" / "solc-v0.8.17"
        if solcx_cache.exists():
            solcx_cache.unlink()
        run_shell(
            f"{python} -c \"import solcx; solcx.install_solc('0.8.17')\"",
            cwd=str(abs_target),
        )
        print("[OK] solc v0.8.17 ready in host cache.\n")
    else:
        print(
            f"[INFO] Skipping host-side solc pre-download on {platform.machine()} "
            "— the AMD64 container will download solc inside its own environment.\n"
        )

    # ── Step 2: generate config.ini ───────────────────────────────────────────
    rpc_url     = env.get("RPC_URL", "").strip()
    chain_id    = env.get("CHAIN_ID", "").strip()
    private_key = env.get("MASTER_PRIVATE_KEY", "").strip()
    public_key  = env.get("MASTER_PUBLIC_KEY", "").strip()
    address     = env.get("MASTER_WALLET_ADDRESS", "").strip()

    if not rpc_url:
        print("[ERROR] 'RPC_URL' is not set in .env")
        sys.exit(1)
    if not chain_id:
        print("[ERROR] 'CHAIN_ID' is not set in .env")
        sys.exit(1)
    if not private_key or private_key == "0x":
        print("[ERROR] 'MASTER_PRIVATE_KEY' is not set in .env")
        sys.exit(1)

    config = configparser.ConfigParser()
    config["base"]       = {"blockchain_net": "dark-local"}
    config["dark-local"] = {
        "url":              rpc_url,
        "chain_id":         chain_id,
        "MASTER_WALLET_ADDRESS": address,
        "MASTER_PRIVATE_KEY": private_key,
        "MASTER_PUBLIC_KEY": public_key,
        "acoupled_setup":   "True",
    }

    config_path = abs_target / "config.ini"
    with open(config_path, "w") as f:
        config.write(f)
    print(f"[OK] config.ini generated at '{config_path}'.\n")

    # ── Step 3: compile contracts ─────────────────────────────────────────────
    # compile.py uses relative paths (./contracts, ./compiled) so it must be
    # run from inside dARK_dapp/, not from the repo root.
    dapp_dir     = abs_target / "dARK_dapp"
    compiled_dir = dapp_dir / "compiled"

    print("[INFO] Compiling Solidity contracts...")
    run_shell(f"{python} compile.py", cwd=str(dapp_dir))

    # compile.py swallows exceptions and exits with code 0 even on failure,
    # so we verify the compiled/ directory was actually populated.
    if not compiled_dir.exists() or not any(compiled_dir.iterdir()):
        print("[ERROR] Compilation failed — 'dARK_dapp/compiled/' is empty or missing.")
        sys.exit(1)
    print("[OK] Contracts compiled.\n")

    # ── Step 4: deploy contracts ──────────────────────────────────────────────
    # deploy.py also uses relative paths (./compiled, ./deployed_contracts.ini)
    # so it must run from inside dARK_dapp/ as well.
    # Wait for the node to be ready before deploying — docker compose up -d
    # returns immediately but the node may still be initialising.
    wait_for_rpc(rpc_url)
    print("[INFO] Deploying contracts to the network...")
    run_shell(f"{python} deploy.py", cwd=str(dapp_dir))
    print("[OK] Contracts deployed.\n")



# ─── Blockchain submodule installer ──────────────────────────────────────────


def install_blockchain(prefix: str, env: dict) -> None:
    """Clone and set up the three blockchain submodules.

    Each submodule is an independent Git repository installed under
    ``components/blockchain/<submodule>/``.

    Submodules:
        - ``dark-env``
        - ``dark-dapp``
        - ``dark-explorador``

    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    print("\n── Blockchain ────────────────────────────────────────────────")

    #: Mapping of submodule folder names to their env-variable suffixes.
    submodules: dict = {
        "dark-env":        "DARK_ENV",
        "dark-dapp":       "DARK_DAPP",
        "dark-explorador": "DARK_EXPLORADOR",
    }

    for folder, suffix in submodules.items():
        url_key      = f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_URL"
        branch_key   = f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_BRANCH"
        setup_key    = f"{prefix}_BLOCKCHAIN_{suffix}_SETUP"
        commands_key = f"{prefix}_BLOCKCHAIN_{suffix}_COMMANDS"

        repo_url = get_url(env, url_key)
        if not repo_url:
            print(f"[SKIP] '{url_key}' is not set — skipping '{folder}'.")
            continue

        branch   = env.get(branch_key, "master").strip()
        do_setup = env.get(setup_key, "True").strip().lower() != "false"
        commands = env.get(commands_key, DEFAULT_COMMANDS).strip()
        target   = f"components/blockchain/{folder}"

        install_repo(name=folder, repo_url=repo_url, branch=branch, target_dir=target)

        if do_setup:
            # dark-dapp has a dedicated installer that generates config.ini
            # and deploys smart contracts instead of running generic COMMANDS.
            if folder == "dark-dapp":
                install_dark_dapp(target_dir=target, env=env)
            else:
                setup_repo(target_dir=target, commands_str=commands)

                # After dark-env is set up, attempt to extract the master wallet if present
                if folder == "dark-env":
                    wallet_path = Path(target) / "master-wallet.txt"
                    wallet_info = extract_wallet_info(str(wallet_path))
                    if wallet_info:
                        print(f"[INFO] Extracted master wallet info from '{wallet_path}'.")
                        update_env_file(".env", wallet_info)
                        print("[OK] Updated global .env with master wallet keys.")
                        # Update the in-memory env dict so subsequent setups (like dark-dapp) see the changes!
                        env.update(wallet_info)
        else:
            print(f"[INFO] SETUP=False — '{folder}' cloned, setup skipped.\n")

# ─── Core-lib installer ───────────────────────────────────────────────────────


def read_deployed_contract_addresses(ini_path: Path) -> tuple[str, str]:
    """Read dARK and Authority addresses from deployed_contracts.ini.

    :param ini_path: Path to deployed_contracts.ini.
    :type ini_path: Path
    :returns: Tuple of (dark_contract_address, authority_contract_address).
    :rtype: tuple[str, str]
    """
    dark_address = ""
    authority_address = ""

    if not ini_path.exists():
        return dark_address, authority_address

    deployed = configparser.ConfigParser()
    deployed.read(ini_path)

    section_map = {section.lower(): section for section in deployed.sections()}

    dark_section = section_map.get("dark")
    if dark_section:
        dark_address = deployed.get(dark_section, "address", fallback="").strip()

    authority_section = section_map.get("authority")
    if authority_section:
        authority_address = deployed.get(authority_section, "address", fallback="").strip()

    return dark_address, authority_address


def resolve_contract_addresses(env: dict) -> tuple[str, str]:
    """Return (dark_address, authority_address).

    Checks prefixed .env vars first (set by operator for remote blockchain),
    then falls back to deployed_contracts.ini for local installs.
    """
    _prefixes = {"developer": "DEVELOPER", "sandbox": "SANDBOX", "production": "PRODUCTION"}
    prefix = _prefixes.get(env.get("TYPE", "developer").lower(), "DEVELOPER")

    dark_addr      = env.get(f"{prefix}_DARK_CONTRACT_ADDRESS", "").strip()
    authority_addr = env.get(f"{prefix}_AUTHORITY_CONTRACT_ADDRESS", "").strip()

    if dark_addr and authority_addr:
        return dark_addr, authority_addr

    ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
    dark_from_ini, auth_from_ini = read_deployed_contract_addresses(ini_path)
    return dark_addr or dark_from_ini, authority_addr or auth_from_ini


def generate_core_lib_env_integration(core_path: Path, env: dict) -> None:
    """Generate .env.integration for dark-core-lib from installed blockchain state.

    :param core_path: Path to components/libraries/dark-core-lib.
    :type core_path: Path
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    dark_contract, authority_contract = resolve_contract_addresses(env)

    if not dark_contract or not authority_contract:
        print(
            "[WARNING] Could not read full contract addresses from "
            f"'{ini_path}'. .env.integration may be incomplete."
        )

    integration_env = {
        "DARK_RPC_URL": env.get("RPC_URL", "http://localhost:8545").strip(),
        "DARK_CHAIN_ID": env.get("CHAIN_ID", "1337").strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract,
        "DARK_AUTHORITY_ADDRESS": authority_contract,
        "DARK_ADMIN_PRIVATE_KEY": env.get("MASTER_PRIVATE_KEY", "").strip(),
        "DARK_READ_ONLY": "False",
        "DARK_VALIDATE_CHAIN_ID": "True",
        "DARK_GAS_LIMIT": "550000",
        "DARK_TX_TIMEOUT_SECONDS": "120",
    }

    env_path = core_path / ".env.integration"
    lines = [f"{key}={value}\n" for key, value in integration_env.items()]
    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"[OK] Generated '{env_path}'.")


def generate_minter_env_integration(minter_path: Path, env: dict) -> None:
    """Generate .env.integration for minter from installed blockchain state.

    Uses the component's ``.env.example`` as a baseline, then overwrites the
    blockchain-sensitive values with the deployed contracts and master key from
    the active installation.

    :param minter_path: Path to components/services/dark-core-minter-api.
    :type minter_path: Path
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    dark_contract, authority_contract = resolve_contract_addresses(env)

    if not dark_contract or not authority_contract:
        print("[WARNING] Contract addresses not found in .env or deployed_contracts.ini. .env.integration may be incomplete.")

    template_env = load_optional_env(minter_path / ".env.example")
    metadata_storage_type = template_env.get("METADATA_STORAGE_TYPE", "store_api").strip()
    metadata_storage_path = template_env.get("METADATA_STORAGE_PATH", "./metadata_storage").strip()

    if metadata_storage_type == "filesystem":
        storage_path = Path(metadata_storage_path)
        if not storage_path.is_absolute():
            metadata_storage_path = str((minter_path / storage_path).resolve())

    split_worker_env = {
        "METADATA_WORKER_ENABLED": env.get(
            "METADATA_WORKER_ENABLED",
            template_env.get("METADATA_WORKER_ENABLED", "true"),
        ).strip(),
        "METADATA_WORKER_PAGE_SIZE": env.get(
            "METADATA_WORKER_PAGE_SIZE",
            template_env.get("METADATA_WORKER_PAGE_SIZE", "100"),
        ).strip(),
        "METADATA_WORKER_SLEEP_SECONDS": env.get(
            "METADATA_WORKER_SLEEP_SECONDS",
            template_env.get("METADATA_WORKER_SLEEP_SECONDS", "2"),
        ).strip(),
        "METADATA_WORKER_STORAGE_RETRY_SECONDS": env.get(
            "METADATA_WORKER_STORAGE_RETRY_SECONDS",
            template_env.get("METADATA_WORKER_STORAGE_RETRY_SECONDS", "10"),
        ).strip(),
        "METADATA_WORKER_MAX_RETRIES": env.get(
            "METADATA_WORKER_MAX_RETRIES",
            template_env.get("METADATA_WORKER_MAX_RETRIES", "5"),
        ).strip(),
        "METADATA_WORKER_RETRY_BACKOFF_BASE": env.get(
            "METADATA_WORKER_RETRY_BACKOFF_BASE",
            template_env.get("METADATA_WORKER_RETRY_BACKOFF_BASE", "2.0"),
        ).strip(),
        "METADATA_WORKER_RUNTIME_NAME": env.get(
            "METADATA_WORKER_RUNTIME_NAME",
            template_env.get("METADATA_WORKER_RUNTIME_NAME", "metadata-publisher"),
        ).strip(),
        "CHAIN_WORKER_ENABLED": env.get(
            "CHAIN_WORKER_ENABLED",
            template_env.get("CHAIN_WORKER_ENABLED", "true"),
        ).strip(),
        "CHAIN_WORKER_PAGE_SIZE": env.get(
            "CHAIN_WORKER_PAGE_SIZE",
            template_env.get("CHAIN_WORKER_PAGE_SIZE", "20"),
        ).strip(),
        "CHAIN_WORKER_SLEEP_SECONDS": env.get(
            "CHAIN_WORKER_SLEEP_SECONDS",
            template_env.get("CHAIN_WORKER_SLEEP_SECONDS", "5"),
        ).strip(),
        "CHAIN_WORKER_RPC_RETRY_SECONDS": env.get(
            "CHAIN_WORKER_RPC_RETRY_SECONDS",
            template_env.get("CHAIN_WORKER_RPC_RETRY_SECONDS", "10"),
        ).strip(),
        "CHAIN_WORKER_CONGESTION_RETRY_SECONDS": env.get(
            "CHAIN_WORKER_CONGESTION_RETRY_SECONDS",
            template_env.get("CHAIN_WORKER_CONGESTION_RETRY_SECONDS", "30"),
        ).strip(),
        "CHAIN_WORKER_BLOCK_STALL_SECONDS": env.get(
            "CHAIN_WORKER_BLOCK_STALL_SECONDS",
            template_env.get("CHAIN_WORKER_BLOCK_STALL_SECONDS", "120"),
        ).strip(),
        "CHAIN_WORKER_ADAPTIVE_PAGE_ENABLED": env.get(
            "CHAIN_WORKER_ADAPTIVE_PAGE_ENABLED",
            template_env.get("CHAIN_WORKER_ADAPTIVE_PAGE_ENABLED", "true"),
        ).strip(),
        "CHAIN_WORKER_MIN_PAGE_SIZE": env.get(
            "CHAIN_WORKER_MIN_PAGE_SIZE",
            template_env.get("CHAIN_WORKER_MIN_PAGE_SIZE", "1"),
        ).strip(),
        "CHAIN_WORKER_RECOVERY_SUCCESS_CYCLES": env.get(
            "CHAIN_WORKER_RECOVERY_SUCCESS_CYCLES",
            template_env.get("CHAIN_WORKER_RECOVERY_SUCCESS_CYCLES", "3"),
        ).strip(),
        "CHAIN_WORKER_MAX_RETRIES": env.get(
            "CHAIN_WORKER_MAX_RETRIES",
            template_env.get("CHAIN_WORKER_MAX_RETRIES", "5"),
        ).strip(),
        "CHAIN_WORKER_RETRY_BACKOFF_BASE": env.get(
            "CHAIN_WORKER_RETRY_BACKOFF_BASE",
            template_env.get("CHAIN_WORKER_RETRY_BACKOFF_BASE", "2.0"),
        ).strip(),
        "CHAIN_WORKER_RUNTIME_NAME": env.get(
            "CHAIN_WORKER_RUNTIME_NAME",
            template_env.get("CHAIN_WORKER_RUNTIME_NAME", "chain-publisher"),
        ).strip(),
        "WORKER_HEARTBEAT_INTERVAL_SECONDS": env.get(
            "WORKER_HEARTBEAT_INTERVAL_SECONDS",
            template_env.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "10"),
        ).strip(),
        "WORKER_HEARTBEAT_STALE_AFTER_SECONDS": env.get(
            "WORKER_HEARTBEAT_STALE_AFTER_SECONDS",
            template_env.get("WORKER_HEARTBEAT_STALE_AFTER_SECONDS", "180"),
        ).strip(),
        "PERMANENT_RESCUE_MAX_ITEMS": env.get(
            "PERMANENT_RESCUE_MAX_ITEMS",
            template_env.get("PERMANENT_RESCUE_MAX_ITEMS", "50"),
        ).strip(),
    }

    integration_env = {
        **template_env,
        **split_worker_env,
        "MINTER_API_WORKERS": env.get(
            "MINTER_API_WORKERS",
            template_env.get("MINTER_API_WORKERS", "2"),
        ).strip(),
        "DARK_RPC_URL": env.get(
            "RPC_URL",
            template_env.get("DARK_RPC_URL", "http://localhost:8545"),
        ).strip(),
        "DARK_RPC_HEALTH_TIMEOUT_SECONDS": env.get(
            "DARK_RPC_HEALTH_TIMEOUT_SECONDS",
            template_env.get("DARK_RPC_HEALTH_TIMEOUT_SECONDS", "2.0"),
        ).strip(),
        "DARK_RPC_CONNECT_RETRY_SECONDS": env.get(
            "DARK_RPC_CONNECT_RETRY_SECONDS",
            template_env.get("DARK_RPC_CONNECT_RETRY_SECONDS", "30.0"),
        ).strip(),
        "DARK_RPC_CONNECT_RETRY_INTERVAL_SECONDS": env.get(
            "DARK_RPC_CONNECT_RETRY_INTERVAL_SECONDS",
            template_env.get("DARK_RPC_CONNECT_RETRY_INTERVAL_SECONDS", "2.0"),
        ).strip(),
        "DARK_CHAIN_ID": env.get(
            "CHAIN_ID",
            template_env.get("DARK_CHAIN_ID", "1337"),
        ).strip(),
        "DARK_GAS_LIMIT": env.get(
            "DARK_GAS_LIMIT",
            template_env.get("DARK_GAS_LIMIT", "550000"),
        ).strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract or template_env.get("DARK_CONTRACT_ADDRESS", "").strip(),
        "DARK_AUTHORITY_ADDRESS": authority_contract or template_env.get("DARK_AUTHORITY_ADDRESS", "").strip(),
        "DARK_ADMIN_PRIVATE_KEY": env.get(
            "MASTER_PRIVATE_KEY",
            template_env.get("DARK_ADMIN_PRIVATE_KEY", ""),
        ).strip(),
        "METADATA_STORAGE_TYPE": metadata_storage_type,
        "METADATA_STORAGE_PATH": metadata_storage_path,
        "METADATA_STORE_API_URL": (
            "http://store-api:8003" if metadata_storage_type == "store_api"
            else template_env.get("METADATA_STORE_API_URL", "http://localhost:8003").strip()
        ),
    }

    env_path = minter_path / ".env.integration"
    lines = [f"{key}={value}\n" for key, value in integration_env.items()]
    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"[OK] Generated '{env_path}'.")


def generate_store_api_env_integration(store_api_path: Path, env: dict) -> None:
    """Generate .env.integration for dark-store-api from installed IPFS settings."""
    template_env = load_optional_env(store_api_path / ".env.example")

    _prefixes = {"developer": "DEVELOPER", "sandbox": "SANDBOX", "production": "PRODUCTION"}
    prefix = _prefixes.get(env.get("TYPE", "developer").lower(), "DEVELOPER")

    ipfs_host    = env.get(f"{prefix}_IPFS_HOST", "").strip()
    ipfs_api_env = env.get(f"{prefix}_IPFS_API_URL", "").strip()

    if ipfs_host:
        ipfs_api_url       = ipfs_api_env or f"http://{ipfs_host}:5001"
        ipfs_cluster_url   = env.get(f"{prefix}_IPFS_CLUSTER_URL", f"http://{ipfs_host}:9094").strip()
        ipfs_cluster_proxy = env.get(f"{prefix}_IPFS_CLUSTER_PROXY_URL", ipfs_cluster_url.replace(":9094", ":9095")).strip()
    elif ipfs_api_env:
        ipfs_api_url       = ipfs_api_env
        ipfs_cluster_url   = env.get(f"{prefix}_IPFS_CLUSTER_URL", "http://localhost:9094").strip()
        ipfs_cluster_proxy = env.get(f"{prefix}_IPFS_CLUSTER_PROXY_URL", "http://localhost:9095").strip()
    else:
        ipfs_api_url       = "http://ipfs0:5001"
        ipfs_cluster_url   = "http://cluster0:9094"
        ipfs_cluster_proxy = "http://cluster0:9095"

    integration_env = {
        **template_env,
        "STORE_API_HOST": template_env.get("STORE_API_HOST", "0.0.0.0").strip(),
        "STORE_API_PORT": "8003",
        "STORAGE_BACKEND": template_env.get("STORAGE_BACKEND", "ipfs_cluster").strip(),
        "IPFS_API_URL":               ipfs_api_url,
        "IPFS_CLUSTER_API_URL":       ipfs_cluster_url,
        "IPFS_CLUSTER_PROXY_API_URL": ipfs_cluster_proxy,
        "IPFS_ADD_MODE": env.get(
            "IPFS_ADD_MODE",
            template_env.get("IPFS_ADD_MODE", "cluster_proxy"),
        ).strip(),
        "IPFS_HEALTH_CACHE_TTL_SECONDS": env.get(
            "IPFS_HEALTH_CACHE_TTL_SECONDS",
            template_env.get("IPFS_HEALTH_CACHE_TTL_SECONDS", "10"),
        ).strip(),
        "IPFS_CLUSTER_MIN_PEERS": env.get(
            "IPFS_CLUSTER_MIN_PEERS",
            template_env.get("IPFS_CLUSTER_MIN_PEERS", "1"),
        ).strip(),
    }

    env_path = store_api_path / ".env.integration"
    lines = [f"{key}={value}\n" for key, value in integration_env.items()]
    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"[OK] Generated '{env_path}'.")


def generate_admin_api_env_integration(admin_api_path: Path, env: dict) -> None:
    """Generate .env.integration for dark-core-admin-api from installed blockchain state.

    Uses the component's ``.env.example`` as a baseline, then overwrites the
    blockchain-sensitive values with the deployed contracts and master key from
    the active installation.

    :param admin_api_path: Path to components/services/dark-core-admin-api.
    :type admin_api_path: Path
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    dark_contract, authority_contract = resolve_contract_addresses(env)

    if not dark_contract or not authority_contract:
        print("[WARNING] Contract addresses not found in .env or deployed_contracts.ini. .env.integration may be incomplete.")

    template_env = load_optional_env(admin_api_path / ".env.example")

    integration_env = {
        **template_env,
        "DARK_RPC_URL": env.get(
            "RPC_URL",
            template_env.get("DARK_RPC_URL", "http://localhost:8545"),
        ).strip(),
        "DARK_CHAIN_ID": env.get(
            "CHAIN_ID",
            template_env.get("DARK_CHAIN_ID", "1337"),
        ).strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract or template_env.get("DARK_CONTRACT_ADDRESS", "").strip(),
        "DARK_AUTHORITY_ADDRESS": authority_contract or template_env.get("DARK_AUTHORITY_ADDRESS", "").strip(),
        "DARK_ADMIN_PRIVATE_KEY": env.get(
            "MASTER_PRIVATE_KEY",
            template_env.get("DARK_ADMIN_PRIVATE_KEY", ""),
        ).strip(),
    }

    env_path = admin_api_path / ".env.integration"
    lines = [f"{key}={value}\n" for key, value in integration_env.items()]
    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"[OK] Generated '{env_path}'.")


def generate_resolver_api_env_integration(resolver_api_path: Path, env: dict) -> None:
    """Generate .env.integration for dark-core-resolver-api.

    Uses the component's ``.env.example`` as a baseline and overlays
    blockchain values from the active installation. Metadata storage settings
    are derived from the resolver defaults so the generated environment is
    deterministic and does not accidentally inherit stale values from a
    previous minter installation.

    :param resolver_api_path: Path to components/services/dark-core-resolver-api.
    :type resolver_api_path: Path
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    dark_contract, _ = resolve_contract_addresses(env)

    if not dark_contract:
        print("[WARNING] dARK contract address not found in .env or deployed_contracts.ini. Resolver .env.integration may be incomplete.")

    template_env = load_optional_env(resolver_api_path / ".env.example")

    metadata_storage_type = template_env.get("METADATA_STORAGE_TYPE", "store_api").strip()
    metadata_storage_path = template_env.get("METADATA_STORAGE_PATH", "./metadata_storage").strip()

    if metadata_storage_type == "filesystem":
        storage_path = Path(metadata_storage_path)
        if not storage_path.is_absolute():
            # Resolver filesystem mode must read the same metadata directory that
            # the minter writes to, so relative paths are resolved from the
            # minter service workspace rather than the resolver workspace.
            minter_path = Path("components/services/dark-core-minter-api").resolve()
            metadata_storage_path = str((minter_path / storage_path).resolve())

    integration_env = {
        **template_env,
        "DARK_RPC_URL": env.get(
            "RPC_URL",
            template_env.get("DARK_RPC_URL", "http://localhost:8545"),
        ).strip(),
        "DARK_CHAIN_ID": env.get(
            "CHAIN_ID",
            template_env.get("DARK_CHAIN_ID", "1337"),
        ).strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract or template_env.get("DARK_CONTRACT_ADDRESS", "").strip(),
        "METADATA_STORAGE_TYPE": metadata_storage_type,
        "METADATA_STORAGE_PATH": metadata_storage_path,
        "METADATA_STORE_API_URL": (
            "http://store-api:8003" if metadata_storage_type == "store_api"
            else template_env.get("METADATA_STORE_API_URL", "http://localhost:8003").strip()
        ),
        "METADATA_STORE_API_TIMEOUT_SECONDS": (
            template_env.get("METADATA_STORE_API_TIMEOUT_SECONDS", "10.0")
        ).strip(),
    }

    env_path = resolver_api_path / ".env.integration"
    lines = [f"{key}={value}\n" for key, value in integration_env.items()]
    with open(env_path, "w") as f:
        f.writelines(lines)

    print(f"[OK] Generated '{env_path}'.")


def commands_include_docker_compose_up(commands_str: str) -> bool:
    """Return True when the command string already handles compose startup."""
    return any(
        "docker compose up" in cmd or "docker-compose up" in cmd
        for cmd in (part.strip() for part in commands_str.split("|"))
    )


def start_minter_stack(minter_path: Path) -> None:
    """Build, migrate, and start minter services via Docker Compose."""
    ensure_docker_running()
    compose_file = minter_path / "docker-compose.yml"
    if not compose_file.exists():
        print(f"[WARNING] '{compose_file}' not found. Skipping minter Docker startup.")
        return

    ensure_dark_net()

    print("[INFO] Building minter Docker images...")
    run_shell("docker compose build", cwd=str(minter_path))

    print("[INFO] Starting minter Postgres for migration...")
    run_compose_up_detached("docker compose up -d postgres", cwd=str(minter_path))

    print("[INFO] Running minter database migrations...")
    run_shell("docker compose run --rm minter-api migrate", cwd=str(minter_path))

    print("[INFO] Starting minter Docker stack...")
    run_compose_up_detached("docker compose up -d", cwd=str(minter_path))
    wait_for_http_ready(
        "http://localhost:8001/health",
        service_name="minter",
    )


def start_admin_api_stack(admin_api_path: Path) -> None:
    """Start admin API via Docker Compose when blockchain network exists."""
    compose_up_stack(
        compose_dir=admin_api_path,
        stack_name="admin API",
        health_url="http://localhost:8000/health",
    )


def start_resolver_api_stack(resolver_api_path: Path) -> None:
    """Start resolver API via Docker Compose when the component provides it."""
    compose_up_stack(
        compose_dir=resolver_api_path,
        stack_name="resolver API",
        health_url="http://localhost:8002/health",
    )


def start_store_api_stack(store_api_path: Path) -> None:
    """Start store API via Docker Compose when the component provides it."""
    compose_up_stack(
        compose_dir=store_api_path,
        stack_name="store API",
        health_url="http://localhost:8003/health",
    )


# ─── Selective component rebuilds ─────────────────────────────────────────────


REBUILD_COMPONENT_ALIASES = {
    "admin": "admin",
    "admin-api": "admin",
    "dark-core-admin-api": "admin",
    "resolver": "resolver",
    "resolver-api": "resolver",
    "dark-core-resolver-api": "resolver",
    "store": "store-api",
    "store-api": "store-api",
    "dark-store-api": "store-api",
    "minter": "minter",
    "minter-api": "minter",
    "dark-core-minter-api": "minter",
    "core": "core-lib",
    "core-lib": "core-lib",
    "dark-core-lib": "core-lib",
}


SERVICE_REBUILD_COMPONENTS = {
    "admin": {
        "display": "admin API",
        "path": Path("components/services/dark-core-admin-api"),
        "services": ["admin-api"],
        "health_url": "http://localhost:8000/health",
        "repo_suffix": "CORE_ADMIN_API",
        "env_generator": generate_admin_api_env_integration,
    },
    "resolver": {
        "display": "resolver API",
        "path": Path("components/services/dark-core-resolver-api"),
        "services": ["resolver-api"],
        "health_url": "http://localhost:8002/health",
        "repo_suffix": "RESOLVER",
        "env_generator": generate_resolver_api_env_integration,
    },
    "store-api": {
        "display": "store API",
        "path": Path("components/services/dark-store-api"),
        "services": ["store-api"],
        "health_url": "http://localhost:8003/health",
        "repo_suffix": "STORE_API",
        "env_generator": generate_store_api_env_integration,
    },
    "minter": {
        "display": "minter",
        "path": Path("components/services/dark-core-minter-api"),
        "services": [],
        "health_url": "http://localhost:8001/health",
        "repo_suffix": "MINTER",
        "env_generator": generate_minter_env_integration,
    },
}


def canonical_rebuild_component(component: str) -> str:
    """Normalize a user-provided component name for selective rebuilds."""
    normalized = component.strip().lower()
    canonical = REBUILD_COMPONENT_ALIASES.get(normalized)
    if canonical:
        return canonical

    valid = ", ".join(sorted(REBUILD_COMPONENT_ALIASES))
    print(f"[ERROR] Unknown rebuild component '{component}'.")
    print(f"[ERROR] Valid components/aliases: {valid}")
    sys.exit(1)


def maybe_pull_rebuild_component(component: str, prefix: str, env: dict) -> None:
    """Optionally update a component repository before rebuilding it."""
    if component == "core-lib":
        core_url_key = f"{prefix}_CORE_LIB_REPOSITORY_URL"
        core_branch_key = f"{prefix}_CORE_LIB_REPOSITORY_BRANCH"
        legacy_url_key = f"{prefix}_ORCHESTRATOR_REPOSITORY_URL"
        legacy_branch_key = f"{prefix}_ORCHESTRATOR_REPOSITORY_BRANCH"

        repo_url = get_url(env, core_url_key) or get_url(env, legacy_url_key)
        branch = (
            env.get(core_branch_key, "").strip()
            or env.get(legacy_branch_key, "main").strip()
            or "main"
        )
        target = "components/libraries/dark-core-lib"
        repo_name = "dark-core-lib"
    else:
        spec = SERVICE_REBUILD_COMPONENTS[component]
        repo_suffix = str(spec["repo_suffix"])
        url_key = f"{prefix}_{repo_suffix}_REPOSITORY_URL"
        branch_key = f"{prefix}_{repo_suffix}_REPOSITORY_BRANCH"
        repo_url = get_url(env, url_key)
        branch = env.get(branch_key, "main").strip() or "main"
        target = str(spec["path"])
        repo_name = str(spec["display"])

    if not repo_url:
        print(f"[WARNING] No repository URL configured for '{component}'. Skipping pull.")
        return

    install_repo(
        name=repo_name,
        repo_url=repo_url,
        branch=branch,
        target_dir=target,
    )


def require_component_path(component: str, path: Path) -> Path:
    """Return a resolved component path or fail with a helpful message."""
    resolved = path.resolve()
    if resolved.exists():
        return resolved

    print(f"[ERROR] Component '{component}' is not installed at '{resolved}'.")
    print("[ERROR] Run the full installer first, or rerun rebuild with --pull if the repo URL is configured.")
    sys.exit(1)


def run_compose_build(compose_dir: Path, services: list[str], no_cache: bool) -> None:
    """Build one component stack or the selected services in that stack."""
    compose_file = compose_dir / "docker-compose.yml"
    if not compose_file.exists():
        print(f"[ERROR] '{compose_file}' not found.")
        sys.exit(1)

    command = "docker compose build"
    if no_cache:
        command += " --no-cache"
    if services:
        command += " " + " ".join(services)
    run_shell(command, cwd=str(compose_dir))


def rebuild_service_component(
    component: str,
    env: dict,
    no_cache: bool = False,
    no_start: bool = False,
    migrate: bool = False,
) -> None:
    """Rebuild and optionally restart a Dockerized service component."""
    spec = SERVICE_REBUILD_COMPONENTS[component]
    display = str(spec["display"])
    component_path = require_component_path(component, Path(spec["path"]))

    print(f"\n── Rebuild {display} ─────────────────────────────────────────")
    env_generator = spec["env_generator"]
    env_generator(component_path, env)

    ensure_docker_running()
    run_compose_build(
        compose_dir=component_path,
        services=list(spec["services"]),
        no_cache=no_cache,
    )

    if no_start:
        print(f"[OK] Built {display}. Startup skipped because --no-start was provided.")
        return

    ensure_dark_net()

    if component == "minter":
        print("[INFO] Starting minter Postgres...")
        run_compose_up_detached("docker compose up -d postgres", cwd=str(component_path))

        if migrate:
            print("[INFO] Running minter database migrations...")
            run_shell("docker compose run --rm minter-api migrate", cwd=str(component_path))

        print("[INFO] Starting minter API and workers...")
        run_compose_up_detached(
            "docker compose up -d minter-api minter-metadata-worker minter-chain-worker",
            cwd=str(component_path),
        )
    else:
        service_names = " ".join(spec["services"])
        print(f"[INFO] Starting {display}...")
        run_compose_up_detached(f"docker compose up -d {service_names}", cwd=str(component_path))

    wait_for_http_ready(str(spec["health_url"]), service_name=display)
    print(f"[OK] Rebuild complete for {display}.")


def rebuild_core_lib(
    prefix: str,
    env: dict,
    no_cache: bool = False,
    no_start: bool = False,
    with_dependents: bool = False,
    skip_minter_migrate: bool = False,
) -> None:
    """Reinstall dark-core-lib locally and optionally rebuild dependent services."""
    if no_cache:
        print("[WARNING] --no-cache only applies to Docker builds; ignoring it for core-lib.")

    core_path = require_component_path("core-lib", Path("components/libraries/dark-core-lib"))

    print("\n── Rebuild core-lib ──────────────────────────────────────────")
    setup_repo(target_dir=str(core_path), commands_str="")

    venv_pip = shared_venv_bin("pip")
    print("[INFO] Reinstalling dark-core-lib in editable mode...")
    try:
        run_shell(f"{venv_pip} install -e .", cwd=str(core_path))
    except SystemExit:
        print(
            "[WARNING] Editable install failed. "
            "Falling back to a regular install for compatibility."
        )
        run_shell(f"{venv_pip} install .", cwd=str(core_path))

    generate_core_lib_env_integration(core_path=core_path, env=env)
    print("[OK] Rebuild complete for core-lib.")

    if not with_dependents:
        print(
            "[INFO] Docker services that bundle core-lib keep their previous image. "
            "Use --with-dependents to rebuild admin, resolver, and minter too."
        )
        return

    for dependent in ("admin", "resolver", "minter"):
        rebuild_service_component(
            component=dependent,
            env=env,
            no_cache=no_cache,
            no_start=no_start,
            migrate=(dependent == "minter" and not skip_minter_migrate),
        )


def rebuild_component(args: argparse.Namespace, prefix: str, env: dict) -> None:
    """Dispatch selective rebuilds by component."""
    component = canonical_rebuild_component(args.component)

    if args.migrate and component not in {"minter", "core-lib"}:
        print("[WARNING] --migrate only affects minter rebuilds; ignoring it.")
    elif args.migrate:
        print("[INFO] --migrate is now the default for minter rebuilds.")

    if args.skip_migrate and args.migrate:
        print("[WARNING] --skip-migrate takes precedence over --migrate.")

    if args.skip_migrate and component not in {"minter", "core-lib"}:
        print("[WARNING] --skip-migrate only applies to minter rebuilds; ignoring it.")
    elif args.skip_migrate and component == "core-lib" and not args.with_dependents:
        print("[WARNING] --skip-migrate has no effect without --with-dependents.")

    if args.pull:
        maybe_pull_rebuild_component(component=component, prefix=prefix, env=env)

    if component == "core-lib":
        rebuild_core_lib(
            prefix=prefix,
            env=env,
            no_cache=args.no_cache,
            no_start=args.no_start,
            with_dependents=args.with_dependents,
            skip_minter_migrate=args.skip_migrate,
        )
        return

    if args.with_dependents:
        print("[WARNING] --with-dependents only applies to core-lib; ignoring it.")

    rebuild_service_component(
        component=component,
        env=env,
        no_cache=args.no_cache,
        no_start=args.no_start,
        migrate=(component == "minter" and not args.skip_migrate),
    )


def install_core_lib(prefix: str, env: dict) -> None:
    """Clone and set up dark-core-lib as the unified SDK component.

    Supports a legacy fallback to ORCHESTRATOR_* variables to avoid
    breaking existing .env files during migration.

    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    print("\n── Core Lib (Unified SDK) ─────────────────────────────────────")

    core_url_key = f"{prefix}_CORE_LIB_REPOSITORY_URL"
    core_branch_key = f"{prefix}_CORE_LIB_REPOSITORY_BRANCH"
    core_setup_key = f"{prefix}_CORE_LIB_SETUP"
    core_commands_key = f"{prefix}_CORE_LIB_COMMANDS"

    legacy_url_key = f"{prefix}_ORCHESTRATOR_REPOSITORY_URL"
    legacy_branch_key = f"{prefix}_ORCHESTRATOR_REPOSITORY_BRANCH"
    legacy_setup_key = f"{prefix}_ORCHESTRATOR_SETUP"
    legacy_commands_key = f"{prefix}_ORCHESTRATOR_COMMANDS"

    repo_url = get_url(env, core_url_key)
    using_legacy_vars = False
    if not repo_url:
        repo_url = get_url(env, legacy_url_key)
        using_legacy_vars = bool(repo_url)

    if not repo_url:
        print(
            f"[SKIP] Neither '{core_url_key}' nor '{legacy_url_key}' is set "
            "— skipping 'dark-core-lib'."
        )
        return

    if using_legacy_vars:
        print(
            f"[WARNING] Using legacy variable '{legacy_url_key}'. "
            f"Please migrate to '{core_url_key}'."
        )

    branch = env.get(core_branch_key, "").strip() or env.get(legacy_branch_key, "main").strip() or "main"
    setup_raw = env.get(core_setup_key, "").strip()
    if not setup_raw:
        setup_raw = env.get(legacy_setup_key, "True").strip()
    do_setup = setup_raw.lower() != "false"

    extra_commands = env.get(core_commands_key, "").strip() or env.get(legacy_commands_key, "").strip()

    core_path = Path("components/libraries/dark-core-lib")

    install_repo(
        name="dark-core-lib",
        repo_url=repo_url,
        branch=branch,
        target_dir=str(core_path),
    )

    if not do_setup:
        print("[INFO] SETUP=False — 'dark-core-lib' cloned, setup skipped.\n")
        return

    setup_repo(target_dir=str(core_path), commands_str="")

    core_abs = core_path.resolve()
    venv_pip = shared_venv_bin("pip")

    print("[INFO] Installing dark-core-lib in editable mode...")
    try:
        run_shell(f"{venv_pip} install -e .", cwd=str(core_abs))
    except SystemExit:
        print(
            "[WARNING] Editable install failed. "
            "Falling back to a regular install for compatibility."
        )
        run_shell(f"{venv_pip} install .", cwd=str(core_abs))

    generate_core_lib_env_integration(core_path=core_abs, env=env)

    if extra_commands:
        # Ensure any "pip ..." command uses the shared root venv pip.
        extra_commands = extra_commands.replace("pip ", f"{venv_pip} ")
        run_commands(extra_commands, cwd=str(core_abs))


def install_core_admin_api(prefix: str, env: dict) -> None:
    """Clone and set up dark-core-admin-api as an optional core service."""
    print("\n── Core Admin API ─────────────────────────────────────────────")

    url_key = f"{prefix}_CORE_ADMIN_API_REPOSITORY_URL"
    branch_key = f"{prefix}_CORE_ADMIN_API_REPOSITORY_BRANCH"
    setup_key = f"{prefix}_CORE_ADMIN_API_SETUP"
    commands_key = f"{prefix}_CORE_ADMIN_API_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping 'dark-core-admin-api'.")
        return

    branch = env.get(branch_key, "main").strip() or "main"
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "").strip()
    target = "components/services/dark-core-admin-api"
    target_path = Path(target).resolve()

    install_repo(
        name="dark-core-admin-api",
        repo_url=repo_url,
        branch=branch,
        target_dir=target,
    )

    generate_admin_api_env_integration(admin_api_path=target_path, env=env)

    if do_setup:
        setup_repo(target_dir=target, commands_str=commands)
    else:
        print("[INFO] SETUP=False — 'dark-core-admin-api' cloned, setup skipped.\n")
        return

    if commands_include_docker_compose_up(commands):
        print("[INFO] Admin API Docker startup already handled by configured commands.")
    else:
        start_admin_api_stack(admin_api_path=target_path)


def install_core_resolver_api(prefix: str, env: dict) -> None:
    """Clone and set up dark-core-resolver-api as an optional core service."""
    print("\n── Core Resolver API ──────────────────────────────────────────")

    url_key = f"{prefix}_RESOLVER_REPOSITORY_URL"
    branch_key = f"{prefix}_RESOLVER_REPOSITORY_BRANCH"
    setup_key = f"{prefix}_RESOLVER_SETUP"
    commands_key = f"{prefix}_RESOLVER_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping 'dark-core-resolver-api'.")
        return

    branch = env.get(branch_key, "main").strip() or "main"
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "").strip()
    target = "components/services/dark-core-resolver-api"
    target_path = Path(target).resolve()

    install_repo(
        name="dark-core-resolver-api",
        repo_url=repo_url,
        branch=branch,
        target_dir=target,
    )

    generate_resolver_api_env_integration(resolver_api_path=target_path, env=env)

    if not do_setup:
        print("[INFO] SETUP=False — 'dark-core-resolver-api' cloned, setup skipped.\n")
        return

    setup_repo(target_dir=target, commands_str="")

    venv_pip = shared_venv_bin("pip")

    print("[INFO] Installing dark-core-resolver-api in editable mode...")
    try:
        run_shell(f"{venv_pip} install -e .", cwd=str(target_path))
    except SystemExit:
        print(
            "[WARNING] Editable install failed. "
            "Falling back to a regular install for compatibility."
        )
        run_shell(f"{venv_pip} install .", cwd=str(target_path))

    if commands:
        commands = commands.replace("pip ", f"{venv_pip} ")
        run_commands(commands, cwd=str(target_path))

        if commands_include_docker_compose_up(commands):
            print("[INFO] Resolver API Docker startup already handled by configured commands.")
        else:
            start_resolver_api_stack(resolver_api_path=target_path)
    else:
        print("[INFO] No resolver COMMANDS configured. Trying Docker Compose startup by convention.")
        start_resolver_api_stack(resolver_api_path=target_path)


def install_dark_store_api(prefix: str, env: dict) -> None:
    """Clone and set up dark-store-api as an optional storage service."""
    print("\n── Store API ─────────────────────────────────────────────────")

    url_key = f"{prefix}_STORE_API_REPOSITORY_URL"
    branch_key = f"{prefix}_STORE_API_REPOSITORY_BRANCH"
    setup_key = f"{prefix}_STORE_API_SETUP"
    commands_key = f"{prefix}_STORE_API_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping 'dark-store-api'.")
        return

    branch = env.get(branch_key, "main").strip() or "main"
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "").strip()
    target = "components/services/dark-store-api"
    target_path = Path(target).resolve()

    install_repo(
        name="dark-store-api",
        repo_url=repo_url,
        branch=branch,
        target_dir=target,
    )

    generate_store_api_env_integration(store_api_path=target_path, env=env)

    if not do_setup:
        print("[INFO] SETUP=False — 'dark-store-api' cloned, setup skipped.\n")
        return

    setup_repo(target_dir=target, commands_str="")

    venv_pip = shared_venv_bin("pip")

    print("[INFO] Installing dark-store-api in editable mode...")
    try:
        run_shell(f"{venv_pip} install -e .", cwd=str(target_path))
    except SystemExit:
        print(
            "[WARNING] Editable install failed. "
            "Falling back to a regular install for compatibility."
        )
        run_shell(f"{venv_pip} install .", cwd=str(target_path))

    if commands:
        commands = commands.replace("pip ", f"{venv_pip} ")
        run_commands(commands, cwd=str(target_path))

        if commands_include_docker_compose_up(commands):
            print("[INFO] Store API Docker startup already handled by configured commands.")
        else:
            start_store_api_stack(store_api_path=target_path)
    else:
        print("[INFO] No store API COMMANDS configured. Trying Docker Compose startup by convention.")
        start_store_api_stack(store_api_path=target_path)


def install_dark_ipfs(prefix: str, env: dict) -> None:
    """Clone and set up dark-ipfs as the default IPFS backend."""
    print("\n── IPFS ──────────────────────────────────────────────────────")

    url_key = f"{prefix}_IPFS_REPOSITORY_URL"
    branch_key = f"{prefix}_IPFS_REPOSITORY_BRANCH"
    setup_key = f"{prefix}_IPFS_SETUP"
    commands_key = f"{prefix}_IPFS_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping 'dark-ipfs'.")
        return

    branch = env.get(branch_key, "main").strip() or "main"
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "make up").strip() or "make up"
    target = "components/blockchain/dark-ipfs"

    if platform.machine() != "x86_64":
        print(
            f"[INFO] Host architecture is {platform.machine()}. "
            "dark-ipfs Docker images are AMD64 — they will run via QEMU emulation. "
            "Ensure 'qemu-user-static' is installed and binfmt_misc is configured "
            "(docker run --privileged --rm tonistiigi/binfmt --install all)."
        )

    install_repo(
        name="dark-ipfs",
        repo_url=repo_url,
        branch=branch,
        target_dir=target,
    )

    if do_setup:
        ensure_dark_net()
        setup_repo(target_dir=target, commands_str=commands)
    else:
        print("[INFO] SETUP=False — 'dark-ipfs' cloned, setup skipped.\n")

# ─── Generic single-repo component installer ─────────────────────────────────


def install_single_component(name: str, prefix: str, env: dict) -> None:
    """Clone and set up a single-repository component.

    :param name: Component name in uppercase, matching the env variable segment
                 (currently used for ``MINTER``).
    :type name: str
    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    print(f"\n── {name.capitalize()} ──────────────────────────────────────────────")

    url_key      = f"{prefix}_{name}_REPOSITORY_URL"
    branch_key   = f"{prefix}_{name}_REPOSITORY_BRANCH"
    setup_key    = f"{prefix}_{name}_SETUP"
    commands_key = f"{prefix}_{name}_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping '{name.lower()}'.")
        return

    branch   = env.get(branch_key, "master").strip()
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "").strip()
    if not commands and name.upper() != "MINTER":
        commands = DEFAULT_COMMANDS
    target   = f"components/{name.lower()}"
    repo_name = name.lower()
    if name.upper() == "MINTER":
        target = "components/services/dark-core-minter-api"
        repo_name = "dark-core-minter-api"
    target_path = Path(target).resolve()

    install_repo(name=repo_name, repo_url=repo_url, branch=branch, target_dir=target)

    if name.upper() == "MINTER":
        generate_minter_env_integration(minter_path=target_path, env=env)

    if do_setup:
        setup_repo(target_dir=target, commands_str=commands)
    else:
        print(f"[INFO] SETUP=False — '{repo_name}' cloned, setup skipped.\n")

    if name.upper() == "MINTER" and do_setup:
        if commands_include_docker_compose_up(commands):
            print("[INFO] Minter Docker startup already handled by configured commands.")
        else:
            start_minter_stack(minter_path=target_path)


# ─── Dashboard installer ──────────────────────────────────────────────────────


def install_dashboard(prefix: str, env: dict) -> None:
    """Clone dashboard-web and run its own install.py.

    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    print("\n── Dashboard ─────────────────────────────────────────────────")

    url_key      = f"{prefix}_DASHBOARD_REPOSITORY_URL"
    branch_key   = f"{prefix}_DASHBOARD_REPOSITORY_BRANCH"
    setup_key    = f"{prefix}_DASHBOARD_SETUP"
    commands_key = f"{prefix}_DASHBOARD_COMMANDS"

    repo_url = get_url(env, url_key)
    if not repo_url:
        print(f"[SKIP] '{url_key}' is not set — skipping 'dashboard-web'.")
        return

    branch   = env.get(branch_key, "main").strip() or "main"
    do_setup = env.get(setup_key, "True").strip().lower() != "false"
    commands = env.get(commands_key, "").strip() or "python3 install.py"
    target   = "components/frontend/dashboard-web"

    install_repo(name="dashboard-web", repo_url=repo_url, branch=branch, target_dir=target)

    if not do_setup:
        print("[INFO] SETUP=False — 'dashboard-web' cloned, setup skipped.\n")
        return

    run_commands(commands, cwd=str(Path(target).resolve()))
    print("[OK] 'dashboard-web' is up.\n")


# ─── Profile installer ────────────────────────────────────────────────────────


def install_profile(prefix: str, env: dict) -> None:
    """Install all components for a given profile prefix.

    Components installed in order:

    1. Blockchain (``dark-env``, ``dark-dapp``, ``dark-explorador``)
    2. Core Lib
    3. Core Admin API
    4. dark-ipfs
    5. dark-store-api
    6. Core Resolver API
    7. Minter

    :param prefix: Environment variable prefix matching the active profile
                   (e.g. ``DEVELOPER``, ``SANDBOX``, ``PRODUCTION``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    components_str = env.get(f"{prefix}_INSTALL_COMPONENTS", "all").strip()
    components     = {c.strip().lower() for c in components_str.split(",")}
    install_all    = "all" in components
    install_bc     = install_all or "blockchain" in components
    install_ipfs   = install_all or "storage"    in components
    install_apps   = install_all or "apps"       in components

    if install_bc:
        blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
        if blockchain_host:
            print(f"[INFO] Blockchain tier is remote ({blockchain_host}) — skipping local install.")
        else:
            install_blockchain(prefix=prefix, env=env)
    else:
        print("[INFO] Blockchain not selected — skipping.")

    if install_apps:
        install_core_lib(prefix=prefix, env=env)
        install_core_admin_api(prefix=prefix, env=env)

    if install_ipfs:
        ipfs_host = env.get(f"{prefix}_IPFS_HOST", "").strip()
        if ipfs_host:
            print(f"[INFO] IPFS tier is remote ({ipfs_host}) — skipping local install.")
        else:
            install_dark_ipfs(prefix=prefix, env=env)
    else:
        print("[INFO] Storage not selected — skipping.")

    if install_apps:
        install_dark_store_api(prefix=prefix, env=env)
        install_core_resolver_api(prefix=prefix, env=env)
        install_single_component(name="MINTER", prefix=prefix, env=env)
        install_dashboard(prefix=prefix, env=env)


# ─── Setup wizard ────────────────────────────────────────────────────────────


def _ask_choice(question: str, options: list[str], default: int = 1) -> int:
    """Print a numbered menu and return the 1-based index of the chosen option."""
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if i == default else ""
        print(f"  [{i}] {opt}{marker}")
    while True:
        raw = input(f"\nChoice [1-{len(options)}]: ").strip()
        if raw == "" and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw)
        print(f"    Invalid option. Enter a number between 1 and {len(options)}.")


def _ask_text(question: str, default: str = "", required: bool = False) -> str:
    """Ask for free-form text input, accepting Enter to use the default."""
    hint = f" [{default}]" if default else ""
    while True:
        raw = input(f"  {question}{hint}: ").strip()
        value = raw if raw else default
        if required and not value:
            print("    This field is required.")
            continue
        return value


def _ask_confirm(question: str, default: bool = True) -> bool:
    """Ask a yes/no question and return True for yes."""
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {question} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


def _wizard_blockchain_remote_standard(prefix: str, host: str, env: dict) -> dict:
    """Standard remote blockchain: derive URLs from the host IP using default ports."""
    env[f"{prefix}_BLOCKCHAIN_HOST"] = host
    env["RPC_URL"] = f"http://{host}:8545"
    print(f"  [INFO] RPC URL set to http://{host}:8545 (default port).")
    print(  "  [INFO] Contract addresses will be read from .env.integration if available.")
    return env


def _wizard_blockchain_remote_advanced(prefix: str, host: str, env: dict) -> dict:
    """Advanced remote blockchain: collect all connection and contract details."""
    env[f"{prefix}_BLOCKCHAIN_HOST"] = host

    env["RPC_URL"] = _ask_text(
        "RPC URL (Node 1 endpoint or load balancer)",
        default=f"http://{host}:8545",
    )
    env["CHAIN_ID"] = _ask_text(
        "Chain ID",
        default=env.get("CHAIN_ID", "2025"),
    )

    extra = _ask_text(
        "Additional validator node IPs (comma-separated, min 2 more for fault tolerance)",
        default=env.get(f"{prefix}_BLOCKCHAIN_EXTRA_NODES", ""),
    )
    if extra:
        env[f"{prefix}_BLOCKCHAIN_EXTRA_NODES"] = extra

    enodes = _ask_text(
        "Enode URLs for peer discovery (comma-separated, optional)",
        default=env.get(f"{prefix}_BLOCKCHAIN_ENODES", ""),
    )
    if enodes:
        env[f"{prefix}_BLOCKCHAIN_ENODES"] = enodes

    print("\n  -- Contract addresses (from the blockchain server after dark-dapp deployment) --")
    dark_addr = _ask_text(
        "dARK contract address (DARK_CONTRACT_ADDRESS)",
        default=env.get(f"{prefix}_DARK_CONTRACT_ADDRESS", ""),
        required=True,
    )
    auth_addr = _ask_text(
        "Authority contract address (AUTHORITY_CONTRACT_ADDRESS)",
        default=env.get(f"{prefix}_AUTHORITY_CONTRACT_ADDRESS", ""),
        required=True,
    )
    env[f"{prefix}_DARK_CONTRACT_ADDRESS"]      = dark_addr
    env[f"{prefix}_AUTHORITY_CONTRACT_ADDRESS"] = auth_addr

    print("\n  -- Master wallet (exported from dark-env on the blockchain server) --")
    env["MASTER_WALLET_ADDRESS"] = _ask_text(
        "Master wallet address",
        default=env.get("MASTER_WALLET_ADDRESS", ""),
        required=True,
    )
    env["MASTER_PRIVATE_KEY"] = _ask_text(
        "Master private key",
        default=env.get("MASTER_PRIVATE_KEY", ""),
        required=True,
    )
    env["MASTER_PUBLIC_KEY"] = _ask_text(
        "Master public key",
        default=env.get("MASTER_PUBLIC_KEY", ""),
    )
    return env


def _wizard_apps_blockchain_info(prefix: str, env: dict) -> dict:
    """Collect or confirm blockchain connection details for apps-only install.

    Reads existing values from the loaded .env dict and falls back to
    deployed_contracts.ini when contract addresses are missing.  Only asks
    the user for what is still absent.
    """
    rpc_url        = env.get("RPC_URL", "").strip()
    chain_id       = env.get("CHAIN_ID", "2025").strip()
    dark_contract  = env.get(f"{prefix}_DARK_CONTRACT_ADDRESS", "").strip()
    auth_contract  = env.get(f"{prefix}_AUTHORITY_CONTRACT_ADDRESS", "").strip()
    master_key     = env.get("MASTER_PRIVATE_KEY", "").strip()
    master_wallet  = env.get("MASTER_WALLET_ADDRESS", "").strip()
    master_pub     = env.get("MASTER_PUBLIC_KEY", "").strip()
    blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()

    if not dark_contract or not auth_contract:
        ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
        dark_ini, auth_ini = read_deployed_contract_addresses(ini_path)
        if dark_ini or auth_ini:
            dark_contract = dark_contract or dark_ini
            auth_contract = auth_contract or auth_ini
            print("  [INFO] Contract addresses loaded from local deployed_contracts.ini.")

    all_present = all([rpc_url, dark_contract, auth_contract, master_key])

    if all_present and blockchain_host:
        print(f"\n  Blockchain configuration found in .env:")
        print(f"    Host          : {blockchain_host}")
        print(f"    RPC URL       : {rpc_url}")
        print(f"    dARK contract : {dark_contract}")
        print(f"    Auth contract : {auth_contract}")
        print(f"    Master wallet : {master_wallet or '(not set)'}")
        if _ask_confirm("Use this blockchain configuration?", default=True):
            env[f"{prefix}_DARK_CONTRACT_ADDRESS"]      = dark_contract
            env[f"{prefix}_AUTHORITY_CONTRACT_ADDRESS"] = auth_contract
            return env

    print("\n  Blockchain connection details (required for apps install):")
    host = _ask_text("Blockchain host (Node 1 IP or hostname)", default=blockchain_host, required=True)
    env[f"{prefix}_BLOCKCHAIN_HOST"] = host
    env["RPC_URL"]   = _ask_text("RPC URL",  default=rpc_url  or f"http://{host}:8545")
    env["CHAIN_ID"]  = _ask_text("Chain ID", default=chain_id)

    print("\n  Contract addresses (from deployed_contracts.ini on the blockchain server):")
    env[f"{prefix}_DARK_CONTRACT_ADDRESS"]      = _ask_text("dARK contract address",      default=dark_contract, required=True)
    env[f"{prefix}_AUTHORITY_CONTRACT_ADDRESS"] = _ask_text("Authority contract address", default=auth_contract,  required=True)

    print("\n  Master wallet (from dark-env on the blockchain server):")
    env["MASTER_PRIVATE_KEY"]    = _ask_text("Master private key",        default=master_key,    required=True)
    env["MASTER_WALLET_ADDRESS"] = _ask_text("Master wallet address",     default=master_wallet)
    env["MASTER_PUBLIC_KEY"]     = _ask_text("Master public key",         default=master_pub)

    return env


def _wizard_apps_ipfs_info(prefix: str, env: dict) -> dict:
    """Collect or confirm IPFS connection details for apps-only install.

    Reads existing values from the loaded .env dict.  If dark-ipfs is
    installed locally it is detected automatically.  Only asks the user for
    what is still absent.
    """
    ipfs_host        = env.get(f"{prefix}_IPFS_HOST", "").strip()
    ipfs_api_url     = env.get(f"{prefix}_IPFS_API_URL", "").strip()
    ipfs_cluster_url = env.get(f"{prefix}_IPFS_CLUSTER_URL", "").strip()

    if not ipfs_api_url and Path("components/blockchain/dark-ipfs").exists():
        ipfs_api_url     = "http://localhost:5001"
        ipfs_cluster_url = "http://localhost:9094"
        print("  [INFO] Local dark-ipfs installation detected — using localhost URLs.")

    if ipfs_api_url:
        print(f"\n  IPFS configuration found:")
        if ipfs_host:
            print(f"    Host          : {ipfs_host}")
        print(f"    IPFS API      : {ipfs_api_url}")
        print(f"    Cluster API   : {ipfs_cluster_url or '(not set)'}")
        if _ask_confirm("Use this IPFS configuration?", default=True):
            env[f"{prefix}_IPFS_API_URL"]     = ipfs_api_url
            if ipfs_cluster_url:
                env[f"{prefix}_IPFS_CLUSTER_URL"] = ipfs_cluster_url
            return env

    print("\n  IPFS connection details (required for store-api):")
    host = _ask_text("IPFS host (Node 1 IP or hostname)", default=ipfs_host, required=True)
    env[f"{prefix}_IPFS_HOST"]              = host
    env[f"{prefix}_IPFS_API_URL"]           = _ask_text("IPFS API URL",           default=f"http://{host}:5001")
    env[f"{prefix}_IPFS_CLUSTER_URL"]       = _ask_text("IPFS Cluster API URL",   default=f"http://{host}:9094")
    env[f"{prefix}_IPFS_CLUSTER_PROXY_URL"] = _ask_text("IPFS Cluster Proxy URL", default=f"http://{host}:9095")

    return env


_BLOCKCHAIN_REPO_DEFAULTS: dict = {
    "DARK_ENV":        "https://github.com/LA-Referencia-IOI/dark-env",
    "DARK_DAPP":       "https://github.com/LA-Referencia-IOI/dark-dapp",
    "DARK_EXPLORADOR": "https://github.com/LA-Referencia-IOI/dark-explorer.git",
}


def _wizard_blockchain_repos(prefix: str, env: dict) -> dict:
    """Ask for any blockchain repository URLs that are not yet configured."""
    missing = [
        (suffix, f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_URL", default)
        for suffix, default in _BLOCKCHAIN_REPO_DEFAULTS.items()
        if not env.get(f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_URL", "").strip()
    ]
    if not missing:
        return env
    print("\n  Blockchain repository URLs (Enter to accept defaults):")
    for suffix, key, default in missing:
        label = suffix.lower().replace("_", "-")
        env[key] = _ask_text(f"{label} repository URL", default=default)
    return env


def _wizard_blockchain_tier(prefix: str, env: dict) -> dict:
    """Collect blockchain tier topology and connection details interactively."""
    location = _ask_choice(
        "Where will the BLOCKCHAIN tier be installed?",
        [
            "This server  — install locally (single node)",
            "Remote server — already deployed or will be deployed separately",
        ],
    )
    if location == 1:
        for key in (
            f"{prefix}_BLOCKCHAIN_HOST",
            f"{prefix}_BLOCKCHAIN_EXTRA_NODES",
            f"{prefix}_BLOCKCHAIN_ENODES",
            f"{prefix}_DARK_CONTRACT_ADDRESS",
            f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
        ):
            env.pop(key, None)
        return _wizard_blockchain_repos(prefix=prefix, env=env)

    host = _ask_text("Node 1 IP or hostname (bootnode + RPC entry point)", required=True)

    mode = _ask_choice(
        "Configuration mode for remote blockchain:",
        [
            "Standard  — use defaults from .env.integration (ports 8545)",
            "Advanced  — enter all details manually (RPC, enodes, contracts, wallet)",
        ],
    )
    if mode == 1:
        return _wizard_blockchain_remote_standard(prefix=prefix, host=host, env=env)
    return _wizard_blockchain_remote_advanced(prefix=prefix, host=host, env=env)


def _wizard_ipfs_remote_standard(prefix: str, host: str, env: dict) -> dict:
    """Standard remote IPFS: derive URLs from the host IP using default ports."""
    env[f"{prefix}_IPFS_HOST"]        = host
    env[f"{prefix}_IPFS_API_URL"]     = f"http://{host}:5001"
    env[f"{prefix}_IPFS_CLUSTER_URL"] = f"http://{host}:9094"
    print(f"  [INFO] IPFS API set to http://{host}:5001 (default port).")
    print(f"  [INFO] IPFS Cluster set to http://{host}:9094 (default port).")
    return env


def _wizard_ipfs_remote_advanced(prefix: str, host: str, env: dict) -> dict:
    """Advanced remote IPFS: collect all API and cluster connection details."""
    env[f"{prefix}_IPFS_HOST"] = host

    extra = _ask_text(
        "Additional IPFS node IPs joining the cluster (comma-separated, optional)",
        default=env.get(f"{prefix}_IPFS_EXTRA_NODES", ""),
    )
    if extra:
        env[f"{prefix}_IPFS_EXTRA_NODES"] = extra

    env[f"{prefix}_IPFS_API_URL"] = _ask_text(
        "IPFS API URL",
        default=f"http://{host}:5001",
    )
    env[f"{prefix}_IPFS_CLUSTER_URL"] = _ask_text(
        "IPFS Cluster API URL",
        default=f"http://{host}:9094",
    )
    env[f"{prefix}_IPFS_CLUSTER_PROXY_URL"] = _ask_text(
        "IPFS Cluster Proxy URL",
        default=f"http://{host}:9095",
    )
    env["IPFS_ADD_MODE"] = _ask_text(
        "IPFS add mode (cluster_proxy / direct)",
        default=env.get("IPFS_ADD_MODE", "cluster_proxy"),
    )
    env["IPFS_CLUSTER_MIN_PEERS"] = _ask_text(
        "Minimum IPFS Cluster peers required",
        default=env.get("IPFS_CLUSTER_MIN_PEERS", "1"),
    )
    return env


def _wizard_ipfs_tier(prefix: str, env: dict) -> dict:
    """Collect IPFS tier topology and connection details interactively."""
    location = _ask_choice(
        "Where will the IPFS tier be installed?",
        [
            "This server  — install locally (single node)",
            "Remote server — already deployed or will be deployed separately",
        ],
    )
    if location == 1:
        for key in (
            f"{prefix}_IPFS_HOST",
            f"{prefix}_IPFS_EXTRA_NODES",
            f"{prefix}_IPFS_API_URL",
            f"{prefix}_IPFS_CLUSTER_URL",
            f"{prefix}_IPFS_CLUSTER_PROXY_URL",
        ):
            env.pop(key, None)
        return env

    host = _ask_text("Node 1 IP or hostname (primary IPFS node)", required=True)

    mode = _ask_choice(
        "Configuration mode for remote IPFS:",
        [
            "Standard  — use defaults from .env.integration (ports 5001 / 9094)",
            "Advanced  — enter all details manually (API, cluster, proxy, add mode)",
        ],
    )
    if mode == 1:
        return _wizard_ipfs_remote_standard(prefix=prefix, host=host, env=env)
    return _wizard_ipfs_remote_advanced(prefix=prefix, host=host, env=env)


def _print_wizard_summary(install_type: str, prefix: str, env: dict) -> None:
    """Print a summary of all wizard choices before proceeding."""
    print("\n" + "─" * 60)
    print("  Configuration Summary")
    print("─" * 60)
    print(f"  Profile         : {install_type.upper()}")
    components = env.get(f"{prefix}_INSTALL_COMPONENTS", "all")
    print(f"  Components      : {components}")
    print(f"  RPC URL         : {env.get('RPC_URL', '(local default)')}")

    blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "")
    if blockchain_host:
        print(f"  Blockchain      : remote — {blockchain_host}")
        extra = env.get(f"{prefix}_BLOCKCHAIN_EXTRA_NODES", "")
        if extra:
            print(f"  Extra nodes     : {extra}")
        enodes = env.get(f"{prefix}_BLOCKCHAIN_ENODES", "")
        if enodes:
            print(f"  Enodes          : {enodes}")
        dark_addr = env.get(f"{prefix}_DARK_CONTRACT_ADDRESS", "(not provided)")
        auth_addr = env.get(f"{prefix}_AUTHORITY_CONTRACT_ADDRESS", "(not provided)")
        print(f"  dARK contract   : {dark_addr}")
        print(f"  Auth contract   : {auth_addr}")
        wallet = env.get("MASTER_WALLET_ADDRESS", "")
        if wallet:
            print(f"  Master wallet   : {wallet}")
    else:
        print("  Blockchain      : local")

    ipfs_host = env.get(f"{prefix}_IPFS_HOST", "")
    if ipfs_host:
        print(f"  IPFS            : remote — {ipfs_host}")
        print(f"  IPFS API        : {env.get(f'{prefix}_IPFS_API_URL', '')}")
        print(f"  IPFS Cluster    : {env.get(f'{prefix}_IPFS_CLUSTER_URL', '')}")
        proxy = env.get(f"{prefix}_IPFS_CLUSTER_PROXY_URL", "")
        if proxy:
            print(f"  Cluster proxy   : {proxy}")
        extra = env.get(f"{prefix}_IPFS_EXTRA_NODES", "")
        if extra:
            print(f"  Extra IPFS nodes: {extra}")
    else:
        print("  IPFS            : local")
    print("─" * 60)


def run_setup_wizard(env: dict) -> dict:
    """Interactive setup wizard — collect profile and infrastructure topology.

    Prompts the user for the installation profile (developer / sandbox /
    production) and, for non-developer profiles, the location and connection
    details for the blockchain and IPFS tiers.  Writes the chosen values back
    to ``.env`` before returning the updated env dict.

    :param env: Environment dictionary loaded from ``.env``.
    :type env: dict
    :returns: Updated environment dictionary.
    :rtype: dict
    """
    print("\n" + "═" * 60)
    print("  dARK Deployer — Setup Wizard")
    print("═" * 60)

    type_choice = _ask_choice(
        "Which setup profile do you want to install?",
        [
            "Developer  — full stack on a single local server",
            "Sandbox    — test environment with decoupled infrastructure",
            "Production — production environment with decoupled infrastructure",
        ],
        default={"developer": 1, "sandbox": 2, "production": 3}.get(
            env.get("TYPE", "developer").lower(), 1
        ),
    )

    install_type = {1: "developer", 2: "sandbox", 3: "production"}[type_choice]
    prefix = PROFILE_PREFIXES[install_type]
    env["TYPE"] = install_type

    if install_type in ("sandbox", "production"):
        print(f"\n  Configuring infrastructure for {install_type.upper()} profile:")

        component_choice = _ask_choice(
            "Which components will be installed on this server?",
            [
                "All               — blockchain + apps + storage",
                "Apps only         — application services (blockchain and IPFS are elsewhere)",
                "Blockchain only   — only the blockchain tier",
                "Storage only      — only the IPFS storage tier",
            ],
            default={"all": 1, "apps": 2, "blockchain": 3, "storage": 4}.get(
                env.get(f"{prefix}_INSTALL_COMPONENTS", "all").strip(), 1
            ),
        )
        component_map = {1: "all", 2: "apps", 3: "blockchain", 4: "storage"}
        selected = component_map[component_choice]
        env[f"{prefix}_INSTALL_COMPONENTS"] = selected

        if selected == "all":
            env = _wizard_blockchain_tier(prefix=prefix, env=env)
            env = _wizard_ipfs_tier(prefix=prefix, env=env)
        elif selected == "apps":
            env = _wizard_apps_blockchain_info(prefix=prefix, env=env)
            env = _wizard_apps_ipfs_info(prefix=prefix, env=env)
        elif selected == "blockchain":
            for key in (
                f"{prefix}_BLOCKCHAIN_HOST",
                f"{prefix}_BLOCKCHAIN_EXTRA_NODES",
                f"{prefix}_BLOCKCHAIN_ENODES",
                f"{prefix}_DARK_CONTRACT_ADDRESS",
                f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
            ):
                env.pop(key, None)
            env = _wizard_blockchain_repos(prefix=prefix, env=env)
        elif selected == "storage":
            for key in (
                f"{prefix}_IPFS_HOST",
                f"{prefix}_IPFS_EXTRA_NODES",
                f"{prefix}_IPFS_API_URL",
                f"{prefix}_IPFS_CLUSTER_URL",
                f"{prefix}_IPFS_CLUSTER_PROXY_URL",
            ):
                env.pop(key, None)

    _print_wizard_summary(install_type=install_type, prefix=prefix, env=env)

    if not _ask_confirm("Save configuration to .env and start installation?", default=True):
        print("\n[INFO] Installation cancelled by user.")
        sys.exit(0)

    update_env_file(".env", env)
    print("[OK] Configuration saved to .env.\n")
    return env


# ─── Entry point ─────────────────────────────────────────────────────────────

#: Maps each TYPE value to its environment variable prefix.
PROFILE_PREFIXES: dict = {
    "developer":  "DEVELOPER",
    "sandbox":    "SANDBOX",
    "production": "PRODUCTION",
}


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the installer command-line parser."""
    parser = argparse.ArgumentParser(
        description="Install or selectively rebuild dARK deployer components.",
    )
    subparsers = parser.add_subparsers(dest="command")

    rebuild_parser = subparsers.add_parser(
        "rebuild",
        help="Rebuild one installed component without reinstalling the whole stack.",
    )
    rebuild_parser.add_argument(
        "component",
        help=(
            "Component to rebuild: store-api, minter, resolver, admin, or core-lib. "
            "Common aliases such as store, minter-api, and dark-core-lib are accepted."
        ),
    )
    rebuild_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Pass --no-cache to Docker Compose builds.",
    )
    rebuild_parser.add_argument(
        "--no-start",
        action="store_true",
        help="Build/regenerate local artifacts but do not restart containers.",
    )
    rebuild_parser.add_argument(
        "--migrate",
        action="store_true",
        help="Deprecated compatibility flag. Minter rebuilds run migrations by default.",
    )
    rebuild_parser.add_argument(
        "--skip-migrate",
        action="store_true",
        help="Skip minter database migrations during rebuild.",
    )
    rebuild_parser.add_argument(
        "--pull",
        action="store_true",
        help="Pull or clone the configured repository before rebuilding.",
    )
    rebuild_parser.add_argument(
        "--with-dependents",
        action="store_true",
        help="For core-lib only: also rebuild admin, resolver, and minter Docker images.",
    )

    return parser


def resolve_profile_prefix(env: dict) -> tuple[str, str]:
    """Resolve TYPE from the loaded env into its installer prefix."""
    install_type = env.get("TYPE", "").lower()

    if not install_type:
        print("[ERROR] TYPE variable is not set in .env")
        sys.exit(1)

    prefix = PROFILE_PREFIXES.get(install_type)
    if prefix is None:
        valid = ", ".join(PROFILE_PREFIXES.keys())
        print(f"[ERROR] Unknown TYPE '{install_type}'. Valid options: {valid}")
        sys.exit(1)

    return install_type, prefix


def main() -> None:
    """Entry point for the dark-developer installer.

    Loads the ``.env`` file, reads the ``TYPE`` variable, and dispatches
    execution to the appropriate profile installer.

    :raises SystemExit: If ``TYPE`` is missing or not a recognised profile.
    """
    _ensure_docker_group()

    print("=== dark-developer installer ===\n")

    ensure_supported_python()
    args = build_arg_parser().parse_args()
    env = load_env()

    if args.command != "rebuild":
        env = run_setup_wizard(env)

    install_type, prefix = resolve_profile_prefix(env)

    print(f"[INFO] Profile: {install_type.upper()}")

    if args.command == "rebuild":
        rebuild_component(args=args, prefix=prefix, env=env)
        print("=== Rebuild complete ===")
        return

    ensure_docker_running()
    install_profile(prefix=prefix, env=env)
    print_install_summary(prefix=prefix, env=env)

    print("=== Installation complete ===")


if __name__ == "__main__":
    main()
