# dARK Deployer Operations

This document describes the current operational behavior of `install.py` and
the lifecycle scripts. It complements the architecture and API documentation;
it is the reference for configuration precedence, secrets, reproducible
component versions, validation, upgrades, restarts, and cleanup.

## 1. Safe preflight commands

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
| `PLATFORM_PRIVATE_KEY_FILE` | Shared Production V1 signer, provisioned outside the deployer | Authoritative in `SIGNER_MODE=shared` |
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
requires `SIGNER_MODE=shared` and one absolute `PLATFORM_PRIVATE_KEY_FILE` with
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

## 4. Reproducible component versions

Without a lock, existing repositories are fetched, switched to their configured
branch and advanced only with a fast-forward merge. Wrong origins, non-Git
target directories, divergent branches and conflicting local changes fail
instead of being overwritten.

After testing a complete stack, record every installed component commit:

```bash
python3 install.py lock
git add components.lock.json
git commit -m "Lock tested dARK component versions"
```

Production requires `components.lock.json`, a complete entry for every selected
component, and `DEPLOYER_COMMIT` matching the current checkout. Other profiles
may omit the lock. When present, installation checks out the exact recorded
commit in detached-HEAD mode. Verify an installed server against the lock with:

```bash
python3 install.py lock --check
```

To upgrade intentionally:

1. Move or temporarily remove the existing lock.
2. Run the desired installs/rebuilds with `--pull`.
3. Execute integration tests.
4. Regenerate and commit `components.lock.json`.

Repository URLs containing credentials are redacted from logs and stored in
the lock without HTTP user-info.

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
| `--pull` | Update or clone before rebuilding; respects `components.lock.json` |
| `--no-cache` | Disable Docker build cache |
| `--no-start` | Build and generate artifacts without starting containers |
| `--skip-migrate` | Skip the normally automatic minter migration |
| `--with-dependents` | Rebuild admin, resolver and minter after core-lib |

For direct ARK imports without metadata and NAAN authorization audits, see
[Direct ARK Import and NAAN Audit](minter-direct-ark-import.md). Those commands
require a Minter revision that includes their CLI modules; update
`components.lock.json` intentionally before rebuilding a deployed component.

For the required DARK 2 `2MM` shoulder format and its minter-code assignment
rules, see [Minter Shoulder Policy](minter-shoulder-policy.md).

## 7. Runtime lifecycle

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
exercise developer, sandbox and production topologies with the actual component
repositories and retain the generated component lock as the tested bill of
materials.
