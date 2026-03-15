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
    - **orchestrator**
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
    target = Path(target_dir)

    if target.exists():
        print(f"[INFO] '{target_dir}' already exists — pulling latest changes...")
        run_shell(f"git pull origin {branch}", cwd=str(target))
    else:
        print(f"[INFO] Cloning '{name}' from {repo_url} (branch: {branch})...")
        run_shell(f"git clone --branch {branch} {repo_url} {str(target)}")

    print(f"[OK] '{name}' ready at '{target_dir}'.\n")


def setup_repo(target_dir: str, commands_str: str) -> None:
    """Run the post-clone setup for a repository.

    If a ``requirements.txt`` is present in ``target_dir``, a Python
    virtual environment named ``venv`` is created (or reused) and all
    dependencies are installed via ``pip`` before any other commands run.

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

    # ── Python dependencies (optional) ───────────────────────────────────────
    requirements = abs_target / "requirements.txt"
    venv_dir = abs_target / "venv"

    if requirements.exists():
        print("[INFO] requirements.txt found — creating virtual environment...")

        # Create the virtual environment if it does not already exist
        if not venv_dir.exists():
            run_shell(f"{sys.executable} -m venv {venv_dir}")
        else:
            print(f"[INFO] venv already exists at '{venv_dir}', skipping creation.")

        # Install dependencies using the venv's own pip
        pip = str(venv_dir / "bin" / "pip")
        run_shell(f"{pip} install -r requirements.txt", cwd=str(abs_target))
        print(f"[OK] Python dependencies installed into '{venv_dir}'.\n")

    # ── Run per-repo commands ─────────────────────────────────────────────────
    run_commands(commands_str, cwd=str(abs_target))

    print(f"[OK] '{target_dir}' is up.\n")


# ─── dark-dapp specific installer ────────────────────────────────────────────


def install_dark_dapp(target_dir: str, env: dict) -> None:
    """Run the full setup sequence for the ``dark-dapp`` component.

    Performs the following steps inside ``target_dir``:

    1. Installs Python dependencies into a ``venv`` (``requirements.txt``).
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
    import configparser

    abs_target = Path(target_dir).resolve()
    venv_dir   = abs_target / "venv"
    pip        = str(venv_dir / "bin" / "pip")
    python     = str(venv_dir / "bin" / "python")

    # ── Step 1: virtual environment + pip install ─────────────────────────────
    print("[INFO] Installing dark-dapp Python dependencies...")
    if not venv_dir.exists():
        run_shell(f"{sys.executable} -m venv {venv_dir}")
    else:
        print(f"[INFO] venv already exists at '{venv_dir}', skipping creation.")

    run_shell(f"{pip} install -r requirements.txt", cwd=str(abs_target))
    print(f"[OK] Python dependencies installed.\n")

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
    # wait_for_rpc(rpc_url)
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

# ─── Orchestrator submodule installer ──────────────────────────────────────────

def install_orchestrator(prefix: str, env: dict) -> None:
    """
    Instala componentes do Orchestrator num venv partilhado e 
    configura o .env da lib com valores dinâmicos do blockchain.
    """
    import configparser
    print("\n── Orchestrator (Shared Venv & Auto-Config) ──────────────────")

    core_folder = "dark-core-orchestrator"
    lib_folder  = "dark-observer-lib"
    base_path   = Path("components/orchestrator")
    core_path   = base_path / core_folder
    lib_path    = base_path / lib_folder

    # 1. instalar o CORE no venv partilhado
    repo_url_core = get_url(env, f"{prefix}_ORCHESTRATOR_REPOSITORY_URL")
    if repo_url_core:
        install_repo(core_folder, repo_url_core, env.get(f"{prefix}_ORCHESTRATOR_REPOSITORY_BRANCH", "main"), str(core_path))
        setup_repo(str(core_path), "") # create venv and install dependencies, but skip custom commands for now
    else:
        print("[ERROR] Core Orchestrator URL not found."); return

    shared_pip = str(core_path.resolve() / "venv" / "bin" / "pip")

    # 2. install LIB
    repo_url_lib = get_url(env, f"{prefix}_ORCHESTRATOR_OBSERVER_LIB_REPOSITORY_URL")
    if repo_url_lib:
        install_repo(lib_folder, repo_url_lib, env.get(f"{prefix}_ORCHESTRATOR_OBSERVER_LIB_REPOSITORY_BRANCH", "main"), str(lib_path))
        
        # --- populate .env  ---
        print(f"[INFO] A configurar .env para {lib_folder}...")
        
        chain_id = env.get("CHAIN_ID", "1337")

        contract_address = "0x0"
        ini_path = Path("components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini")
        
        if ini_path.exists():
            deployed_config = configparser.ConfigParser()
            deployed_config.read(ini_path)
            try:
                contract_address = deployed_config['dARK']['address']
            except KeyError:
                print(f"[WARNING] Secção [dARK] ou 'address' não encontrado em {ini_path}")
        else:
            print(f"[WARNING] Ficheiro de contratos não encontrado em {ini_path}")

        # c) write .env for the lib
        lib_env_path = lib_path / ".env"
        with open(lib_env_path, "w") as f:
            f.write(f"DARK_CHAIN_ID={chain_id}\n")
            f.write(f"DARK_CONTRACT_ADDRESS={contract_address}\n")
        print(f"[OK] .env gerado em {lib_env_path}")

        # setup lib with pip install -e, using the CORE's venv to ensure they share the same environment
        print(f"[INFO] A instalar {lib_folder} no venv do {core_folder}...")
        run_shell(f"{shared_pip} install -e {lib_path.resolve()}", cwd=str(core_path.resolve()))
    
    # 3. run CORE commands (if any) after the lib is installed, so they can rely on the lib being present in the venv
    core_cmds = env.get(f"{prefix}_ORCHESTRATOR_COMMANDS", "").strip()
    if core_cmds:
        core_cmds = core_cmds.replace("pip ", f"{shared_pip} ")
        run_commands(core_cmds, cwd=str(core_path.resolve()))

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

    install_repo(name=name.lower(), repo_url=repo_url, branch=branch, target_dir=target)

    if do_setup:
        setup_repo(target_dir=target, commands_str=commands)
    else:
        print(f"[INFO] SETUP=False — '{name.lower()}' cloned, setup skipped.\n")


# ─── Profile installer ────────────────────────────────────────────────────────


def install_profile(prefix: str, env: dict) -> None:
    """Install all components for a given profile prefix.

    Components installed in order:

    1. Blockchain (``dark-env``, ``dark-dapp``, ``dark-explorador``)
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


    install_orchestrator(prefix=prefix, env=env)

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
    install_profile(prefix=prefix, env=env)

    print("=== Installation complete ===")


if __name__ == "__main__":
    main()
