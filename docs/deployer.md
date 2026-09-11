# dARK Deployer

## Single source of truth

`deployment-topology.json` is the only deployment inventory. Maintained
templates are `examples/deployment-v2/local-simple.json`, `local-ha.json`, and
`production-five-host.json`. The inventory describes hosts, execution mode,
Docker/network groups, Besu assignments, storage peers, component repositories
and branches, public endpoints, and references to secret files. It contains no
secret values. `.env` is optional runtime input, not a second inventory.

## CLI

```bash
venv/bin/python deploy.py inventory-create --template local-ha --output deployment-topology.json
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py plan --inventory deployment-topology.json
venv/bin/python deploy.py render --inventory deployment-topology.json
venv/bin/python deploy.py preflight --inventory deployment-topology.json
venv/bin/python deploy.py install --inventory deployment-topology.json
venv/bin/python deploy.py status --inventory deployment-topology.json
venv/bin/python deploy.py verify --inventory deployment-topology.json
```

Remote deployments use `push`, then `apply`; `resume` continues an interrupted
run. `recreate --group <group> --service <service> --build` rebuilds one service
without restarting the group. `--skip-acquire` reuses prepared checkouts.

## Acquisition and safety

Acquisition clones missing components, fetches the configured branch, and uses
`merge --ff-only`. Divergent or locally modified checkouts stop instead of being
overwritten. Rendering creates public bundles and manifests; private keys and
secret contents are never copied into them. Apply validates host role, artifact
hashes, paths and required Docker capabilities before mutation.

## Wallet and chain artifacts

An inventory may define `secrets.master-wallet.source_path`. If that file exists,
install confirms it and derives the public address automatically. The explicit
`--master-wallet-file` takes precedence. `--create-master-wallet` invokes the
guarded generator when no wallet exists. Existing networks use a provisioned
chain artifact; genesis and identities are not regenerated.

## Local and remote execution

Local inventories run Compose on the current Docker host. Remote inventories
render one bundle per declared host and execute over SSH. Production uses local
Docker networks per host; cross-host Besu and storage communication uses private
or VPN addresses. Developer HA simulates those groups on one machine with a
shared backbone network.

## Rendering and safety

Validation checks schema, role assignments, unique addresses, replica limits,
safe secret paths and component references. Planning resolves groups without
changing state. Rendering produces deterministic Compose and environment
files, endpoint/firewall data, source evidence and a hash manifest. Local apply
invokes Docker Compose; remote apply transfers the same public bundle over SSH.
Private keys and secret contents are never included.

The default order is validators-a, validators-b, apps/RPC and contracts,
storage peers, then APIs and dashboard. `recreate` targets one service while
preserving its persistent data; `resume` continues an interrupted journal.

## Inventory editor

`requirements-tui.txt` enables the Textual editor:

```bash
venv/bin/python deploy.py inventory-edit --inventory deployment-topology.json
```

The editor validates the complete document and saves atomically with a backup.
It never starts Docker or reads secret contents.
