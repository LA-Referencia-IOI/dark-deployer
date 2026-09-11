# dARK Deployer Operations

This document describes the current operational behavior of the v2 deployer.
The old `install.py` lifecycle is historical only and is not an installation
instruction. It complements the architecture and API documentation;
it is the reference for configuration precedence, secrets, reproducible
component versions, validation, upgrades, restarts, and cleanup.

## 1. Safe preflight commands

Use the v2 CLI with one inventory. Templates live in
`examples/deployment-v2/` and are copied into a deployment-specific path.

```bash
venv/bin/python deploy.py validate --inventory examples/deployment-v2/local-ha.json
venv/bin/python deploy.py render --inventory examples/deployment-v2/local-ha.json
venv/bin/python deploy.py verify --inventory examples/deployment-v2/local-ha.json
```

The `install.py` examples below are retained as historical notes and should
not be used for new deployments.

The following commands do not start Docker, clone repositories, or write
configuration:

```bash
python3 install.py validate
python3 install.py plan
```

`validate` checks the saved topology, URLs, chain ID, contract addresses and
signer keys. `plan` performs the same checks and prints a redacted summary.
Neither command prints private key material.

The interactive installation remains:

```bash
python3 install.py
```

For Developer, the wizard offers `Simple` (one peer) and `HA simulation` (two
peers in one site). HA creates two runtime files in `dark-ipfs`:

```text
.env.node.site-a-storage-1
.env.node.site-a-storage-2
```

Each file can be operated independently by passing `ENV_FILE` to Make, which
allows controlled failover tests without deleting persistent volumes:

```bash
make identity ENV_FILE=.env.node.site-a-storage-1
make down ENV_FILE=.env.node.site-a-storage-1
make up ENV_FILE=.env.node.site-a-storage-1
```

### Resume an interrupted installation

Do not rerun completed blockchain stages after a later component fails. Resume
from the first unfinished stage using the saved configuration and handoff:

```bash
python3 install.py resume --from ipfs
python3 install.py resume --from store-api
```

The ordered stages are `blockchain`, `core-lib`, `admin`, `ipfs`, `store-api`,
`resolver`, `minter` and `dashboard`. Resume is explicit: it preserves every
earlier stage, validates the saved handoff, and does not open the wizard.

## 2. Configuration precedence

The installer keeps operator configuration separate from deployment state:

| Source | Purpose | Precedence |
| --- | --- | --- |
| `.env` | Profile, topology, repository URLs, setup options | Normal source |
| `.env.integration` | Public deployed state: RPC, chain, contracts, ABI and Store API | Authoritative for apps-only and remote handoffs |
| `MASTER_WALLET_KEY_FILE` | Shared master-wallet signer, provisioned outside the deployer | Authoritative in `SIGNER_MODE=shared` |
| `.env.integration.secrets` | Legacy Admin and Minter signer handoff | Backward compatibility only |
| `*_PRIVATE_KEY_FILE` | Raw secret mounted by an external secret manager | Preferred over legacy master fallback |

Blank values from `.env.example` never shadow a populated handoff. When an
apps-only `.env` conflicts with its handoff, the handoff wins and the installer
prints the affected variable name without printing either value. Imported
handoff values are not persisted back into the general `.env` file.

Older handoffs containing `DARK_ADMIN_PRIVATE_KEY` in the public file are read
for compatibility and produce a warning. Regenerate them as soon as possible.

## 3. Signer roles

Legacy profiles recognize three separate role variables:

| Role | Direct variable | File variable | Used by |
| --- | --- | --- | --- |
| Contract deployer | `DEPLOYER_PRIVATE_KEY` | `DEPLOYER_PRIVATE_KEY_FILE` | `dark-dapp` deployment |
| Administration | `ADMIN_PRIVATE_KEY` | `ADMIN_PRIVATE_KEY_FILE` | Admin API |
| ARK minting | `MINTER_PRIVATE_KEY` | `MINTER_PRIVATE_KEY_FILE` | Minter API/workers |

Secret files contain the raw hexadecimal key, optionally prefixed with `0x`.
Relative paths are resolved from the deployer repository root.

`MASTER_PRIVATE_KEY` remains a compatibility fallback. Production V1 instead
requires `SIGNER_MODE=shared` and one absolute `MASTER_WALLET_KEY_FILE` with
mode `0600`. The installer derives the public platform address and resolves the
deployer, Admin and Minter roles from that one file.

The Production public handoff never contains a private key. It contains the
derived `DARK_PLATFORM_ADDRESS`; Apps checks this against its independently
provisioned key file. The legacy secret handoff may contain:

```ini
DARK_ADMIN_PRIVATE_KEY=0x...
DARK_MINTER_PRIVATE_KEY=0x...
```

Both handoff files are ignored by Git. Sensitive files and generated service
environment files are written atomically with mode `0600`.

## 4. Component branches

Component versions are selected only by the `*_REPOSITORY_BRANCH` values in
`.env`. Existing repositories are fetched, switched to the configured branch
and advanced only with a fast-forward merge. Wrong origins, non-Git target
directories, divergent branches and conflicting local changes fail instead of
being overwritten.

If a configured component branch is not published by a reachable remote, the
installer prints a warning and uses `main`. Connection, authentication and
other repository failures are not silently replaced by this fallback.

For example:

```ini
PRODUCTION_MINTER_REPOSITORY_BRANCH=main
PRODUCTION_IPFS_REPOSITORY_BRANCH=main
```

Production also requires `DEPLOYER_BRANCH` to match the current deployer
checkout. Component checkouts remain on named branches; no component commit
lock or detached-HEAD checkout is used. Record resulting commit hashes in the
deployment report when release traceability is required.

To change or upgrade a component:

1. Change its `*_REPOSITORY_BRANCH` value in `.env`.
2. Run the desired install or rebuild with `--pull`.
3. Execute the integration tests.

Repository URLs containing credentials are redacted from logs.

## 5. Component setup commands

Use JSON arrays for custom command sequences:

```ini
DEVELOPER_IPFS_COMMANDS_JSON=["make prepare | tee prepare.log","make up"]
```

Each array element is one shell invocation, so pipes and redirects remain
inside that command. Legacy `*_COMMANDS=first|second` fields are still accepted,
but cannot represent a pipeline unambiguously.

## 6. Selective rebuilds

```bash
python3 install.py rebuild store-api
python3 install.py rebuild minter
python3 install.py rebuild minter --skip-migrate
python3 install.py rebuild core-lib --with-dependents
python3 install.py rebuild resolver --pull
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--pull` | Update or clone from the repository branch configured in `.env` |
| `--no-cache` | Disable Docker build cache |
| `--no-start` | Build and generate artifacts without starting containers |
| `--skip-migrate` | Skip the normally automatic minter migration |
| `--with-dependents` | Rebuild admin, resolver and minter after core-lib |

For direct ARK imports without metadata and NAAN authorization audits, see
[Direct ARK Import and NAAN Audit](minter-direct-ark-import.md). The CLI modules
are part of the current `main` branch and are installed when the Minter is
rebuilt from that branch.

For the required DARK 2 `2MM` shoulder format and its minter-code assignment
rules, see [Minter Shoulder Policy](minter-shoulder-policy.md).

The Minter runs three workers alongside the API: metadata persistence, IPFS
replication reconciliation, and chain publication. The reconciler uses a page
size of `100`, status batches of up to `200` unique CIDs, and a two-second
global idle poll only to detect newly arrived work. The initial pin is `1/1`;
after publication, maintenance promotes it to the topology target (normally
`2`) only when metadata and first-pin work are clear. First-pin audits use
`15s → 1min → 5min`; durability audits use `5min → 15min → 1h`. A scheduled
ARK does not cause a Cluster request before its `next_action_at`. The installer
derives these values from the shared topology and writes the generated Minter
environment. `queued` and `pinning` are ordinary transitional states.

## 7. Compose ownership and runtime lifecycle

Docker orchestration is owned by `dark-deployer`. The installer and lifecycle
scripts select only the role-specific files under `compose/`: `apps.yml`,
`blockchain-a.yml` and `storage.yml` for developer, plus their
`*-production.yml` overlays for real hosts. Component repositories provide
application sources and Dockerfiles, but their former operational
`docker-compose.yml` files are no longer entrypoints. This keeps developer and
production host boundaries aligned and avoids a second source of service names,
networks or volumes.

All lifecycle scripts resolve paths from their own repository location, so they
can be invoked from any working directory.

```bash
python3 stop.py
python3 restart.py
python3 clean.py
```

- `stop.py` stops every installed Compose stack and returns non-zero if any
  stack fails.
- `restart.py` starts every installed stack, then polls Admin, Minter, Resolver,
  Store API, IPFS API and IPFS Cluster health endpoints. HTTP errors are failures,
  not successful health checks.
- `clean.py` removes containers, volumes, `components/`, `venv/`, and `dark-net`.
  It asks for confirmation and never silently escalates privileges.

Automation may use:

```bash
python3 clean.py --yes
python3 clean.py --yes --sudo  # explicit fallback for root-owned generated files
```

Cleanup is destructive. `--sudo` applies only to the fixed project-local
`components/` and `venv/` paths.

## 8. CI and local verification

The repository workflow tests Python 3.10 and 3.12. It runs Ruff's critical
checks, compiles all entry points, executes the unit suite, checks installer
help, and rejects private key assignments embedded in documentation.

Equivalent local checks:

```bash
python3 -m pip install -r requirements-dev.txt
ruff check .
python3 -m py_compile install.py pre-install.py restart.py stop.py clean.py
python3 -m unittest discover -s tests -v
```

Unit tests do not replace a real Docker deployment test. Before promotion,
exercise developer, sandbox and production topologies with the component
branches configured in `.env` and retain those branch assignments with the
deployment record.
### Directorios persistentes de storage

La topología es la fuente de verdad para la persistencia IPFS. Cada host con
rol `storage-node` debe incluir `storage_data_root` (ruta absoluta). El
instalador crea la ruta y sus subdirectorios por peer antes de iniciar Compose;
no hay que declarar volúmenes Docker ni editar los `.env.node` a mano. Los
datos existentes deben copiarse al árbol correspondiente antes de cambiar una
ruta. Las rutas de claves siguen siendo independientes y nunca se almacenan en
la topología.

La misma regla aplica a Besu: los hosts `apps`, `blockchain-a` y
`blockchain-b` declaran `blockchain_data_root`. El instalador crea un
subdirectorio por nodo (`rpc01`, `validator01`…`validator04`) y Compose lo
monta en `/data`. Así, cambiar o respaldar la cadena consiste en operar sobre
el filesystem del host, no sobre volúmenes Docker implícitos.
