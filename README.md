# dARK Deployer

Declarative deployment tooling for the dARK blockchain, application services,
dashboard, and IPFS storage. The supported entry point is `deploy.py`; the
single source of deployment configuration is a version 3 inventory JSON.

## Start here

Choose the inventory that matches the deployment you are building:

| Goal | Recommended template | What it proves |
| --- | --- | --- |
| Fast local functional test | `operator-local-simple` | One storage peer and the end-to-end service path |
| Local replication simulation | `operator-local-ha` | Two peers and five logical groups on one Docker host |
| Standard production shape | `operator-production-five-host` | Five SSH hosts with explicit LAN/VPN routing |
| Dedicated public Resolver | `operator-production-six-host` | Six SSH hosts with separate Resolver and apps gateways |
| Non-standard service layout | `examples/deployment-v3/` | Direct control of the full v3 execution inventory |

Create, inspect, validate, plan, and install a compact inventory:

```bash
venv/bin/python deploy.py inventory-create \
  --template operator-local-simple \
  --output deployment-inventory.json
venv/bin/python deploy.py inventory-resolve \
  --inventory deployment-inventory.json \
  --output resolved-inventory.json
venv/bin/python deploy.py validate --inventory deployment-inventory.json
venv/bin/python deploy.py plan --inventory deployment-inventory.json --json
venv/bin/python deploy.py install --inventory deployment-inventory.json
```

The complete, scenario-based procedure is in
[OPERATIONS-MANUAL.md](OPERATIONS-MANUAL.md). Read it before applying an SSH
inventory or creating a new blockchain network.

## Architecture

The inventory describes hosts, named LAN/VPN networks, explicit inter-host
routes with an explicit protocol, central infrastructure port policies, Besu roles, storage peers,
component branches, secret distribution and the edge proxy. The standard
production layout is:

| Machine | Services |
| --- | --- |
| `apps` | non-validator RPC, Minter API, PostgreSQL, all Minter workers, Admin, Resolver, Store API, dashboard and edge proxy |
| `blockchain-a` | validators 01/02 and explorer |
| `blockchain-b` | validators 03/04 |
| `storage-1`, `storage-2` | one Kubo and one IPFS Cluster peer each |

Developer templates simulate these machines on one Docker host. Maintained
templates are in [`examples/deployment-v3/`](examples/deployment-v3/):
`local-simple.json`, `local-ha.json`, `production-five-host.json`, and
`production-six-host.json`.

Compact operator templates are in
[`examples/operator-inventory/`](examples/operator-inventory/). They describe
placement, storage, routing and proxy decisions and resolve to the complete v3
inventory before execution. See [`OPERATIONS-MANUAL.md`](OPERATIONS-MANUAL.md)
for the compact workflow and the Minter shoulder override.

## Requirements

- Python 3.10+ (3.12 recommended)
- Docker Engine and Compose v2 for local execution
- SSH and reachable private/VPN addresses for remote hosts
- Controller-side secret files declared by the inventory; `install` transfers each only to its declared consumer hosts

Never commit private keys or generated runtime files.

## Quick start

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python deploy.py inventory-create --template local-ha \
  --output deployment-inventory.json
venv/bin/python deploy.py validate --inventory deployment-inventory.json
venv/bin/python deploy.py plan --inventory deployment-inventory.json
venv/bin/python deploy.py install --inventory deployment-inventory.json
```

`install` acquires component repositories, records the resolved commits,
renders one Compose bundle per machine, transfers narrowly scoped secrets,
starts service instances in functional phases, and runs its final verification
before reporting success. Existing checkouts are updated with
`fetch` and `merge --ff-only`; divergent local changes stop safely. Use
`--skip-acquire` to reuse prepared checkouts.

For an existing chain, provision its private artifact and pass
`--chain-artifact`. For a new local chain, `--master-wallet-file` is sufficient;
the public address and signer are derived unless explicitly overridden.
To generate a new wallet automatically during a local installation, add
`--create-master-wallet`. The option is guarded and never overwrites an
existing `blockchain/master-wallet.txt`.

## Inventory and operations

The operator inventory is intentionally small: it declares machines,
group-to-machine placement, blockchain identity, storage peers and replication,
networks and routing, explicit proxy listeners/routes, secret sources, and typed overrides.
The catalogue derives service instances, connections, secret consumers, and
private endpoint exposure. Use `inventory-explain` to see why a resolved field
exists; use `inventory-diff` before applying a configuration change.

The optional Textual editor provides structured editing without exposing secret
contents:

```bash
venv/bin/python -m pip install -r requirements-tui.txt
venv/bin/python deploy.py inventory-edit --inventory deployment-inventory.json
```

See [explicit service placement](docs/deployment-v3-service-placement.md) for
the service graph, endpoint derivation, and the intentional Minter co-location
constraint.

## Documentation map

| Need | Document |
| --- | --- |
| Install, configure, recover, and verify | [Operations manual](OPERATIONS-MANUAL.md) |
| CLI, inventory formats, and safety model | [Deployer reference](docs/deployer.md) |
| v3 graph, endpoints, bundles, and artifacts | [Inventory and artifact flow](docs/deployment-v3-inventory-and-artifact-flow.md) |
| Compact-format rationale and catalogue design | [Operator inventory design](docs/deployment-v3-operator-inventory-proposal.md) |
| Future guided adaptation of an inventory | [Inventory adaptation wizard proposal](docs/inventory-adaptation-wizard-proposal.md) |
| Runtime architecture and ARK lifecycle | [Architecture](docs/architecture.md) |
| Lima five-host acceptance environment | [Lima guide](docs/lima-five-host-test.md) |
| AWS five-host deployment in one Availability Zone | [AWS single-zone guide](docs/aws-single-az-five-host.md) |
| Historical decisions and superseded procedures | [Historical material](docs/history.md) |

The lifecycle commands are:

```bash
venv/bin/python deploy.py validate --inventory deployment-inventory.json
venv/bin/python deploy.py render --inventory deployment-inventory.json
venv/bin/python deploy.py preflight --inventory deployment-inventory.json
venv/bin/python deploy.py apply --inventory deployment-inventory.json
venv/bin/python deploy.py status --inventory deployment-inventory.json
venv/bin/python deploy.py verify --inventory deployment-inventory.json
```

For remote inventories, `push` transfers public bundles and `apply` executes
them on declared hosts. `resume` revalidates uncertain readiness gates before
continuing. Rebuild
one service without restarting other services on its machine with:

```bash
venv/bin/python deploy.py recreate --inventory deployment-inventory.json \
  --service dashboard --build
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
deployment_v3/            # inventory, rendering and execution
examples/deployment-v3/   # maintained inventory templates
blockchain/               # internal Besu runtime
components/               # component source repositories
docs/                     # architecture and operations
tests/                    # deployer tests
```

See [`OPERATIONS-MANUAL.md`](OPERATIONS-MANUAL.md) for the complete procedure
and [`docs/history.md`](docs/history.md) for the historical archive. Run tests with:

```bash
venv/bin/python -m unittest discover -s tests -v
```

The deployer never removes persistent data automatically. Review the inventory
and documented cleanup procedure before deleting containers, networks, or
volumes.

## License

Software: AGPL-3.0-or-later. Documentation: CC BY 4.0 unless noted otherwise.
