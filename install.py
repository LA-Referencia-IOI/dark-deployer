#!/usr/bin/env python3
"""
dark-developer installer.

Reads the ``TYPE`` variable from the ``.env`` file and clones all
subcomponent repositories into the ``components/`` directory, then
runs the per-repo commands defined in the ``COMMANDS`` variables.

Usage::

    python install.py
    python install.py rebuild store-api
    python install.py storage audit
    python install.py storage reconcile

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

Preferred command format in ``.env``::

    {PREFIX}_{COMPONENT}_COMMANDS_JSON=["cmd1","cmd2","producer | consumer"]

Legacy ``COMMANDS=cmd1|cmd2`` values remain supported. JSON commands are
executed sequentially inside the cloned repository directory and preserve
shell syntax such as pipes, globs, and redirects within each command.

Selective rebuilds::

    python install.py rebuild store-api
    python install.py rebuild minter
    python install.py rebuild minter --skip-migrate
    python install.py rebuild core-lib --with-dependents
"""

import argparse
import configparser
import grp
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from dark_deployer.commands import get_commands, parse_commands
from dark_deployer.files import parse_env_file, write_env_secure, write_text_secure
from dark_deployer.process import (
    redact_url_credentials,
    run_command,
    run_shell,
    strip_url_credentials,
)
from dark_deployer.storage import (
    StoragePeer,
    StorageTopology,
    StorageTopologyError,
    cluster_request,
    entry_cid,
    load_storage_topology,
    node_environment,
    pin_entries,
    pinned_peer_ids,
    store_api_environment,
)

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
COMPONENT_LOCK_PATH = PROJECT_ROOT / "components.lock.json"
COMPONENT_INSTALL_PATHS = {
    "dark-env": "components/blockchain/dark-env",
    "dark-dapp": "components/blockchain/dark-dapp",
    "dark-explorador": "components/blockchain/dark-explorador",
    "dark-ipfs": "components/storage/dark-ipfs",
    "dark-core-lib": "components/libraries/dark-core-lib",
    "dark-core-admin-api": "components/services/dark-core-admin-api",
    "dark-core-resolver-api": "components/services/dark-core-resolver-api",
    "dark-store-api": "components/services/dark-store-api",
    "dark-core-minter-api": "components/services/dark-core-minter-api",
    "dashboard-web": "components/frontend/dashboard-web",
}
_LEGACY_EXAMPLE_PRIVATE_KEY_SHA256 = (
    "c54ae1c9bcee975e5b1409b3b4129b5fadcb0d61ff945a52e0a6d192c2d37ea7"
)


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


def load_env(filepath: str = ".env") -> dict:
    """Parse a required ``.env`` file and return its contents."""
    path = Path(filepath)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return parse_env_file(path, required=True)


def load_optional_env(filepath: Path) -> dict:
    """Parse an optional env-style file, returning an empty dict if absent."""
    return parse_env_file(filepath, required=False)


def load_component_locks() -> dict:
    """Load and validate immutable component commit locks."""
    if not COMPONENT_LOCK_PATH.exists():
        return {}
    try:
        document = json.loads(COMPONENT_LOCK_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Invalid '{COMPONENT_LOCK_PATH.name}': {exc}")
        sys.exit(1)

    if document.get("version") != 1 or not isinstance(document.get("components"), dict):
        print(f"[ERROR] '{COMPONENT_LOCK_PATH.name}' must use schema version 1.")
        sys.exit(1)

    for name, entry in document["components"].items():
        commit = entry.get("commit", "") if isinstance(entry, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            print(f"[ERROR] Invalid locked commit for component '{name}'.")
            sys.exit(1)
    return document["components"]


def generate_component_lock(check: bool = False) -> None:
    """Generate or verify commit locks for all installed component repositories."""
    components = {}
    for name, relative_path in COMPONENT_INSTALL_PATHS.items():
        path = PROJECT_ROOT / relative_path
        if not (path / ".git").exists():
            continue
        origin = run_command(
            ["git", "remote", "get-url", "origin"],
            cwd=str(path),
            capture_output=True,
        ).stdout.strip()
        commit = run_command(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path),
            capture_output=True,
        ).stdout.strip()
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(path),
            capture_output=True,
            text=True,
        )
        components[name] = {
            "repository": strip_url_credentials(origin),
            "commit": commit,
            "branch": branch_result.stdout.strip(),
        }

    document = {"version": 1, "components": components}
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if check:
        if not COMPONENT_LOCK_PATH.exists():
            print(f"[ERROR] '{COMPONENT_LOCK_PATH}' does not exist.")
            sys.exit(1)
        locked = load_component_locks()
        mismatches = []
        for name, entry in locked.items():
            installed = components.get(name)
            if not installed:
                mismatches.append(f"{name}: not installed")
                continue
            if installed["commit"] != entry["commit"]:
                mismatches.append(
                    f"{name}: {installed['commit']} != {entry['commit']}"
                )
        unlocked = sorted(set(components) - set(locked))
        mismatches.extend(f"{name}: installed but not locked" for name in unlocked)
        if mismatches:
            print("[ERROR] Component lock verification failed:")
            for mismatch in mismatches:
                print(f"  - {mismatch}")
            sys.exit(1)
        print("[OK] components.lock.json matches all installed component commits.")
        return

    write_text_secure(COMPONENT_LOCK_PATH, content, mode=0o644)
    print(f"[OK] Locked {len(components)} installed components in '{COMPONENT_LOCK_PATH}'.")


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
    def is_running(worker: dict) -> bool:
        # Current minter versions expose ``alive`` and ``status``. Keep the
        # legacy ``running`` flag compatible with older component releases.
        return bool(
            worker.get("running")
            or worker.get("alive")
            or worker.get("status") == "running"
        )

    metadata_running = is_running(workers.get("metadata") or {})
    chain_running = is_running(workers.get("chain") or {})
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


def storage_probe_url(
    topology: StorageTopology,
    configured_url: str,
    vpn_address: str,
    port: int,
) -> str:
    """Return a host-reachable health URL for a storage endpoint.

    The generated single-node developer topology deliberately gives
    application containers Docker DNS aliases. Those aliases are not
    resolvable by the host running the installer, while the same services are
    published on loopback for operations and health checks.
    """
    if topology.development_single_node:
        return f"http://{vpn_address}:{port}"
    return configured_url


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
    install_bc, install_storage, install_apps = _selected_tiers(prefix, env)

    rpc_url = env.get("RPC_URL", "http://localhost:8545").strip() or "http://localhost:8545"
    explorer_port = env.get("EXPLORER_PORT", "25000").strip() or "25000"
    explorer_url = f"http://localhost:{explorer_port}"
    admin_url = "http://localhost:8000"
    resolver_url = "http://localhost:8002"
    store_api_url = "http://localhost:8003"
    minter_url = "http://localhost:8001"

    if install_bc or install_apps:
        print(f"- Blockchain RPC: {rpc_url} [{probe_rpc_block(rpc_url)}]")

    explorer_selected = install_bc and bool(get_url(env, f"{prefix}_BLOCKCHAIN_DARK_EXPLORADOR_REPOSITORY_URL"))
    if explorer_selected:
        print(f"- Block Explorer: {explorer_url} [{probe_http_status(explorer_url)}]")

    admin_selected = install_apps and bool(get_url(env, f"{prefix}_CORE_ADMIN_API_REPOSITORY_URL"))
    if admin_selected:
        print(
            f"- Core Admin API: {admin_url} "
            f"[health: {probe_http_status(f'{admin_url}/health')}] "
            f"[docs: {admin_url}/docs]"
        )

    if install_storage or install_apps:
        topology = configured_storage_topology(prefix, env)
        site_id = env.get(f"{prefix}_STORAGE_SITE_ID", "").strip()
        for index, peer in enumerate(topology.site_peers(site_id), 1):
            ipfs_probe_url = storage_probe_url(
                topology, peer.ipfs_api_url, peer.vpn_address, 5001
            )
            cluster_probe_url = storage_probe_url(
                topology, peer.cluster_api_url, peer.vpn_address, 9094
            )
            print(
                f"- Site IPFS {index}: {peer.ipfs_api_url} "
                f"[{probe_ipfs_api_status(ipfs_probe_url)}]"
            )
            print(
                f"- Site Cluster {index}: {peer.cluster_api_url} "
                f"[{probe_http_status(f'{cluster_probe_url}/id')}]"
            )

    store_api_selected = install_apps and bool(get_url(env, f"{prefix}_STORE_API_REPOSITORY_URL"))
    if store_api_selected:
        print(
            f"- Store API: {store_api_url} "
            f"[health: {probe_http_status(f'{store_api_url}/health')}] "
            f"[docs: {store_api_url}/docs]"
        )

    resolver_selected = install_apps and bool(get_url(env, f"{prefix}_RESOLVER_REPOSITORY_URL"))
    if resolver_selected:
        print(
            f"- Core Resolver API: {resolver_url} "
            f"[health: {probe_http_status(f'{resolver_url}/health')}] "
            f"[docs: {resolver_url}/docs]"
        )

    minter_selected = install_apps and bool(get_url(env, f"{prefix}_MINTER_REPOSITORY_URL"))
    if minter_selected:
        print(
            f"- Core Minter API: {minter_url} "
            f"[health: {probe_http_status(f'{minter_url}/health')}] "
            f"[workers: {probe_minter_worker_status(minter_url)}] "
            f"[docs: {minter_url}/docs]"
        )

    core_lib_selected = install_apps and bool(
        get_url(env, f"{prefix}_CORE_LIB_REPOSITORY_URL")
        or get_url(env, f"{prefix}_ORCHESTRATOR_REPOSITORY_URL")
    )
    if core_lib_selected:
        core_env_path = Path("components/libraries/dark-core-lib/.env.integration")
        print(f"- Core Lib env: {core_env_path}")


def run_commands(commands: str | list[str], cwd: str) -> None:
    """Execute a normalized sequence of shell commands.

    :param commands: JSON/list commands or a legacy ``|``-separated string.
    :param cwd: Working directory in which every command is executed.
    :type cwd: str
    """
    for command in parse_commands(commands):
        run_shell(command, cwd=cwd)


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


def get_role_private_key(env: dict, role: str) -> str:
    """Resolve a role key from a direct value, mounted file, or legacy master key."""
    role_key = f"{role.upper()}_PRIVATE_KEY"
    direct = env.get(role_key, "").strip()
    if direct:
        return direct

    file_value = env.get(f"{role_key}_FILE", "").strip()
    if file_value:
        secret_path = Path(file_value)
        if not secret_path.is_absolute():
            secret_path = PROJECT_ROOT / secret_path
        try:
            return secret_path.read_text().strip()
        except OSError as exc:
            print(f"[ERROR] Cannot read secret file for {role_key}: {exc}")
            sys.exit(1)

    # MASTER_PRIVATE_KEY remains a compatibility fallback for existing installs.
    return env.get("MASTER_PRIVATE_KEY", "").strip()


def validate_private_key(private_key: str, profile: str) -> None:
    """Reject malformed keys and the private key published by older examples."""
    normalized = private_key.strip().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized):
        print("[ERROR] MASTER_PRIVATE_KEY must contain exactly 32 bytes of hexadecimal data.")
        sys.exit(1)

    digest = hashlib.sha256(bytes.fromhex(normalized)).hexdigest()
    if profile != "developer" and digest == _LEGACY_EXAMPLE_PRIVATE_KEY_SHA256:
        print(
            "[ERROR] Refusing the private key published by an older .env.example "
            f"for the {profile.upper()} profile. Generate and authorize a new signer key."
        )
        sys.exit(1)


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
    path = Path(env_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    try:
        with open(path, "r") as f:
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

    write_text_secure(path, "".join(new_lines))


# ─── Repository installer ─────────────────────────────────────────────────────

#: Default commands used when a repo does not define its own COMMANDS variable.
DEFAULT_COMMANDS: list[str] = [
    "chmod +x setup.sh",
    "chmod +x scripts/*.sh",
    "./setup.sh",
    "docker compose up -d",
]


def install_repo(name: str, repo_url: str, branch: str, target_dir: str) -> None:
    """Clone a repository or pull the latest changes if it already exists.

    Existing repositories are switched to the configured branch and advanced
    only by fast-forward. Otherwise, the repository is cloned from ``repo_url``.

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
        url = strip_url_credentials(url)
        https_match = re.match(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if https_match:
            return f"{https_match.group(1)}/{https_match.group(2)}"

        ssh_match = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url)
        if ssh_match:
            return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

        return None

    def build_repo_url_candidates(url: str) -> list[str]:
        """Build URL candidates, preserving the configured transport first."""
        candidates = [url]
        ssh_url = github_https_to_ssh(url)
        https_url = github_ssh_to_https(url)

        if ssh_url:
            candidates.append(ssh_url)
        elif https_url:
            candidates.append(https_url)

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
    lock_entry = load_component_locks().get(name, {})
    locked_commit = lock_entry.get("commit", "")
    locked_repository = lock_entry.get("repository", "")
    if locked_repository:
        locked_slug = github_repo_slug(locked_repository)
        configured_slug = github_repo_slug(repo_url)
        same_locked_repo = (
            locked_slug == configured_slug
            if locked_slug and configured_slug
            else locked_repository.rstrip("/").removesuffix(".git")
            == repo_url.rstrip("/").removesuffix(".git")
        )
        if not same_locked_repo:
            print(f"[ERROR] Lock entry for '{name}' belongs to a different repository.")
            sys.exit(1)

    if target.exists():
        if not target.is_dir() or not (target / ".git").exists():
            print(f"[ERROR] Existing target '{target}' is not a Git repository.")
            sys.exit(1)

        print(f"[INFO] '{target_dir}' already exists — updating deterministically...")
        current_origin = run_command(
            ["git", "remote", "get-url", "origin"],
            cwd=str(target),
            capture_output=True,
        )
        current_origin_url = current_origin.stdout.strip()
        current_slug = github_repo_slug(current_origin_url)
        configured_slug = github_repo_slug(repo_url)
        same_repo = (
            current_slug == configured_slug
            if current_slug and configured_slug
            else current_origin_url.rstrip("/").removesuffix(".git")
            == repo_url.rstrip("/").removesuffix(".git")
        )
        if not same_repo:
            print(
                f"[ERROR] '{target}' points to a different origin.\n"
                f"        Current:    {redact_url_credentials(current_origin_url)}\n"
                f"        Configured: {redact_url_credentials(repo_url)}"
            )
            sys.exit(1)

        repo_candidates = [current_origin_url] + [
            candidate for candidate in repo_candidates if candidate != current_origin_url
        ]

        fetch_success = False
        last_exc: Optional[SystemExit] = None
        for candidate_url in repo_candidates:
            if not current_origin_url or current_origin_url != candidate_url:
                print(
                    "[INFO] Updating origin URL to "
                    f"'{redact_url_credentials(candidate_url)}'..."
                )
                run_command(
                    ["git", "remote", "set-url", "origin", candidate_url],
                    cwd=str(target),
                )
                current_origin_url = candidate_url

            try:
                run_command(["git", "fetch", "origin", branch], cwd=str(target))
                fetch_success = True
                break
            except SystemExit as exc:
                last_exc = exc
                print(
                    "[WARNING] Fetch failed for remote "
                    f"'{redact_url_credentials(candidate_url)}'. Trying next candidate..."
                )

        if not fetch_success and last_exc is not None:
            raise last_exc

        if locked_commit:
            commit_exists = subprocess.run(
                ["git", "cat-file", "-e", f"{locked_commit}^{{commit}}"],
                cwd=str(target),
            )
            if commit_exists.returncode != 0:
                run_command(["git", "fetch", "origin", locked_commit], cwd=str(target))
            run_command(["git", "checkout", "--detach", locked_commit], cwd=str(target))
            print(f"[INFO] '{name}' pinned to commit {locked_commit}.")
        else:
            local_branch = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=str(target),
            )
            if local_branch.returncode == 0:
                run_command(["git", "switch", branch], cwd=str(target))
            else:
                run_command(
                    ["git", "switch", "--create", branch, "--track", f"origin/{branch}"],
                    cwd=str(target),
                )
            run_command(
                ["git", "merge", "--ff-only", f"origin/{branch}"],
                cwd=str(target),
            )
    else:
        print(
            f"[INFO] Cloning '{name}' from {redact_url_credentials(repo_url)} "
            f"(branch: {branch})..."
        )
        clone_success = False
        last_exc: Optional[SystemExit] = None
        for candidate_url in repo_candidates:
            try:
                run_command(
                    [
                        "git", "clone", "--branch", branch, "--single-branch",
                        "--", candidate_url, str(target),
                    ]
                )
                clone_success = True
                break
            except SystemExit as exc:
                last_exc = exc
                print(
                    "[WARNING] Clone failed for URL "
                    f"'{redact_url_credentials(candidate_url)}'. Trying next candidate..."
                )

        if not clone_success and last_exc is not None:
            raise last_exc

        if locked_commit:
            commit_exists = subprocess.run(
                ["git", "cat-file", "-e", f"{locked_commit}^{{commit}}"],
                cwd=str(target),
            )
            if commit_exists.returncode != 0:
                run_command(["git", "fetch", "origin", locked_commit], cwd=str(target))
            run_command(["git", "checkout", "--detach", locked_commit], cwd=str(target))
            print(f"[INFO] '{name}' pinned to commit {locked_commit}.")

    print(f"[OK] '{name}' ready at '{target_dir}'.\n")


def setup_repo(target_dir: str, commands_str: str | list[str]) -> None:
    """Run the post-clone setup for a repository.

    If a ``requirements.txt`` is present in ``target_dir``, dependencies are
    installed into the shared root virtual environment (``./venv``) before
    any other commands run.

    The remaining setup commands are then executed in sequence as defined
    by ``commands_str``.

    :param target_dir: Path to the cloned repository directory.
    :type target_dir: str
    :param commands_str: Commands to run inside ``target_dir``.
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
    private_key = get_role_private_key(env, "deployer")
    public_key  = env.get("MASTER_PUBLIC_KEY", "").strip()
    address     = env.get("MASTER_WALLET_ADDRESS", "").strip()

    if not rpc_url:
        print("[ERROR] 'RPC_URL' is not set in .env")
        sys.exit(1)
    if not chain_id:
        print("[ERROR] 'CHAIN_ID' is not set in .env")
        sys.exit(1)
    if not private_key or private_key == "0x":
        print("[ERROR] DEPLOYER_PRIVATE_KEY (or legacy MASTER_PRIVATE_KEY) is not set")
        sys.exit(1)
    validate_private_key(private_key, env.get("TYPE", "developer").lower())

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
    with tempfile.TemporaryFile(mode="w+") as temporary_config:
        config.write(temporary_config)
        temporary_config.seek(0)
        write_text_secure(config_path, temporary_config.read())
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
        commands = get_commands(env, commands_key, DEFAULT_COMMANDS)
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


_DEPLOYED_CONTRACTS_INI_PATH = PROJECT_ROOT / (
    "components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini"
)

_ROOT_ENV_INTEGRATION = PROJECT_ROOT / ".env.integration"
_ROOT_ENV_INTEGRATION_SECRETS = PROJECT_ROOT / ".env.integration.secrets"


def find_deployed_contracts_ini() -> tuple[str, str]:
    """Read deployed_contracts.ini from the canonical path inside components/."""
    return read_deployed_contract_addresses(_DEPLOYED_CONTRACTS_INI_PATH)


def read_deployed_contract_abis(ini_path: Path) -> tuple[str, str]:
    """Read dARK and Authority ABI JSON from deployed_contracts.ini, compacted to one line.

    Returns empty strings when the file is absent or the abi field cannot be parsed.
    """
    dark_abi_json = ""
    authority_abi_json = ""

    if not ini_path.exists():
        return dark_abi_json, authority_abi_json

    deployed = configparser.ConfigParser()
    deployed.read(ini_path)

    section_map = {section.lower(): section for section in deployed.sections()}

    def _read_abi(section_name: str) -> str:
        raw = deployed.get(section_name, "abi", fallback="").strip()
        if not raw:
            return ""
        try:
            return json.dumps(json.loads(raw), separators=(",", ":"))
        except (json.JSONDecodeError, ValueError):
            return ""

    dark_section = section_map.get("dark")
    if dark_section:
        dark_abi_json = _read_abi(dark_section)

    authority_section = section_map.get("authority")
    if authority_section:
        authority_abi_json = _read_abi(authority_section)

    return dark_abi_json, authority_abi_json


# ─── Root .env.integration helpers ───────────────────────────────────────────

def _write_root_env_integration(new_vars: dict) -> None:
    existing: dict = {}
    if _ROOT_ENV_INTEGRATION.exists():
        existing = load_optional_env(_ROOT_ENV_INTEGRATION)
    existing.update(new_vars)
    # Private keys belong exclusively in .env.integration.secrets.
    existing.pop("DARK_ADMIN_PRIVATE_KEY", None)
    existing.pop("DARK_MINTER_PRIVATE_KEY", None)
    write_env_secure(_ROOT_ENV_INTEGRATION, existing)


def _write_root_env_integration_secrets(new_vars: dict) -> None:
    existing: dict = {}
    if _ROOT_ENV_INTEGRATION_SECRETS.exists():
        existing = load_optional_env(_ROOT_ENV_INTEGRATION_SECRETS)
    existing.update(new_vars)
    write_env_secure(_ROOT_ENV_INTEGRATION_SECRETS, existing)


def _generate_root_env_integration_blockchain(env: dict) -> None:
    dark_contract, authority_contract = resolve_contract_addresses(env)
    dark_abi_json, authority_abi_json = read_deployed_contract_abis(_DEPLOYED_CONTRACTS_INI_PATH)

    vars: dict = {
        "DARK_RPC_URL":           env.get("RPC_URL", "http://localhost:8545").strip(),
        "DARK_CHAIN_ID":          env.get("CHAIN_ID", "2025").strip(),
        "DARK_CONTRACT_ADDRESS":  dark_contract,
        "DARK_AUTHORITY_ADDRESS": authority_contract,
    }
    if dark_abi_json:
        vars["DARK_ABI_JSON"] = dark_abi_json
    if authority_abi_json:
        vars["AUTHORITY_ABI_JSON"] = authority_abi_json

    _write_root_env_integration(vars)
    admin_key = get_role_private_key(env, "admin")
    minter_key = get_role_private_key(env, "minter")
    secret_values = {}
    if admin_key:
        secret_values["DARK_ADMIN_PRIVATE_KEY"] = admin_key
    if minter_key:
        secret_values["DARK_MINTER_PRIVATE_KEY"] = minter_key
    if secret_values:
        _write_root_env_integration_secrets(secret_values)

    abi_note = " (+ ABI)" if dark_abi_json else ""
    print(
        f"[OK] Generated root '.env.integration' with blockchain connection vars{abi_note}.\n"
        "     Copy this public file to each apps server.\n"
        "     Copy '.env.integration.secrets' only to an apps server that must sign transactions."
    )


def _update_root_env_integration_storage(env: dict, prefix: str) -> None:
    store_api_url = env.get(f"{prefix}_STORE_API_URL", "").strip()
    if not store_api_url:
        store_api_url = "http://store-api:8003"
    _write_root_env_integration({
        "METADATA_STORAGE_TYPE":  "store_api",
        "METADATA_STORE_API_URL": store_api_url,
    })
    print("[OK] Updated root '.env.integration' with the site-local Store API URL.")


def validate_root_env_integration() -> None:
    if _ROOT_ENV_INTEGRATION.exists():
        return
    print(
        "\n[ERROR] Root '.env.integration' not found.\n"
        "\n  This file is generated by the blockchain installation."
        " Copy it to this server before running the services install.\n"
        "\n  Steps:\n"
        "    1. On the blockchain server: run the installer — it generates .env.integration\n"
        "       and .env.integration.secrets\n"
        "    2. Copy the public file and signer secret directly to this apps server\n"
        "    3. Re-run the installer\n"
        f"\n  Expected path: {_ROOT_ENV_INTEGRATION.resolve()}"
    )
    sys.exit(1)


def _merge_root_env_integration_into_env(
    env: dict,
    prefix: str,
    authoritative: bool = False,
) -> None:
    """Load root .env.integration and back-map Docker-facing var names into the env dict.

    Blank values are always filled. In apps-only deployments the handoff is
    authoritative, preventing stale values copied from .env.example from
    shadowing the deployed infrastructure state.
    """
    if not _ROOT_ENV_INTEGRATION.exists():
        return
    root = load_optional_env(_ROOT_ENV_INTEGRATION)
    secrets = load_optional_env(_ROOT_ENV_INTEGRATION_SECRETS)

    # Backward compatibility for handoff files generated by older releases.
    if not secrets.get("DARK_ADMIN_PRIVATE_KEY") and root.get("DARK_ADMIN_PRIVATE_KEY"):
        secrets["DARK_ADMIN_PRIVATE_KEY"] = root["DARK_ADMIN_PRIVATE_KEY"]
        print(
            "[WARNING] Legacy '.env.integration' contains a private key. "
            "Regenerate it to split public configuration from secrets."
        )

    # Older secret handoffs carried one signer. Preserve compatibility by
    # assigning it to both roles until the operator rotates separate keys.
    if secrets.get("DARK_ADMIN_PRIVATE_KEY") and not secrets.get("DARK_MINTER_PRIVATE_KEY"):
        secrets["DARK_MINTER_PRIVATE_KEY"] = secrets["DARK_ADMIN_PRIVATE_KEY"]

    mappings = {
        "RPC_URL": root.get("DARK_RPC_URL", ""),
        "CHAIN_ID": root.get("DARK_CHAIN_ID", ""),
        "ADMIN_PRIVATE_KEY": secrets.get("DARK_ADMIN_PRIVATE_KEY", ""),
        "MINTER_PRIVATE_KEY": secrets.get("DARK_MINTER_PRIVATE_KEY", ""),
        f"{prefix}_DARK_CONTRACT_ADDRESS": root.get("DARK_CONTRACT_ADDRESS", ""),
        f"{prefix}_AUTHORITY_CONTRACT_ADDRESS": root.get("DARK_AUTHORITY_ADDRESS", ""),
        f"{prefix}_STORE_API_URL": root.get("METADATA_STORE_API_URL", ""),
        "DARK_ABI_JSON": root.get("DARK_ABI_JSON", ""),
        "AUTHORITY_ABI_JSON": root.get("AUTHORITY_ABI_JSON", ""),
    }

    for key, incoming_value in mappings.items():
        incoming = incoming_value.strip()
        if not incoming:
            continue

        current = env.get(key, "").strip()
        if current and current != incoming and authoritative:
            print(f"[WARNING] '{key}' differs from the handoff; using handoff value.")

        if authoritative or not current:
            env[key] = incoming


#: (env_key_template, human description, corresponding key in root .env.integration or None)
_BLOCKCHAIN_REQUIRED_FIELDS: list[tuple[str, str, Optional[str]]] = [
    ("RPC_URL",                          "Blockchain RPC URL",           "DARK_RPC_URL"),
    ("{prefix}_DARK_CONTRACT_ADDRESS",      "dARK contract address",        "DARK_CONTRACT_ADDRESS"),
    ("{prefix}_AUTHORITY_CONTRACT_ADDRESS", "Authority contract address",   "DARK_AUTHORITY_ADDRESS"),
    (
        "ADMIN_PRIVATE_KEY",
        "Admin signer private key",
        ".env.integration.secrets → DARK_ADMIN_PRIVATE_KEY",
    ),
    (
        "MINTER_PRIVATE_KEY",
        "Minter signer private key",
        ".env.integration.secrets → DARK_MINTER_PRIVATE_KEY",
    ),
]

def _missing_required_fields(
    prefix: str, env: dict, fields: list[tuple[str, str, Optional[str]]]
) -> list[tuple[str, str, Optional[str]]]:
    """Return the subset of ``fields`` whose env key is blank in ``env``.

    Each returned tuple has its ``{prefix}`` placeholder already resolved.
    """
    missing = []
    for key_template, description, integration_key in fields:
        key = key_template.format(prefix=prefix)
        value = env.get(key, "").strip()
        if not value and key == "ADMIN_PRIVATE_KEY":
            value = get_role_private_key(env, "admin")
        elif not value and key == "MINTER_PRIVATE_KEY":
            value = get_role_private_key(env, "minter")
        if not value:
            missing.append((key, description, integration_key))
    return missing


def _abort_missing_fields(missing: list[tuple[str, str, Optional[str]]], context: str) -> None:
    """Print an itemized list of missing configuration fields and exit(1)."""
    if not missing:
        return
    print(f"\n[ERROR] Missing required configuration for {context}.")
    print(
        "\n  The following fields are not set. Fill them in the handoff files"
        "\n  or directly in '.env', then re-run:\n"
    )
    for key, description, integration_key in missing:
        print(f"    - {key:<40} {description}")
        if integration_key:
            print(f"      handoff key: {integration_key}")
    print("\n  See README.md → \"Decoupled Setup\" for the full field reference.")
    sys.exit(1)


def _apply_default_blockchain_repo_urls(prefix: str, env: dict) -> None:
    """Fill any unset blockchain repository URL fields with their public defaults, silently."""
    applied = []
    for suffix, default_url in _BLOCKCHAIN_REPO_DEFAULTS.items():
        key = f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_URL"
        if not env.get(key, "").strip():
            env[key] = default_url
            applied.append((key, default_url))
    if applied:
        print("\n  [INFO] Using default blockchain repository URLs (not set in .env):")
        for key, url in applied:
            print(f"    - {key} = {url}")


def _apply_default_storage_repo_urls(prefix: str, env: dict) -> None:
    """Fill any unset storage repository URL fields with their public defaults, silently."""
    applied = []
    for label, (key_suffix, default_url) in _STORAGE_REPO_DEFAULTS.items():
        key = f"{prefix}{key_suffix}"
        if not env.get(key, "").strip():
            env[key] = default_url
            applied.append((key, default_url))
    if applied:
        print("\n  [INFO] Using default storage repository URLs (not set in .env):")
        for key, url in applied:
            print(f"    - {key} = {url}")


def _derive_host_from_url(url: str) -> str:
    """Extract the hostname component from a URL for display/flag purposes."""
    return urllib.parse.urlparse(url).hostname or url


#: dark-env's docker-compose service name for the RPC node, reachable by other
#: containers on the shared 'dark-net' network — NOT the same as the host-facing
#: RPC_URL (usually 'localhost'), which is unreachable from inside a container.
_LOCAL_BLOCKCHAIN_DOCKER_RPC_URL = "http://rpc01:8545"


def _docker_rpc_url(env: dict) -> str:
    """Return the RPC URL that Docker services on 'dark-net' should use to reach the chain.

    When the blockchain tier is installed locally as part of this same run
    (``install_profile`` sets ``_BLOCKCHAIN_CO_LOCATED``), its RPC node lives in
    the same Docker network under a fixed service name. Otherwise the blockchain
    is remote/decoupled, so the operator-provided RPC_URL is used as-is.
    """
    if env.get("_BLOCKCHAIN_CO_LOCATED") == "true":
        return _LOCAL_BLOCKCHAIN_DOCKER_RPC_URL
    return env.get("RPC_URL", "").strip()


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

    dark_from_ini, auth_from_ini = find_deployed_contracts_ini()
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
            f"'{_DEPLOYED_CONTRACTS_INI_PATH}'. .env.integration may be incomplete."
        )

    integration_env = {
        "DARK_RPC_URL": env.get("RPC_URL", "http://localhost:8545").strip(),
        "DARK_CHAIN_ID": env.get("CHAIN_ID", "1337").strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract,
        "DARK_AUTHORITY_ADDRESS": authority_contract,
        "DARK_READ_ONLY": "False",
        "DARK_VALIDATE_CHAIN_ID": "True",
        "DARK_GAS_LIMIT": "550000",
        "DARK_TX_TIMEOUT_SECONDS": "120",
    }

    dark_abi_json = env.get("DARK_ABI_JSON", "").strip()
    authority_abi_json = env.get("AUTHORITY_ABI_JSON", "").strip()
    if dark_abi_json:
        integration_env["DARK_ABI_JSON"] = dark_abi_json
    if authority_abi_json:
        integration_env["AUTHORITY_ABI_JSON"] = authority_abi_json

    env_path = core_path / ".env.integration"
    write_env_secure(env_path, integration_env)

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
    _prefixes = {"developer": "DEVELOPER", "sandbox": "SANDBOX", "production": "PRODUCTION"}
    prefix = _prefixes.get(env.get("TYPE", "developer").lower(), "DEVELOPER")

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
        "DARK_RPC_URL": _docker_rpc_url(env) or template_env.get("DARK_RPC_URL", "http://localhost:8545").strip(),
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
        "DARK_ADMIN_PRIVATE_KEY": (
            get_role_private_key(env, "minter")
            or template_env.get("DARK_ADMIN_PRIVATE_KEY", "")
        ).strip(),
        "METADATA_STORAGE_TYPE": metadata_storage_type,
        "METADATA_STORAGE_PATH": metadata_storage_path,
        "METADATA_STORE_API_URL": env.get(f"{prefix}_STORE_API_URL", "").strip()
            or ("http://store-api:8003" if metadata_storage_type == "store_api"
                else template_env.get("METADATA_STORE_API_URL", "http://localhost:8003").strip()),
    }

    dark_abi_json = env.get("DARK_ABI_JSON", "").strip()
    authority_abi_json = env.get("AUTHORITY_ABI_JSON", "").strip()
    if dark_abi_json:
        integration_env["DARK_ABI_JSON"] = dark_abi_json
    if authority_abi_json:
        integration_env["AUTHORITY_ABI_JSON"] = authority_abi_json

    env_path = minter_path / ".env.integration"
    write_env_secure(env_path, integration_env)

    print(f"[OK] Generated '{env_path}'.")


def generate_store_api_env_integration(store_api_path: Path, env: dict) -> None:
    """Generate Store API failover and quorum settings from the global topology."""
    template_env = load_optional_env(store_api_path / ".env.example")

    _prefixes = {"developer": "DEVELOPER", "sandbox": "SANDBOX", "production": "PRODUCTION"}
    prefix = _prefixes.get(env.get("TYPE", "developer").lower(), "DEVELOPER")

    integration_env = {
        **template_env,
        "STORE_API_HOST": template_env.get("STORE_API_HOST", "0.0.0.0").strip(),
        "STORE_API_PORT": "8003",
        "STORAGE_BACKEND": template_env.get("STORAGE_BACKEND", "ipfs_cluster").strip(),
        "IPFS_ADD_MODE": "cluster_proxy",
        "IPFS_HEALTH_CACHE_TTL_SECONDS": env.get(
            "IPFS_HEALTH_CACHE_TTL_SECONDS",
            template_env.get("IPFS_HEALTH_CACHE_TTL_SECONDS", "10"),
        ).strip(),
    }

    topology = configured_storage_topology(prefix, env)
    site_id = env.get(f"{prefix}_STORAGE_SITE_ID", "").strip()
    integration_env.update(store_api_environment(topology, site_id))
    integration_env["IPFS_REPLICATION_CONFIRM_TIMEOUT_SECONDS"] = env.get(
        "IPFS_REPLICATION_CONFIRM_TIMEOUT_SECONDS",
        template_env.get("IPFS_REPLICATION_CONFIRM_TIMEOUT_SECONDS", "120"),
    ).strip()
    integration_env["IPFS_REPLICATION_CONFIRM_INTERVAL_SECONDS"] = env.get(
        "IPFS_REPLICATION_CONFIRM_INTERVAL_SECONDS",
        template_env.get("IPFS_REPLICATION_CONFIRM_INTERVAL_SECONDS", "2"),
    ).strip()

    env_path = store_api_path / ".env.integration"
    write_env_secure(env_path, integration_env)

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
        "DARK_RPC_URL": _docker_rpc_url(env) or template_env.get("DARK_RPC_URL", "http://localhost:8545").strip(),
        "DARK_CHAIN_ID": env.get(
            "CHAIN_ID",
            template_env.get("DARK_CHAIN_ID", "1337"),
        ).strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract or template_env.get("DARK_CONTRACT_ADDRESS", "").strip(),
        "DARK_AUTHORITY_ADDRESS": authority_contract or template_env.get("DARK_AUTHORITY_ADDRESS", "").strip(),
        "DARK_ADMIN_PRIVATE_KEY": (
            get_role_private_key(env, "admin")
            or template_env.get("DARK_ADMIN_PRIVATE_KEY", "")
        ).strip(),
    }

    dark_abi_json = env.get("DARK_ABI_JSON", "").strip()
    authority_abi_json = env.get("AUTHORITY_ABI_JSON", "").strip()
    if dark_abi_json:
        integration_env["DARK_ABI_JSON"] = dark_abi_json
    if authority_abi_json:
        integration_env["AUTHORITY_ABI_JSON"] = authority_abi_json

    env_path = admin_api_path / ".env.integration"
    write_env_secure(env_path, integration_env)

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
    _prefixes = {"developer": "DEVELOPER", "sandbox": "SANDBOX", "production": "PRODUCTION"}
    prefix = _prefixes.get(env.get("TYPE", "developer").lower(), "DEVELOPER")

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
        "DARK_RPC_URL": _docker_rpc_url(env) or template_env.get("DARK_RPC_URL", "http://localhost:8545").strip(),
        "DARK_CHAIN_ID": env.get(
            "CHAIN_ID",
            template_env.get("DARK_CHAIN_ID", "1337"),
        ).strip(),
        "DARK_CONTRACT_ADDRESS": dark_contract or template_env.get("DARK_CONTRACT_ADDRESS", "").strip(),
        "METADATA_STORAGE_TYPE": metadata_storage_type,
        "METADATA_STORAGE_PATH": metadata_storage_path,
        "METADATA_STORE_API_URL": env.get(f"{prefix}_STORE_API_URL", "").strip()
            or ("http://store-api:8003" if metadata_storage_type == "store_api"
                else template_env.get("METADATA_STORE_API_URL", "http://localhost:8003").strip()),
        "METADATA_STORE_API_TIMEOUT_SECONDS": (
            template_env.get("METADATA_STORE_API_TIMEOUT_SECONDS", "10.0")
        ).strip(),
    }

    dark_abi_json = env.get("DARK_ABI_JSON", "").strip()
    if dark_abi_json:
        integration_env["DARK_ABI_JSON"] = dark_abi_json

    env_path = resolver_api_path / ".env.integration"
    write_env_secure(env_path, integration_env)

    print(f"[OK] Generated '{env_path}'.")


def commands_include_docker_compose_up(commands_str: str | list[str]) -> bool:
    """Return True when the command string already handles compose startup."""
    return any(
        "docker compose up" in cmd or "docker-compose up" in cmd
        for cmd in parse_commands(commands_str)
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

    extra_commands = get_commands(env, core_commands_key)
    if not extra_commands:
        extra_commands = get_commands(env, legacy_commands_key)

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
        extra_commands = [
            command.replace("pip ", f"{venv_pip} ")
            for command in extra_commands
        ]
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
    commands = get_commands(env, commands_key)
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
    commands = get_commands(env, commands_key)
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
        commands = [
            command.replace("pip ", f"{venv_pip} ")
            for command in commands
        ]
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
    commands = get_commands(env, commands_key)
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
        commands = [
            command.replace("pip ", f"{venv_pip} ")
            for command in commands
        ]
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
    commands = get_commands(env, commands_key, ["make up"])
    target = "components/storage/dark-ipfs"

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

    topology = configured_storage_topology(prefix, env)
    node_id = env.get(f"{prefix}_STORAGE_NODE_ID", "").strip()
    generated = node_environment(
        topology,
        node_id,
        env[f"{prefix}_IPFS_SWARM_KEY_FILE"].strip(),
        env[f"{prefix}_IPFS_CLUSTER_SECRET_FILE"].strip(),
    )
    node_env_path = Path(target).resolve() / ".env.node"
    write_env_secure(node_env_path, generated)
    print(f"[OK] Generated '{node_env_path}' for {node_id}.")

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
    commands = get_commands(env, commands_key)
    if not commands and name.upper() != "MINTER":
        commands = list(DEFAULT_COMMANDS)
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
    commands = get_commands(env, commands_key, ["python3 install.py"])
    target   = "components/frontend/dashboard-web"

    install_repo(name="dashboard-web", repo_url=repo_url, branch=branch, target_dir=target)

    if not do_setup:
        print("[INFO] SETUP=False — 'dashboard-web' cloned, setup skipped.\n")
        return

    run_commands(commands, cwd=str(Path(target).resolve()))
    print("[OK] 'dashboard-web' is up.\n")


# ─── Profile installer ────────────────────────────────────────────────────────


INSTALL_STAGES = (
    "blockchain",
    "core-lib",
    "admin",
    "ipfs",
    "store-api",
    "resolver",
    "minter",
    "dashboard",
)


def install_profile(
    prefix: str,
    env: dict,
    resume_from: Optional[str] = None,
) -> None:
    """Install all components for a given profile prefix.

    Components installed in order:

    1. Blockchain (``dark-env``, ``dark-dapp``, ``dark-explorador``)
    2. Core Lib
    3. Core Admin API
    4. dark-ipfs
    5. dark-store-api
    6. Core Resolver API
    7. Minter
    8. Dashboard

    :param prefix: Environment variable prefix matching the active profile
                   (e.g. ``DEVELOPER``, ``SANDBOX``, ``PRODUCTION``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    :param resume_from: First stage to execute when continuing a partial install.
    :type resume_from: str, optional
    """
    install_bc, install_ipfs, install_apps = _selected_tiers(prefix, env)
    start_index = INSTALL_STAGES.index(resume_from) if resume_from else 0

    def should_run(stage: str) -> bool:
        selected = INSTALL_STAGES.index(stage) >= start_index
        if not selected:
            print(f"[RESUME] Keeping completed stage '{stage}'.")
        return selected

    if install_bc:
        blockchain_host = env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
        if blockchain_host:
            print(f"[INFO] Blockchain tier is remote ({blockchain_host}) — skipping local install.")
            env["_BLOCKCHAIN_CO_LOCATED"] = "false"
        else:
            if should_run("blockchain"):
                install_blockchain(prefix=prefix, env=env)
                _generate_root_env_integration_blockchain(env=env)
                _merge_root_env_integration_into_env(env=env, prefix=prefix)
            else:
                validate_root_env_integration()
                _merge_root_env_integration_into_env(
                    env=env,
                    prefix=prefix,
                    authoritative=True,
                )
            env["_BLOCKCHAIN_CO_LOCATED"] = "true"
    else:
        print("[INFO] Blockchain not selected — skipping.")
        env["_BLOCKCHAIN_CO_LOCATED"] = "false"

    # Pure apps mode imports blockchain contract and signer handoff data.
    if install_apps and not install_bc and not install_ipfs:
        validate_root_env_integration()
        _merge_root_env_integration_into_env(
            env=env,
            prefix=prefix,
            authoritative=True,
        )

    # Revalidate after a local blockchain install, because wallet extraction
    # may have populated signer material used by the application tier.
    validate_install_configuration(prefix, env)

    if install_apps:
        if should_run("core-lib"):
            install_core_lib(prefix=prefix, env=env)
        if should_run("admin"):
            install_core_admin_api(prefix=prefix, env=env)

    if install_ipfs:
        if should_run("ipfs"):
            install_dark_ipfs(prefix=prefix, env=env)
    else:
        print("[INFO] Storage node not selected — skipping.")

    if install_apps:
        # Every application site owns a local Store API backed by both local peers.
        env[f"{prefix}_STORE_API_URL"] = "http://store-api:8003"
        if should_run("store-api"):
            install_dark_store_api(prefix=prefix, env=env)
            _update_root_env_integration_storage(env=env, prefix=prefix)
            _merge_root_env_integration_into_env(env=env, prefix=prefix)
        if should_run("resolver"):
            install_core_resolver_api(prefix=prefix, env=env)
        if should_run("minter"):
            install_single_component(name="MINTER", prefix=prefix, env=env)
        if should_run("dashboard"):
            install_dashboard(prefix=prefix, env=env)


def _selected_tiers(prefix: str, env: dict) -> tuple[bool, bool, bool]:
    components = {
        item.strip().lower()
        for item in env.get(f"{prefix}_INSTALL_COMPONENTS", "all").split(",")
        if item.strip()
    }
    unknown = components - {"all", "apps", "blockchain", "storage-node"}
    if unknown:
        print(f"[ERROR] Unknown install components: {', '.join(sorted(unknown))}")
        sys.exit(1)
    install_all = "all" in components
    return (
        install_all or "blockchain" in components,
        install_all or "storage-node" in components,
        install_all or "apps" in components,
    )


def configured_storage_topology(prefix: str, env: dict) -> StorageTopology:
    """Load the profile's configured topology, resolving paths from the repo root."""
    raw_path = env.get(f"{prefix}_STORAGE_TOPOLOGY_FILE", "storage-topology.json").strip()
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        return load_storage_topology(path)
    except StorageTopologyError as exc:
        print(f"[ERROR] Invalid global storage topology: {exc}")
        sys.exit(1)


def _validate_http_url(name: str, value: str) -> None:
    if not value:
        return
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        print(f"[ERROR] {name} must be an absolute HTTP(S) URL: {value!r}")
        sys.exit(1)


def _validate_storage_secret_file(name: str, path_value: str, kind: str) -> Path:
    """Validate an external storage secret without printing its contents."""
    path = Path(path_value)
    if not path.is_absolute():
        print(f"[ERROR] {name} must be an absolute secret file path.")
        sys.exit(1)
    try:
        raw = path.read_text().strip()
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        print(f"[ERROR] {name} is not a readable secret file: {exc}")
        sys.exit(1)
    if mode & 0o077:
        print(f"[ERROR] {name} permissions must not grant group/world access (mode {mode:04o}).")
        sys.exit(1)
    if kind == "cluster":
        valid = bool(re.fullmatch(r"[0-9a-fA-F]{64}", raw))
        description = "exactly 64 hexadecimal characters"
    else:
        lines = raw.splitlines()
        valid = (
            len(lines) == 3
            and lines[0] == "/key/swarm/psk/1.0.0/"
            and lines[1] == "/base16/"
            and bool(re.fullmatch(r"[0-9a-fA-F]{64}", lines[2]))
        )
        description = "a Kubo /key/swarm/psk/1.0.0/ base16 key"
    if not valid:
        print(f"[ERROR] {name} must contain {description}.")
        sys.exit(1)
    return path


def validate_install_configuration(prefix: str, env: dict) -> None:
    """Validate topology-sensitive settings before mutating the installation."""
    install_bc, install_storage, install_apps = _selected_tiers(prefix, env)
    blockchain_remote = bool(env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip())

    chain_id = env.get("CHAIN_ID", "").strip()
    if chain_id and (not chain_id.isdigit() or int(chain_id) <= 0):
        print("[ERROR] CHAIN_ID must be a positive integer.")
        sys.exit(1)

    _validate_http_url("RPC_URL", env.get("RPC_URL", "").strip())
    _validate_http_url(
        f"{prefix}_STORE_API_URL",
        env.get(f"{prefix}_STORE_API_URL", "").strip(),
    )
    for key in (
        f"{prefix}_DARK_CONTRACT_ADDRESS",
        f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
    ):
        address = env.get(key, "").strip()
        if address and not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
            print(f"[ERROR] {key} must be a 20-byte hexadecimal address.")
            sys.exit(1)

    if install_apps and (not install_bc or blockchain_remote):
        missing = _missing_required_fields(prefix, env, _BLOCKCHAIN_REQUIRED_FIELDS)
        _abort_missing_fields(missing, context="using a remote blockchain tier")

    if install_storage or install_apps:
        topology = configured_storage_topology(prefix, env)
        if (
            topology.development_single_node
            and env.get("TYPE", "developer").strip().lower() != "developer"
        ):
            print(
                "[ERROR] development_single_node storage topology is allowed "
                "only with the developer profile."
            )
            sys.exit(1)
        site_id = env.get(f"{prefix}_STORAGE_SITE_ID", "").strip()
        if not site_id:
            print(f"[ERROR] {prefix}_STORAGE_SITE_ID is required in global-cluster mode.")
            sys.exit(1)
        try:
            topology.site_peers(site_id)
        except StorageTopologyError as exc:
            print(f"[ERROR] {exc}")
            sys.exit(1)
        if install_storage:
            node_id = env.get(f"{prefix}_STORAGE_NODE_ID", "").strip()
            try:
                node = topology.peer(node_id)
            except StorageTopologyError as exc:
                print(f"[ERROR] {exc}")
                sys.exit(1)
            if node.site != site_id:
                print(
                    f"[ERROR] Storage node {node_id!r} belongs to {node.site!r}, "
                    f"not configured site {site_id!r}."
                )
                sys.exit(1)
            swarm_key = f"{prefix}_IPFS_SWARM_KEY_FILE"
            cluster_key = f"{prefix}_IPFS_CLUSTER_SECRET_FILE"
            swarm_path = _validate_storage_secret_file(
                swarm_key, env.get(swarm_key, "").strip(), "swarm"
            )
            cluster_path = _validate_storage_secret_file(
                cluster_key, env.get(cluster_key, "").strip(), "cluster"
            )
            if swarm_path == cluster_path:
                print("[ERROR] Kubo and Cluster must use separate secret files.")
                sys.exit(1)

    profile = env.get("TYPE", "developer").lower()
    role_keys = {
        role: get_role_private_key(env, role)
        for role in ("deployer", "admin", "minter")
    }
    for private_key in set(role_keys.values()) - {""}:
        validate_private_key(private_key, profile)

    if profile == "production" and (install_apps or install_bc):
        missing_roles = [
            role for role in ("admin", "minter") if not role_keys[role]
        ]
        if missing_roles:
            print(
                "[ERROR] Production requires explicit private keys for roles: "
                + ", ".join(missing_roles)
            )
            sys.exit(1)
        if role_keys["admin"] == role_keys["minter"]:
            print("[ERROR] Production requires distinct ADMIN_PRIVATE_KEY and MINTER_PRIVATE_KEY values.")
            sys.exit(1)


def print_install_plan(prefix: str, env: dict) -> None:
    """Print a redacted, non-mutating overview of the configured installation."""
    install_bc, install_storage, install_apps = _selected_tiers(prefix, env)
    print("\n=== Installation plan ===")
    print(f"Profile     : {env.get('TYPE', '').upper()}")
    print(f"Blockchain  : {'remote' if env.get(f'{prefix}_BLOCKCHAIN_HOST', '').strip() else ('local' if install_bc else 'skip')}")
    print(f"Storage node: {'install' if install_storage else 'skip'}")
    print(f"Apps        : {'install' if install_apps else 'skip'}")
    if install_storage or install_apps:
        topology = configured_storage_topology(prefix, env)
        policy = topology.policy
        print(f"IPFS cluster: {topology.cluster_name} (global CRDT)")
        print(f"Site / node : {env.get(f'{prefix}_STORAGE_SITE_ID', '-')} / {env.get(f'{prefix}_STORAGE_NODE_ID', '-')}")
        print(f"Topology    : {len(topology.sites)} site(s), {len(topology.peers)} peer(s)")
        print(
            "Replication : "
            f"min={policy.replication_min}, max={policy.replication_max}, "
            f"write={policy.write_min_peers} peer(s)/{policy.write_min_sites} site(s)"
        )
    configured_roles = [
        role for role in ("deployer", "admin", "minter")
        if get_role_private_key(env, role)
    ]
    print(f"Signers     : {', '.join(configured_roles) if configured_roles else 'not yet generated'}")
    print("No files, repositories, containers, or networks were changed.")


def _entry_pinned_sites(topology: StorageTopology, entry: dict) -> set[str]:
    """Resolve pinned status peer names/IDs to topology site IDs."""
    peer_sites = topology.peer_sites()
    sites: set[str] = set()
    peer_map = entry.get("peer_map", {})
    if not isinstance(peer_map, dict):
        return sites
    for peer_id, value in peer_map.items():
        if not isinstance(value, dict) or str(value.get("status", "")).lower() != "pinned":
            continue
        site = peer_sites.get(str(value.get("peername", ""))) or peer_sites.get(str(peer_id))
        if site:
            sites.add(site)
    return sites


def cluster_operation_endpoints(
    topology: StorageTopology,
    site_id: str,
) -> list[str]:
    """Return Cluster API URLs reachable by the machine running operations."""
    peers = topology.site_peers(site_id)
    return [
        storage_probe_url(
            topology,
            peer.cluster_api_url,
            peer.vpn_address,
            9094,
        )
        for peer in peers
    ]


def audit_storage(topology: StorageTopology, site_id: str) -> dict[str, int]:
    """Inspect global pin status without changing allocations."""
    endpoints = cluster_operation_endpoints(topology, site_id)
    entries = pin_entries(cluster_request(endpoints, "GET", "/pins", timeout=60.0))
    policy = topology.policy
    summary = {
        "pins": 0,
        "below_minimum": 0,
        "below_full_replication": 0,
        "pinning": 0,
        "errors": 0,
    }
    for entry in entries:
        cid = entry_cid(entry)
        if not cid:
            continue
        summary["pins"] += 1
        pinned_count = len(pinned_peer_ids(entry))
        pinned_sites = len(_entry_pinned_sites(topology, entry))
        statuses = [
            str(value.get("status", "")).lower()
            for value in entry.get("peer_map", {}).values()
            if isinstance(value, dict)
        ]
        if pinned_count < policy.replication_min:
            summary["below_minimum"] += 1
            print(
                f"[UNDER-REPLICATED] {cid}: {pinned_count} peer(s), "
                f"{pinned_sites} site(s)"
            )
        if pinned_count < policy.replication_max:
            summary["below_full_replication"] += 1
        if any("pinning" in status or "queued" in status for status in statuses):
            summary["pinning"] += 1
        if any("error" in status for status in statuses):
            summary["errors"] += 1

    print("\n=== Global storage audit ===")
    print(f"Cluster             : {topology.cluster_name}")
    print(f"Expected topology   : {len(topology.sites)} site(s), {len(topology.peers)} peer(s)")
    print(f"Pins inspected      : {summary['pins']}")
    print(f"Below minimum       : {summary['below_minimum']}")
    print(f"Below full target   : {summary['below_full_replication']}")
    print(f"Pinning/queued      : {summary['pinning']}")
    print(f"Pin errors          : {summary['errors']}")
    return summary


def reconcile_storage(topology: StorageTopology, site_id: str) -> dict[str, int]:
    """Reapply the topology replication policy to every pin; never unpin."""
    endpoints = cluster_operation_endpoints(topology, site_id)
    allocations = pin_entries(
        cluster_request(endpoints, "GET", "/allocations", timeout=60.0)
    )
    policy = topology.policy
    cids = [cid for entry in allocations if (cid := entry_cid(entry))]
    print(
        f"[INFO] Reapplying min={policy.replication_min}, "
        f"max={policy.replication_max} to {len(cids)} pin(s)."
    )
    failures = 0
    for index, cid in enumerate(cids, 1):
        try:
            cluster_request(
                endpoints,
                "POST",
                f"/pins/{urllib.parse.quote(cid, safe='')}",
                query={
                    "replication-min": str(policy.replication_min),
                    "replication-max": str(policy.replication_max),
                },
                timeout=60.0,
            )
        except RuntimeError as exc:
            failures += 1
            print(f"[ERROR] Could not reconcile {cid}: {exc}")
        if index % 100 == 0:
            print(f"[INFO] Reconciled {index}/{len(cids)} pins.")
    if failures:
        print(f"[WARNING] Reconciliation completed with {failures} failed pin(s).")
    else:
        print(f"[OK] Reconciliation requested for all {len(cids)} pin(s); no pins were removed.")
    return audit_storage(topology, site_id)


def run_storage_operation(action: str, prefix: str, env: dict) -> None:
    """Dispatch global storage audit/reconciliation commands."""
    topology = configured_storage_topology(prefix, env)
    site_id = env.get(f"{prefix}_STORAGE_SITE_ID", "").strip()
    if not site_id:
        print(f"[ERROR] {prefix}_STORAGE_SITE_ID is required.")
        sys.exit(1)
    try:
        topology.site_peers(site_id)
    except StorageTopologyError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    summary = (
        audit_storage(topology, site_id)
        if action == "audit"
        else reconcile_storage(topology, site_id)
    )
    if summary["below_minimum"] or summary["errors"]:
        print("[ERROR] Global storage is below its required durability policy.")
        sys.exit(2)


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


def _ask_confirm(question: str, default: bool = True) -> bool:
    """Ask a yes/no question and return True for yes."""
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {question} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


_BLOCKCHAIN_REPO_DEFAULTS: dict = {
    "DARK_ENV":        "https://github.com/LA-Referencia-IOI/dark-env",
    "DARK_DAPP":       "https://github.com/LA-Referencia-IOI/dark-dapp",
    "DARK_EXPLORADOR": "https://github.com/LA-Referencia-IOI/dark-explorer.git",
}

# Each entry: label → (env-key suffix after prefix, default URL)
_STORAGE_REPO_DEFAULTS: dict = {
    "dark-ipfs":      ("_IPFS_REPOSITORY_URL",      "git@github.com:LA-Referencia-IOI/dark-ipfs.git"),
    "dark-store-api": ("_STORE_API_REPOSITORY_URL",  "git@github.com:LA-Referencia-IOI/dark-store-api.git"),
}


def _prepare_developer_storage_assets(env: dict) -> None:
    """Create the local-only topology and IPFS secrets required by developer.

    Production and sandbox topologies always keep the two-peers-per-site
    invariant. The developer wizard instead promises a complete stack on one
    machine, so it receives an explicit one-site/one-peer topology whose API
    endpoints use the shared Docker network.
    """
    prefix = "DEVELOPER"
    _, install_storage, install_apps = _selected_tiers(prefix, env)
    if not (install_storage or install_apps):
        return

    site_id = env.get(f"{prefix}_STORAGE_SITE_ID", "").strip() or "site-a"
    node_id = (
        env.get(f"{prefix}_STORAGE_NODE_ID", "").strip()
        or f"{site_id}-storage-1"
    )
    env[f"{prefix}_STORAGE_SITE_ID"] = site_id
    env[f"{prefix}_STORAGE_NODE_ID"] = node_id

    raw_topology_path = (
        env.get(f"{prefix}_STORAGE_TOPOLOGY_FILE", "").strip()
        or "storage-topology.json"
    )
    topology_path = Path(raw_topology_path)
    if not topology_path.is_absolute():
        topology_path = PROJECT_ROOT / topology_path
    env[f"{prefix}_STORAGE_TOPOLOGY_FILE"] = raw_topology_path

    if not topology_path.exists():
        topology_document = {
            "version": 1,
            "cluster_name": "dark-developer",
            "development_single_node": True,
            "strict_single_site": False,
            "sites": [
                {
                    "id": site_id,
                    "peers": [
                        {
                            "id": node_id,
                            "vpn_address": "127.0.0.1",
                            "ipfs_api_url": "http://dark-ipfs-local:5001",
                            "cluster_api_url": (
                                "http://dark-ipfs-cluster-local:9094"
                            ),
                            "cluster_proxy_url": (
                                "http://dark-ipfs-cluster-local:9095"
                            ),
                        }
                    ],
                }
            ],
        }
        write_text_secure(
            topology_path,
            json.dumps(topology_document, indent=2) + "\n",
            mode=0o644,
        )
        print(f"[OK] Generated single-node developer topology at '{topology_path}'.")

    secret_dir = PROJECT_ROOT / ".dark-secrets" / "developer"
    secret_specs = (
        (
            f"{prefix}_IPFS_SWARM_KEY_FILE",
            secret_dir / "ipfs-swarm.key",
            "/key/swarm/psk/1.0.0/\n/base16/\n"
            f"{secrets.token_hex(32)}\n",
        ),
        (
            f"{prefix}_IPFS_CLUSTER_SECRET_FILE",
            secret_dir / "ipfs-cluster-secret",
            f"{secrets.token_hex(32)}\n",
        ),
    )
    for key, default_path, content in secret_specs:
        configured = env.get(key, "").strip()
        configured_path = Path(configured) if configured else None
        if configured_path and not configured_path.is_absolute():
            configured_path = PROJECT_ROOT / configured_path
        if configured_path and configured_path.exists():
            env[key] = str(configured_path.resolve())
            continue

        if not default_path.exists():
            write_text_secure(default_path, content, mode=0o600)
            print(f"[OK] Generated developer secret file '{default_path}'.")
        env[key] = str(default_path.resolve())


def _apply_local_blockchain_defaults(env: dict) -> None:
    """Apply defaults only when this host will run the blockchain tier."""
    if not env.get("RPC_URL", "").strip():
        env["RPC_URL"] = "http://localhost:8545"
    if not env.get("CHAIN_ID", "").strip():
        env["CHAIN_ID"] = "2025"


def _wizard_blockchain_tier(prefix: str, env: dict) -> dict:
    """Collect blockchain tier topology — selection only, no free-text entry.

    "Remote" requires the connection info to already be present in ``env``
    (merged from the root .env.integration or set directly in .env); if
    anything is missing, the wizard lists exactly what and exits.
    """
    location = _ask_choice(
        "Where will the BLOCKCHAIN tier be installed?",
        [
            "This server  — install locally (single node)",
            "Remote server — already deployed or will be deployed separately",
        ],
    )
    if location == 1:
        _apply_local_blockchain_defaults(env)
        for key in (
            f"{prefix}_BLOCKCHAIN_HOST",
            f"{prefix}_BLOCKCHAIN_EXTRA_NODES",
            f"{prefix}_BLOCKCHAIN_ENODES",
            f"{prefix}_DARK_CONTRACT_ADDRESS",
            f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
        ):
            env.pop(key, None)
        _apply_default_blockchain_repo_urls(prefix=prefix, env=env)
        return env

    if _ROOT_ENV_INTEGRATION.exists():
        _merge_root_env_integration_into_env(
            env=env,
            prefix=prefix,
            authoritative=True,
        )
    missing = _missing_required_fields(prefix, env, _BLOCKCHAIN_REQUIRED_FIELDS)
    _abort_missing_fields(missing, context="a remote BLOCKCHAIN tier")
    env[f"{prefix}_BLOCKCHAIN_HOST"] = _derive_host_from_url(env["RPC_URL"])
    return env


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

    if {"all", "apps", "storage-node"} & {
        value.strip() for value in components.split(",")
    }:
        print(f"  Storage site    : {env.get(f'{prefix}_STORAGE_SITE_ID', '(not configured)')}")
        print(f"  Storage node    : {env.get(f'{prefix}_STORAGE_NODE_ID', '(apps only)')}")
        print(f"  Topology file   : {env.get(f'{prefix}_STORAGE_TOPOLOGY_FILE', 'storage-topology.json')}")
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
    handoff_authoritative = False

    if install_type == "developer":
        _apply_local_blockchain_defaults(env)
    else:
        print(f"\n  Configuring infrastructure for {install_type.upper()} profile:")

        component_choice = _ask_choice(
            "Which components will be installed on this server?",
            [
                "All               — blockchain + apps + one storage node",
                "Apps only         — application services + site-local Store API",
                "Blockchain only   — only the blockchain tier",
                "Storage node only — one Kubo + Cluster peer",
            ],
            default={"all": 1, "apps": 2, "blockchain": 3, "storage-node": 4}.get(
                env.get(f"{prefix}_INSTALL_COMPONENTS", "all").strip(), 1
            ),
        )
        component_map = {1: "all", 2: "apps", 3: "blockchain", 4: "storage-node"}
        selected = component_map[component_choice]
        env[f"{prefix}_INSTALL_COMPONENTS"] = selected

        if selected == "all":
            env = _wizard_blockchain_tier(prefix=prefix, env=env)
            handoff_authoritative = bool(
                env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
                and _ROOT_ENV_INTEGRATION.exists()
            )
        elif selected == "apps":
            # Pure apps mode only imports blockchain handoff data. Store API is local.
            validate_root_env_integration()
            _merge_root_env_integration_into_env(
                env=env,
                prefix=prefix,
                authoritative=True,
            )
            handoff_authoritative = True
            missing = _missing_required_fields(prefix, env, _BLOCKCHAIN_REQUIRED_FIELDS)
            _abort_missing_fields(missing, context='installing "apps"')
        elif selected == "blockchain":
            _apply_local_blockchain_defaults(env)
            for key in (
                f"{prefix}_BLOCKCHAIN_HOST",
                f"{prefix}_BLOCKCHAIN_EXTRA_NODES",
                f"{prefix}_BLOCKCHAIN_ENODES",
                f"{prefix}_DARK_CONTRACT_ADDRESS",
                f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
            ):
                env.pop(key, None)
            _apply_default_blockchain_repo_urls(prefix=prefix, env=env)
        elif selected == "storage-node":
            _apply_default_storage_repo_urls(prefix=prefix, env=env)

    _print_wizard_summary(install_type=install_type, prefix=prefix, env=env)

    if not _ask_confirm("Save configuration to .env and start installation?", default=True):
        print("\n[INFO] Installation cancelled by user.")
        sys.exit(0)

    if install_type == "developer":
        _prepare_developer_storage_assets(env)

    persisted_env = dict(env)
    if handoff_authoritative:
        # Handoff values remain runtime-only. In particular, never copy the
        # signer key from .env.integration.secrets into the general .env file.
        for key in (
            "RPC_URL",
            "CHAIN_ID",
            "MASTER_PRIVATE_KEY",
            "ADMIN_PRIVATE_KEY",
            "MINTER_PRIVATE_KEY",
            f"{prefix}_DARK_CONTRACT_ADDRESS",
            f"{prefix}_AUTHORITY_CONTRACT_ADDRESS",
            f"{prefix}_STORE_API_URL",
            "DARK_ABI_JSON",
            "AUTHORITY_ABI_JSON",
        ):
            persisted_env[key] = ""
    update_env_file(".env", persisted_env)
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

    subparsers.add_parser(
        "validate",
        help="Validate the saved non-interactive configuration without making changes.",
    )
    subparsers.add_parser(
        "plan",
        help="Validate and print a redacted installation plan without making changes.",
    )
    lock_parser = subparsers.add_parser(
        "lock",
        help="Record exact commits for installed component repositories.",
    )
    lock_parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that components.lock.json matches installed repositories.",
    )
    storage_parser = subparsers.add_parser(
        "storage",
        help="Audit or reconcile the global IPFS Cluster.",
    )
    storage_parser.add_argument(
        "action",
        choices=("audit", "reconcile"),
        help="Audit is read-only; reconcile reapplies replication policy without unpinning.",
    )
    resume_parser = subparsers.add_parser(
        "resume",
        help="Continue a partial installation without rerunning completed stages.",
    )
    resume_parser.add_argument(
        "--from",
        dest="from_stage",
        required=True,
        choices=INSTALL_STAGES,
        help="First installation stage to execute.",
    )

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
    print("=== dark-developer installer ===\n")

    ensure_supported_python()
    args = build_arg_parser().parse_args()

    if args.command == "lock":
        generate_component_lock(check=args.check)
        return

    if args.command not in {"validate", "plan", "storage"}:
        _ensure_docker_group()

    env = load_env()

    if args.command not in {"rebuild", "resume", "validate", "plan", "storage"}:
        env = run_setup_wizard(env)

    install_type, prefix = resolve_profile_prefix(env)

    if args.command == "storage":
        run_storage_operation(args.action, prefix, env)
        return

    if args.command in {"validate", "plan"}:
        install_bc, install_storage, install_apps = _selected_tiers(prefix, env)
        if install_bc and not env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip():
            _apply_local_blockchain_defaults(env)
        if install_apps and not install_bc and not install_storage:
            validate_root_env_integration()
            _merge_root_env_integration_into_env(
                env=env,
                prefix=prefix,
                authoritative=True,
            )
        elif (
            install_apps
            and env.get(f"{prefix}_BLOCKCHAIN_HOST", "").strip()
            and _ROOT_ENV_INTEGRATION.exists()
        ):
            _merge_root_env_integration_into_env(
                env=env,
                prefix=prefix,
                authoritative=True,
            )
        validate_install_configuration(prefix, env)
        if args.command == "plan":
            print_install_plan(prefix, env)
        else:
            print("[OK] Installation configuration is valid.")
        return

    if args.command == "resume" and args.from_stage != "blockchain":
        validate_root_env_integration()
        _merge_root_env_integration_into_env(
            env=env,
            prefix=prefix,
            authoritative=True,
        )

    for role in ("deployer", "admin", "minter"):
        private_key = get_role_private_key(env, role)
        if private_key:
            validate_private_key(private_key, install_type)

    print(f"[INFO] Profile: {install_type.upper()}")

    if args.command == "rebuild":
        rebuild_component(args=args, prefix=prefix, env=env)
        print("=== Rebuild complete ===")
        return

    validate_install_configuration(prefix, env)
    ensure_docker_running()
    install_profile(
        prefix=prefix,
        env=env,
        resume_from=args.from_stage if args.command == "resume" else None,
    )
    print_install_summary(prefix=prefix, env=env)

    print("=== Installation complete ===")


if __name__ == "__main__":
    main()
