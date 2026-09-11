# dARK Deployer

Declarative deployment tooling for the dARK blockchain, application services,
dashboard, and IPFS storage. The supported entry point is `deploy.py`; the
single source of deployment configuration is a v2 inventory JSON.

## Architecture

The inventory describes hosts, networks, Besu roles, storage peers, component
branches, and secret references. The standard production layout is:

| Group | Services |
| --- | --- |
| `apps` | non-validator RPC, contracts, Admin, Minter, Resolver, Store API, dashboard |
| `validators-a` | validators 01/02 and explorer |
| `validators-b` | validators 03/04 |
| `storage` | Kubo and IPFS Cluster peers |

Developer templates simulate these groups on one Docker host. Maintained
templates are in [`examples/deployment-v2/`](examples/deployment-v2/):
`local-simple.json`, `local-ha.json`, and `production-five-host.json`.

## Requirements

- Python 3.10+ (3.12 recommended)
- Docker Engine and Compose v2 for local execution
- SSH and reachable private/VPN addresses for remote hosts
- Secret files provisioned at paths declared by the inventory

Never commit private keys or generated runtime files.

## Quick start

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python deploy.py inventory-create --template local-ha \
  --output deployment-topology.json
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py plan --inventory deployment-topology.json
venv/bin/python deploy.py install --inventory deployment-topology.json
```

`install` acquires component repositories, renders groups, starts them in
dependency order, and verifies the result. Existing checkouts are updated with
`fetch` and `merge --ff-only`; divergent local changes stop safely. Use
`--skip-acquire` to reuse prepared checkouts.

For an existing chain, provision its private artifact and pass
`--chain-artifact`. For a new local chain, `--master-wallet-file` is sufficient;
the public address and signer are derived unless explicitly overridden.

## Inventory and operations

The optional Textual editor provides structured editing without exposing secret
contents:

```bash
venv/bin/python -m pip install -r requirements-tui.txt
venv/bin/python deploy.py inventory-edit --inventory deployment-topology.json
```

The lifecycle commands are:

```bash
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py render --inventory deployment-topology.json
venv/bin/python deploy.py preflight --inventory deployment-topology.json
venv/bin/python deploy.py apply --inventory deployment-topology.json
venv/bin/python deploy.py status --inventory deployment-topology.json
venv/bin/python deploy.py verify --inventory deployment-topology.json
```

For remote inventories, `push` transfers public bundles and `apply` executes
them on declared hosts. `resume` continues an interrupted deployment. Rebuild
one service without restarting its group with:

```bash
venv/bin/python deploy.py recreate --inventory deployment-topology.json \
  --group apps --service dashboard --build
```

## Blockchain and storage

The internal Besu runtime is under `blockchain/`. Chain commands include
`chain-init`, `chain-static-nodes`, `chain-export`, and `chain-verify`. Do not
regenerate genesis or node identities for an existing network.

Store API returns a CID once Cluster accepts an add. The Minter requires one
real pin for each metadata level before publishing to Chain; final durability
replication and payload purging run afterwards as maintenance.

## Repository layout

```text
deploy.py                 # supported CLI
deployment_v2/            # inventory, rendering and execution
examples/deployment-v2/   # maintained inventory templates
blockchain/               # internal Besu runtime
components/               # component source repositories
docs/                     # architecture and operations
tests/                    # deployer tests
```

See [`OPERATIONS-MANUAL.md`](OPERATIONS-MANUAL.md) for the complete procedure
and [`docs/README.md`](docs/README.md) for the documentation map. Run tests with:

```bash
venv/bin/python -m unittest discover -s tests -v
```

The deployer never removes persistent data automatically. Review the inventory
and documented cleanup procedure before deleting containers, networks, or
volumes.

## License

Software: AGPL-3.0-or-later. Documentation: CC BY 4.0 unless noted otherwise.
