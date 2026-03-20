#!/usr/bin/env python3
"""
dark-developer installer.

Reads the ``TYPE`` variable from the ``.env`` file and clones all
subcomponent repositories into the ``components/`` directory, then
runs the per-repo commands defined in the ``COMMANDS`` variables.

Usage::

    python install.py

Supported profiles:
    - ``developer``  — installs the full developer infrastructure.
    - ``sandbox``    — installs the sandbox stack.
    - ``production`` — installs the production stack.

Components installed (per profile):
    - **blockchain** — three sub-repos: ``dark-env``, ``dark-dapp``, ``dark-explorador``
    - **core-lib**
    - **resolver**
    - **minter**
    - **ipfs**

Command format in ``.env``::

    {PREFIX}_{COMPONENT}_COMMANDS=cmd1|cmd2|cmd3

Commands are separated by ``|`` and executed sequentially inside the
cloned repository directory. Shell syntax (globs, redirects) is supported.
"""

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

PROJECT_ROOT = Path(__file__).resolve().parent
SHARED_VENV_DIR = PROJECT_ROOT / "venv"


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


def run_shell(cmd: str, cwd: str = None) -> None:
    """Execute a single shell command string and exit the process on failure.

    The command is executed through the shell, so globs, pipes, and other
    shell syntax are supported within each individual command string.

    :param cmd: Shell command to execute.
    :type cmd: str
    :param cwd: Working directory for the command. Defaults to ``None``.
    :type cwd: str, optional
    :raises SystemExit: If the command returns a non-zero exit code.
    """
    print(f"[RUN] {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)

    if result.returncode != 0:
        print(f"[ERROR] Command failed: {cmd}")
        sys.exit(result.returncode)


def ensure_docker_running() -> None:
    """Fail early when Docker is not available or the daemon is stopped."""
    result = subprocess.run(
        "docker info",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return

    stderr = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
    print("[ERROR] Docker does not appear to be running or accessible.")
    print(f"[ERROR] {stderr}")
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


def docker_network_exists(network_name: str) -> bool:
    """Return True when the named Docker network exists."""
    result = subprocess.run(
        f"docker network inspect {network_name}",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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

    if not docker_network_exists("dark-net"):
        print(
            "[WARNING] Docker network 'dark-net' was not found. "
            f"Skipping {stack_name} Docker startup. Start blockchain first."
        )
        return

    print(f"[INFO] Starting {stack_name} Docker stack...")
    run_shell("docker compose up -d --build", cwd=str(compose_dir))

    if health_url:
        wait_for_http_ready(health_url, service_name=stack_name)


def print_install_summary(prefix: str, env: dict) -> None:
    """Print a concise summary of the installed local service endpoints."""
    print("\n=== Service Summary ===")

    rpc_url = env.get("RPC_URL", "http://localhost:8545").strip() or "http://localhost:8545"
    explorer_port = env.get("EXPLORER_PORT", "25000").strip() or "25000"
    explorer_url = f"http://localhost:{explorer_port}"
    admin_url = "http://localhost:8000"
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

    minter_selected = bool(get_url(env, f"{prefix}_MINTER_REPOSITORY_URL"))
    if minter_selected:
        print(
            f"- Core Minter API: {minter_url} "
            f"[health: {probe_http_status(f'{minter_url}/health')}] "
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
        run_shell(f"{sys.executable} -m venv {SHARED_VENV_DIR}")
    else:
        print(f"[INFO] Shared venv already exists at '{SHARED_VENV_DIR}', reusing it.")

    return SHARED_VENV_DIR


def shared_venv_bin(bin_name: str) -> str:
    """Return absolute path to a binary inside the shared root venv."""
    venv_dir = ensure_shared_venv()
    return str(venv_dir / "bin" / bin_name)


def ensure_component_venv(venv_dir: Path) -> Path:
    """Create or reuse a component-local virtual environment.

    This is used for components whose pinned dependencies would otherwise
    downgrade packages in the shared developer venv.
    """
    if not venv_dir.exists():
        print(f"[INFO] Creating component venv at '{venv_dir}'...")
        run_shell(f"{sys.executable} -m venv {venv_dir}")
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
    python         = component_venv_bin(component_venv, "python")

    # ── Step 1: isolated venv + pip install ──────────────────────────────────
    print("[INFO] Installing dark-dapp Python dependencies into component-local venv...")
    run_shell(f"{pip} install -r requirements.txt", cwd=str(abs_target))
    print(f"[OK] Python dependencies installed into '{component_venv}'.\n")

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


def generate_core_lib_env_integration(core_path: Path, env: dict) -> None:
    """Generate .env.integration for dark-core-lib from installed blockchain state.

    :param core_path: Path to components/libraries/dark-core-lib.
    :type core_path: Path
    :param env: Dictionary of environment variables loaded from .env.
    :type env: dict
    """
    ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
    dark_contract, authority_contract = read_deployed_contract_addresses(ini_path)

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
        "DARK_GAS_LIMIT": "500000",
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
    ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
    dark_contract, authority_contract = read_deployed_contract_addresses(ini_path)

    if not dark_contract or not authority_contract:
        print(
            "[WARNING] Could not read full contract addresses from "
            f"'{ini_path}'. .env.integration may be incomplete."
        )

    template_env = load_optional_env(minter_path / ".env.example")

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

    env_path = minter_path / ".env.integration"
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
    ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
    dark_contract, authority_contract = read_deployed_contract_addresses(ini_path)

    if not dark_contract or not authority_contract:
        print(
            "[WARNING] Could not read full contract addresses from "
            f"'{ini_path}'. .env.integration may be incomplete."
        )

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


def commands_include_docker_compose_up(commands_str: str) -> bool:
    """Return True when the command string already handles compose startup."""
    return any(
        "docker compose up" in cmd or "docker-compose up" in cmd
        for cmd in (part.strip() for part in commands_str.split("|"))
    )


def start_minter_stack(minter_path: Path) -> None:
    """Start minter services via Docker Compose when blockchain network exists."""
    compose_up_stack(
        compose_dir=minter_path,
        stack_name="minter",
        health_url="http://localhost:8001/health",
    )


def start_admin_api_stack(admin_api_path: Path) -> None:
    """Start admin API via Docker Compose when blockchain network exists."""
    compose_up_stack(
        compose_dir=admin_api_path,
        stack_name="admin API",
        health_url="http://localhost:8000/health",
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

# ─── Generic single-repo component installer ─────────────────────────────────


def install_single_component(name: str, prefix: str, env: dict) -> None:
    """Clone and set up a single-repository component.

    :param name: Component name in uppercase, matching the env variable segment
                 (e.g. ``RESOLVER``, ``MINTER``, ``IPFS``).
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
    commands = env.get(commands_key, DEFAULT_COMMANDS).strip()
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


# ─── Profile installer ────────────────────────────────────────────────────────


def install_profile(prefix: str, env: dict) -> None:
    """Install all components for a given profile prefix.

    Components installed in order:

    1. Blockchain (``dark-env``, ``dark-dapp``, ``dark-explorador``)
    2. Core Lib
    3. Core Admin API
    4. Resolver
    5. Minter
    6. IPFS

    :param prefix: Environment variable prefix matching the active profile
                   (e.g. ``DEVELOPER``, ``SANDBOX``, ``PRODUCTION``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    install_blockchain(prefix=prefix, env=env)


    install_core_lib(prefix=prefix, env=env)
    install_core_admin_api(prefix=prefix, env=env)

    for component in ("RESOLVER", "MINTER", "IPFS"):
        install_single_component(name=component, prefix=prefix, env=env)


# ─── Entry point ─────────────────────────────────────────────────────────────

#: Maps each TYPE value to its environment variable prefix.
PROFILE_PREFIXES: dict = {
    "developer":  "DEVELOPER",
    "sandbox":    "SANDBOX",
    "production": "PRODUCTION",
}


def main() -> None:
    """Entry point for the dark-developer installer.

    Loads the ``.env`` file, reads the ``TYPE`` variable, and dispatches
    execution to the appropriate profile installer.

    :raises SystemExit: If ``TYPE`` is missing or not a recognised profile.
    """
    print("=== dark-developer installer ===\n")

    env = load_env()
    install_type = env.get("TYPE", "").lower()

    if not install_type:
        print("[ERROR] TYPE variable is not set in .env")
        sys.exit(1)

    prefix = PROFILE_PREFIXES.get(install_type)
    if prefix is None:
        valid = ", ".join(PROFILE_PREFIXES.keys())
        print(f"[ERROR] Unknown TYPE '{install_type}'. Valid options: {valid}")
        sys.exit(1)

    print(f"[INFO] Profile: {install_type.upper()}")
    ensure_docker_running()
    install_profile(prefix=prefix, env=env)
    print_install_summary(prefix=prefix, env=env)

    print("=== Installation complete ===")


if __name__ == "__main__":
    main()
