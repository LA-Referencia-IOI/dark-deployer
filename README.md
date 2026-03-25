# dark-developer

Installer for the **dark** ecosystem. It clones and sets up all subcomponent repositories based on a single `.env` configuration file.

---

## Requirements

- Python 3.10+
- Git
- Docker + Docker Compose

---

## Quick Start

### 1. Clone this repository

```bash
git clone https://github.com/your-org/dark-developer.git
cd dark-developer
```

### 2. Create your `.env`

Copy the example file and fill in the values:

```bash
cp .env.example .env
```

Then open `.env` and configure it (see [Environment Variables](#environment-variables) below).

### 3. Run the installer

```bash
python3 install.py
```

The script will clone each configured repository into `components/` and run its setup commands automatically.

---

### 4 . Clean
```bash
python3 clean.py
```
---

### 5. Restart

```bash
python3 restart.py
```
## Environment Variables

### `TYPE`

Defines which profile to install. Must be one of:

| Value        | Description                         |
| ------------ | ----------------------------------- |
| `developer`  | Full local developer infrastructure |
| `sandbox`    | Sandbox / staging stack             |
| `production` | Production stack                    |

```env
TYPE=developer
```

---

### Components

Each component follows this naming pattern:

```
{PROFILE}_{COMPONENT}_REPOSITORY_URL=    # Git URL (leave empty to skip)
{PROFILE}_{COMPONENT}_REPOSITORY_BRANCH= # Branch to clone (default: master)
{PROFILE}_{COMPONENT}_SETUP=             # True = clone + run commands / False = clone only
{PROFILE}_{COMPONENT}_COMMANDS=          # | -separated commands to run when SETUP=True
```

#### Blockchain submodules

The `blockchain` component has three independent sub-repositories:

| Submodule         | Variable prefix                         |
| ----------------- | --------------------------------------- |
| `dark-env`        | `{PROFILE}_BLOCKCHAIN_DARK_ENV_`        |
| `dark-dapp`       | `{PROFILE}_BLOCKCHAIN_DARK_DAPP_`       |
| `dark-explorador` | `{PROFILE}_BLOCKCHAIN_DARK_EXPLORADOR_` |

#### Other components

| Component    | Variable prefix           |
| ------------ | ------------------------- |
| Core Lib     | `{PROFILE}_CORE_LIB_`     |
| Core Admin API | `{PROFILE}_CORE_ADMIN_API_` |
| Store API    | `{PROFILE}_STORE_API_`    |
| Resolver API | `{PROFILE}_RESOLVER_`     |
| Minter       | `{PROFILE}_MINTER_`       |
| IPFS         | `{PROFILE}_IPFS_`         |

> **Tip:** Any component with an empty `_REPOSITORY_URL` is automatically skipped. You can configure only the repositories you need.

---

### `COMMANDS`

Each repository can define its own startup commands, separated by `|`:

```env
DEVELOPER_BLOCKCHAIN_DARK_ENV_COMMANDS=chmod +x setup.sh|chmod +x scripts/*.sh|./setup.sh|docker compose up -d
```

Commands are executed **in sequence** inside the cloned repository directory. Shell syntax (globs, etc.) is supported. If `COMMANDS` is not set, the following default is used:

```
chmod +x setup.sh | chmod +x scripts/*.sh | ./setup.sh | docker compose up -d
```

---

### `SETUP`

> [!WARNING]
> **Always use the full per-component variable name.** Using a bare `SETUP=False` in `.env` has no effect — the installer looks for `{PROFILE}_{COMPONENT}_SETUP` and falls back to `True` if it is not found.
>
> ❌ `SETUP=False` — ignored  
> ✅ `DEVELOPER_BLOCKCHAIN_DARK_DAPP_SETUP=False` — correct

| Value              | Behaviour                                             |
| ------------------ | ----------------------------------------------------- |
| `True` _(default)_ | Clones the repository **and** runs all `COMMANDS`     |
| `False`            | Clones the repository only — no commands are executed |

Use `False` when you want to download a component to inspect or configure it manually before starting it.

```env
# Download only, do not start the service
DEVELOPER_BLOCKCHAIN_DARK_DAPP_SETUP=False

# Download and start immediately
DEVELOPER_BLOCKCHAIN_DARK_ENV_SETUP=True
```

---

### Python dependencies

If a repository contains a `requirements.txt`, the installer will automatically:

1. Create (or reuse) a shared root virtual environment at `./venv`.
2. Install dependencies via `./venv/bin/pip install -r requirements.txt`.

This happens **before** the `COMMANDS` are executed.

---

### Core Lib auto-configuration

When `core-lib` setup is enabled, the installer also generates:

`components/libraries/dark-core-lib/.env.integration`

using values from:

- Global `.env`: `RPC_URL`, `CHAIN_ID`, `MASTER_PRIVATE_KEY`
- Deployed contracts file: `components/blockchain/dark-dapp/dARK_dapp/deployed_contracts.ini`

This keeps `dark-core-lib` aligned with the blockchain and contract addresses deployed during installation.

---

## Installed directory structure

After running the installer, components are placed under `components/`:

```
components/
├── blockchain/
│   ├── dark-env/
│   ├── dark-dapp/
│   ├── dark-explorador/
│   └── dark-ipfs/
├── libraries/
│   ├── dark-core-lib/
│   │   └── .env.integration
├── services/
│   ├── dark-core-admin-api/
│   │   └── .env.integration
│   ├── dark-store-api/
│   │   └── .env.integration
│   ├── dark-core-resolver-api/
│   │   └── .env.integration
│   └── dark-core-minter-api/
│       └── .env.integration
```
