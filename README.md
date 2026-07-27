# dARK Deployer

![dARK Logo](docs/figures/dARK_logo.png)

**dARK** (Decentralized Archival Resource Key) is a blockchain-based implementation of the [ARK](https://arks.org/) identifier scheme.

**dARK is public, federated digital infrastructure.** It ensures permanent, decentralized access to identifiers while preventing proprietary appropriation of the core protocol.

This repository is the **dark-deployer**: an orchestrator that clones, configures, and starts the full dARK 2.0 stack from a single `.env` file.

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.7442743.svg)](https://doi.org/10.5281/zenodo.7442743)

---

## Documentation

- 📖 **[Developer Guide](DARK_2.0_GUIDE.md)**: ARK identifier format, on-chain data structures, ARK lifecycle states, Python SDK (`dark-core-lib`) usage, batch publishing pipeline, and error handling.
- 🏗 **[Architecture](DARK_2.0_ARCHITECTURE.md)**: Full system architecture — two-contract design, service layer, minter worker lifecycle, resolver resolution modes, storage layer (IPFS + store-api), and SDK pipelining.
- ⚙️ **[API Reference](DARK_2.0_API_REFERENCE.md)**: All REST APIs (Admin :8000, Minter :8001, Resolver :8002, Store :8003), smart contract interfaces, events, and integration guidelines.
- 🧠 **[IPFS Concepts and Store API](docs/ipfs-concepts-and-dark-store-api.md)**: Technical explanation of IPFS content addressing, CIDs, blocks, pinning, IPFS Cluster, failure modes, and how those concepts shape dARK Store API.
- 🗄 **[IPFS, Cluster and Store API](docs/ipfs-cluster-store-api-real-environment.md)**: How the Store API, local IPFS node, IPFS Cluster peer, Cluster Proxy, Docker networks, health checks, and replication policy fit together in a real environment.
- 🔐 **[Minter Signed Client Keys](docs/minter-signed-client-keys.md)**: Deployment-key style authentication for Minter clients using authority-bound public keys, signed headers, replay protection, and Admin API key registry.

---

## System Overview

dARK 2.0 is composed of several independent services that this deployer clones and wires together:

| Component | Description | Default port |
| --------- | ----------- | ------------ |
| `dark-env` | Hyperledger Besu blockchain node + genesis | — |
| `dark-dapp` | Solidity smart contracts (`Authority.sol` + `dARK.sol`), compiled and deployed automatically | RPC via `.env` |
| `dark-explorador` | Block explorer (Nginx-based) | `EXPLORER_PORT` |
| `dark-ipfs` | IPFS + IPFS Cluster storage backend | 5001 / 9094 |
| `dark-core-lib` | Python SDK — shared by all services | — |
| `dark-core-admin-api` | REST API for authority and NAAN management | 8000 |
| `dark-core-minter-api` | REST API for ARK minting (split metadata + chain workers) | 8001 |
| `dark-core-resolver-api` | REST API for ARK resolution | 8002 |
| `dark-store-api` | REST API wrapping IPFS storage | 8003 |
| `dashboard-web` | Laravel/Vue web dashboard for managing authorities, NAANs, and ARK records | 8081 |

The smart contracts follow a **two-contract modular architecture**:

| Contract | Role |
| -------- | ---- |
| `Authority.sol` | Registers organizations, binds UUIDs to wallets, manages NAAN permissions |
| `dARK.sol` | Pure ARK identifier storage — delegates all permission checks to `Authority.sol` |

```
Admin ──► register_authority ──► Authority.sol ◄── is_authorized? ◄── dARK.sol ◄── create_ark ◄── Authority Wallet
```

---

## Project Structure

```
dark-deployer/
├── install.py          # Main installer — reads .env and sets up all components
├── stop.py             # Gracefully stops all Docker Compose stacks
├── restart.py          # Restarts all Docker Compose stacks and probes endpoints
├── clean.py            # Full cleanup: stops containers, deletes components/ and venv/
├── requirements.txt    # Root-level Python dependencies (web3, py-solc-x, …)
├── .env                # Configuration (copy from .env.example)
└── components/         # Created by install.py — one sub-directory per component
    ├── blockchain/
    │   ├── dark-env/           # Besu node
    │   ├── dark-dapp/          # Smart contracts
    │   │   └── dARK_dapp/
    │   │       ├── contracts/  # Authority.sol, dARK.sol, IAuthority.sol
    │   │       ├── compiled/   # ABI + bytecode (generated)
    │   │       └── deployed_contracts.ini  # Deployed addresses (generated)
    │   ├── dark-explorador/    # Block explorer
    │   └── dark-ipfs/          # IPFS + Cluster
    ├── frontend/
    │   └── dashboard-web/      # Web dashboard (Laravel + Vue)
    ├── libraries/
    │   └── dark-core-lib/      # Shared Python SDK
    └── services/
        ├── dark-core-admin-api/
        ├── dark-core-minter-api/
        ├── dark-core-resolver-api/
        └── dark-store-api/
```

---

## Prerequisites

- Python 3.10+
- Docker (daemon must be running)
- Git

---

## Quick Start

### 1. Configure

Copy the example env file and fill in the required values:

```bash
cp .env.example .env
```

Key variables in `.env`:

```ini
# Installation profile: developer | sandbox | production
TYPE=developer

# Git repository URLs for each component (set only the ones you need)
DEVELOPER_BLOCKCHAIN_DARK_ENV_REPOSITORY_URL=https://github.com/LA-Referencia-IOI/dark-env
DEVELOPER_BLOCKCHAIN_DARK_DAPP_REPOSITORY_URL=https://github.com/LA-Referencia-IOI/dark-dapp
# ... (see .env.example for the full list)

# Blockchain connection — populated automatically from dark-env after first run
RPC_URL=http://localhost:8545
CHAIN_ID=1337
MASTER_WALLET_ADDRESS=
MASTER_PRIVATE_KEY=
MASTER_PUBLIC_KEY=
```

> **Note:** `MASTER_WALLET_ADDRESS`, `MASTER_PRIVATE_KEY`, and `MASTER_PUBLIC_KEY` are extracted automatically from `dark-env/master-wallet.txt` the first time `dark-env` is installed and written back to `.env`.

### 2. Install

```bash
python3 install.py
```

`install.py` will, in order:

1. Validate Python version and Docker availability.
2. Clone (or pull) every component repository whose URL is set in `.env`.
3. For `dark-env`: run `setup.sh` and `docker compose up -d`, then extract the master wallet into `.env`.
4. For `dark-dapp`: create an isolated venv, generate `config.ini`, compile Solidity contracts, wait for the RPC node, and deploy contracts.
5. For each service: generate a `.env.integration` file from the deployed contract addresses, install Python packages, and start the Docker Compose stack.
6. For `dashboard-web`: clone the repository and delegate setup to its own `install.py`, which builds the Docker stack, runs migrations, and seeds initial users.
7. Print a live service summary with health status for every endpoint.

---

## Service Endpoints (default)

| Service | URL | Docs |
| ------- | --- | ---- |
| Blockchain RPC | `RPC_URL` from `.env` | — |
| Block Explorer | `http://localhost:<EXPLORER_PORT>` | — |
| Core Admin API | `http://localhost:8000` | `/docs` |
| Core Minter API | `http://localhost:8001` | `/docs` |
| Core Resolver API | `http://localhost:8002` | `/docs` |
| Store API | `http://localhost:8003` | `/docs` |
| IPFS API | `http://localhost:5001` | — |
| IPFS Cluster | `http://localhost:9094` | — |
| Dashboard | `http://localhost:8081` | — |

---

## Operational Scripts

```bash
# Stop all stacks gracefully (containers preserved)
python3 stop.py

# Restart all stacks and probe health endpoints
python3 restart.py

# Rebuild only one installed component
python3 install.py rebuild store-api
python3 install.py rebuild minter
python3 install.py rebuild minter --skip-migrate
python3 install.py rebuild core-lib --with-dependents

# Full cleanup: stop + remove containers/volumes, delete components/ and venv/
python3 clean.py
```

`install.py rebuild <component>` accepts `store-api`, `minter`, `resolver`, `admin`, and `core-lib`.
Use `--no-cache` for a clean Docker rebuild, `--no-start` to build without restarting containers,
and `--pull` to update the configured repository before rebuilding. Minter rebuilds run database
migrations by default; use `--skip-migrate` only when you explicitly need to skip them.

---

## Installation Profiles

The `TYPE` variable in `.env` selects the active profile:

| Profile | Env prefix | Typical use |
| ------- | ---------- | ----------- |
| `developer` | `DEVELOPER_` | Local development with all components |
| `sandbox` | `SANDBOX_` | Shared test environment |
| `production` | `PRODUCTION_` | Production deployment |

Each profile reads component URLs and settings from its own set of prefixed variables (e.g., `DEVELOPER_BLOCKCHAIN_DARK_ENV_REPOSITORY_URL`). Only components with a URL set are installed; the rest are silently skipped.

---

## Decoupled Setup (Sandbox / Production)

`sandbox` and `production` profiles support installing the **blockchain**, **storage**, and **apps** tiers on separate servers. The setup wizard (`python3 install.py`) only ever asks you to **pick from a menu** — profile, which components go on this server, whether a tier is local or remote. It never asks you to type a URL, address, or key.

**All connection information must already be in place before you run the wizard.** If a required field is missing for the selection you make, the installer prints exactly which fields are empty and where to put them, then exits — it does not fall back to asking interactively.

### The `.env.integration` handoff file

This is how information moves between servers without anyone typing it twice:

1. On the **blockchain** server, after a local install, `install.py` writes a root `.env.integration` file with the blockchain connection vars.
2. On the **storage** server, running the installer with that same file present appends the storage vars to it.
3. Copy the resulting `.env.integration` to the **apps** server, in the `dark-deployer` root directory, before running its installer.

| Key in `.env.integration` | Written by | Read by |
| --- | --- | --- |
| `DARK_RPC_URL` | blockchain tier | apps tier (`RPC_URL`), remote-blockchain selection |
| `DARK_CHAIN_ID` | blockchain tier | apps tier (`CHAIN_ID`) |
| `DARK_CONTRACT_ADDRESS` | blockchain tier | apps tier (`{PREFIX}_DARK_CONTRACT_ADDRESS`) |
| `DARK_AUTHORITY_ADDRESS` | blockchain tier | apps tier (`{PREFIX}_AUTHORITY_CONTRACT_ADDRESS`) |
| `DARK_ADMIN_PRIVATE_KEY` | blockchain tier | apps tier (`MASTER_PRIVATE_KEY`) |
| `METADATA_STORAGE_TYPE` | storage tier | apps tier |
| `METADATA_STORE_API_URL` | storage tier | apps tier (`{PREFIX}_STORE_API_URL`) |

`.env.integration` contains a private key — it is intentionally excluded from version control (`.gitignore`). Transfer it out-of-band (`scp`, a secrets manager, etc.), never through git.

### What each selection requires

| Selection | Required beforehand | Where to fill it |
| --- | --- | --- |
| Components = **apps** | `RPC_URL`, `{PREFIX}_DARK_CONTRACT_ADDRESS`, `{PREFIX}_AUTHORITY_CONTRACT_ADDRESS`, `MASTER_PRIVATE_KEY`, `{PREFIX}_STORE_API_URL` | Copy `.env.integration` from the blockchain/storage servers into this directory (recommended), or set the `{PREFIX}_*` vars directly in `.env` |
| Blockchain tier = **remote** (within an "all" install) | Same blockchain fields as above | `.env.integration` or `.env` |
| Blockchain tier = **local** | Nothing — repo URLs default to the public `LA-Referencia-IOI` repos if left blank in `.env` | — |
| IPFS tier = **remote** (dark-store-api here, IPFS elsewhere) | `{PREFIX}_IPFS_API_URL`, `{PREFIX}_IPFS_CLUSTER_URL` | `.env` |
| IPFS tier = **local** | Nothing — `{PREFIX}_STORE_API_URL` defaults to the Docker service name `http://store-api:8003` (correct when apps run on the same server) | Only needed if apps run on a *different* server; set `{PREFIX}_STORE_API_URL` in `.env` to this server's external address |

If you run the wizard and a required field is missing, you'll see something like:

```
[ERROR] Missing required configuration for installing "apps".

  The following fields are not set. Fill them in the root '.env.integration'
  (copied from the blockchain/storage servers) or directly in '.env', then re-run:

    - RPC_URL                                Blockchain RPC URL
      .env.integration key: DARK_RPC_URL
    - SANDBOX_STORE_API_URL                   Store API URL (used by minter/resolver to reach storage)
      .env.integration key: METADATA_STORE_API_URL
```

---

## ARK Lifecycle (Summary)

**Via smart contracts directly (SDK / `dark-core-lib`):**
```
1. Admin calls:      Authority.register_authority(uuid, wallet, enc_key)
2. Authority calls:  Authority.authorize_naan(naan)
3. Authority calls:  dARK.create_ark(naan, name, url, cid)
4. Anyone calls:     dARK.resolve(naan, name)  →  returns url
```

**Via the Minter API (`dark-core-minter-api`):**
```
1. POST /arks              → state: reserved (R)  — NOID identifier allocated
2. PUT  /arks/{ark}        → state: draft    (D)  — metadata accepted, on-chain creation queued
   [metadata worker]       → IPFS persistence (level-1 + level-2 CIDs stored)
   [chain worker]          → dARK.create_ark() submitted and confirmed
                           → state: published (P)
3. PUT  /arks/{ark}        → state: update   (U)  — updated metadata queued
   [chain worker]          → dARK.update_ark() confirmed
                           → state: published (P)
4. DELETE /arks/{ark}      → state: tombstone (T)  — irreversible
5. GET /arks/{ark:path}    → resolver redirects to URL
   GET /arks/{ark}?info    → full ARK record (JSON)
```

---

## License

### Software License

The dARK source code is licensed under the **GNU Affero General Public License v3.0 (AGPLv3)**.

- You are free to use, modify, and distribute the software without cost, provided that network services built on dARK also make their source code available (closing the "ASP loophole").
- See the [LICENSE](LICENSE) file for the full text.

### Documentation License

Documentation and non-code assets are licensed under **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

---

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for details on how to propose changes.

---

## Links

- [ARK Alliance](https://arks.org/)
- [Hyperledger Besu](https://besu.hyperledger.org/)
- [DOI: 10.5281/zenodo.7442743](https://doi.org/10.5281/zenodo.7442743)
