#!/usr/bin/env python3
"""
dark-developer installer.

Reads the ``TYPE`` variable from the ``.env`` file and clones all
subcomponent repositories into the ``components/`` directory, then
runs the setup routine for each one.

Usage::

    python install.py

Supported profiles:
    - ``developer``  — installs the full developer infrastructure.
    - ``sandbox``    — installs the sandbox stack.
    - ``production`` — installs the production stack.

Components installed (per profile):
    - **blockchain** — three sub-repos: ``dark-env``, ``dark-adpp``, ``dark-explorador``
    - **orchestrator**
    - **resolver**
    - **minter**
    - **ipfs**
"""

import subprocess
import sys
from pathlib import Path

# ─── Helpers ─────────────────────────────────────────────────────────────────


def load_env(filepath: str = ".env") -> dict:
    """Parse a ``.env`` file and return its contents as a dictionary.

    Lines starting with ``#`` and empty lines are ignored.
    Only lines containing ``=`` are parsed.

    :param filepath: Path to the ``.env`` file.
    :type filepath: str
    :returns: Dictionary mapping variable names to their values.
    :rtype: dict
    :raises SystemExit: If the file does not exist.
    """
    env: dict = {}
    path = Path(filepath)

    if not path.exists():
        print(
            f"[ERROR] '{filepath}' not found. "
            "Copy .env.example to .env and fill in the values."
        )
        sys.exit(1)

    with open(path) as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    return env


def run(cmd: list, cwd: str = None) -> None:
    """Execute a shell command and exit the process on failure.

    :param cmd: Command and its arguments as a list of strings.
    :type cmd: list
    :param cwd: Working directory for the command. Defaults to ``None``.
    :type cwd: str, optional
    :raises SystemExit: If the command returns a non-zero exit code.
    """
    print(f"[RUN] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)

    if result.returncode != 0:
        print(f"[ERROR] Command failed: {' '.join(cmd)}")
        sys.exit(result.returncode)


# ─── Component installers ─────────────────────────────────────────────────────


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
    target = Path(target_dir)

    if target.exists():
        print(f"[INFO] '{target_dir}' already exists — pulling latest changes...")
        run(["git", "pull", "origin", branch], cwd=str(target))
    else:
        print(f"[INFO] Cloning '{name}' from {repo_url} (branch: {branch})...")
        run(["git", "clone", "--branch", branch, repo_url, str(target)])

    print(f"[OK] '{name}' ready at '{target_dir}'.\n")


def setup_component(target_dir: str) -> None:
    """Run the standard post-clone setup for any component.

    Performs the following steps inside ``target_dir``:

    1. If ``requirements.txt`` is present, creates a ``venv`` virtual
       environment and installs all Python dependencies via ``pip``.
    2. Makes ``setup.sh`` and all scripts inside ``scripts/`` executable.
    3. Executes ``./setup.sh`` to initialise the component.
    4. Brings up the Docker Compose stack in detached mode.

    :param target_dir: Path to the cloned component directory.
    :type target_dir: str
    :raises SystemExit: If any step fails.
    """
    print(f"[INFO] Setting up component at '{target_dir}'...")

    # ── Python dependencies (optional) ───────────────────────────────────────
    requirements = Path(target_dir) / "requirements.txt"
    venv_dir = Path(target_dir) / "venv"

    if requirements.exists():
        print(f"[INFO] requirements.txt found — creating virtual environment...")

        # Create the virtual environment if it does not already exist
        if not venv_dir.exists():
            run([sys.executable, "-m", "venv", str(venv_dir)])
        else:
            print(f"[INFO] venv already exists at '{venv_dir}', skipping creation.")

        # Install dependencies using the venv's own pip
        pip = str(venv_dir / "bin" / "pip")
        run([pip, "install", "-r", "requirements.txt"], cwd=target_dir)
        print(f"[OK] Python dependencies installed into '{venv_dir}'.\n")

    # ── Shell scripts ─────────────────────────────────────────────────────────
    # Make setup.sh and all helper scripts executable
    run(["chmod", "+x", "setup.sh"], cwd=target_dir)
    run(["bash", "-c", "chmod +x scripts/*.sh"], cwd=target_dir)

    # Execute the setup script
    run(["./setup.sh"], cwd=target_dir)

    # Start the Docker Compose stack in detached mode
    run(["docker", "compose", "up", "-d"], cwd=target_dir)

    print(f"[OK] Component at '{target_dir}' is up.\n")


def require_url(env: dict, key: str) -> str:
    """Read a repository URL from ``env`` and exit if it is empty.

    :param env: Dictionary of environment variables.
    :type env: dict
    :param key: Variable name to look up.
    :type key: str
    :returns: The non-empty URL value.
    :rtype: str
    :raises SystemExit: If the variable is missing or empty.
    """
    value = env.get(key, "").strip()
    if not value:
        print(f"[ERROR] '{key}' is not set in .env")
        sys.exit(1)
    return value


# ─── Blockchain submodule installer ──────────────────────────────────────────


def install_blockchain(prefix: str, env: dict) -> None:
    """Clone and set up the three blockchain submodules.

    Each submodule is an independent Git repository that gets installed
    under ``components/blockchain/<submodule>/``.

    Submodules:
        - ``dark-env``
        - ``dark-adpp``
        - ``dark-explorador``

    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    print("\n── Blockchain ────────────────────────────────────────────────")

    #: Mapping of submodule names to their env-variable suffixes.
    submodules: dict = {
        "dark-env":        "DARK_ENV",
        "dark-adpp":       "DARK_ADPP",
        "dark-explorador": "DARK_EXPLORADOR",
    }

    for folder, suffix in submodules.items():
        url_key    = f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_URL"
        branch_key = f"{prefix}_BLOCKCHAIN_{suffix}_REPOSITORY_BRANCH"

        repo_url = require_url(env, url_key)
        branch   = env.get(branch_key, "master").strip()
        target   = f"components/blockchain/{folder}"

        install_repo(name=folder, repo_url=repo_url, branch=branch, target_dir=target)
        setup_component(target_dir=target)


# ─── Generic single-repo component installer ─────────────────────────────────


def install_single_component(name: str, prefix: str, env: dict) -> None:
    """Clone and set up a single-repository component.

    The ``name`` argument is used both for logging and to build the
    environment variable keys, e.g. ``DEVELOPER_ORCHESTRATOR_REPOSITORY_URL``.

    :param name: Component name in uppercase, matching the env variable segment
                 (e.g. ``ORCHESTRATOR``, ``RESOLVER``, ``MINTER``, ``IPFS``).
    :type name: str
    :param prefix: Environment variable prefix (e.g. ``DEVELOPER``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    print(f"\n── {name.capitalize()} ──────────────────────────────────────────────")

    url_key    = f"{prefix}_{name}_REPOSITORY_URL"
    branch_key = f"{prefix}_{name}_REPOSITORY_BRANCH"

    repo_url = require_url(env, url_key)
    branch   = env.get(branch_key, "master").strip()
    target   = f"components/{name.lower()}"

    install_repo(name=name.lower(), repo_url=repo_url, branch=branch, target_dir=target)
    setup_component(target_dir=target)


# ─── Profile installers ───────────────────────────────────────────────────────


def install_profile(prefix: str, env: dict) -> None:
    """Install all components for a given profile prefix.

    Components installed in order:

    1. Blockchain (``dark-env``, ``dark-adpp``, ``dark-explorador``)
    2. Orchestrator
    3. Resolver
    4. Minter
    5. IPFS

    :param prefix: Environment variable prefix matching the active profile
                   (e.g. ``DEVELOPER``, ``SANDBOX``, ``PRODUCTION``).
    :type prefix: str
    :param env: Dictionary of environment variables loaded from ``.env``.
    :type env: dict
    """
    install_blockchain(prefix=prefix, env=env)

    for component in ("ORCHESTRATOR", "RESOLVER", "MINTER", "IPFS"):
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
    install_profile(prefix=prefix, env=env)

    print("=== Installation complete ===")


if __name__ == "__main__":
    main()
