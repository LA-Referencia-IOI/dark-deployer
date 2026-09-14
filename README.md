# dARK Deployer

`dark-deployer` installs, upgrades, verifies, and operates a complete dARK
platform: a private Besu/QBFT network, APIs, workers, contracts, Dashboard,
Explorer, Resolver, and distributed Kubo/IPFS Cluster storage.

The supported entry point is `deploy.py`. It receives a declarative inventory,
validates it, builds a per-machine and per-phase plan, prepares components and
artifacts, produces one Docker Compose bundle for each host, and verifies the
result. The inventory is the source of truth; generated Compose files must not
be edited manually.

The deployer can run the complete stack on one local Docker host or distribute
groups across remote SSH-managed servers. The same inventory, networking,
secret, planning, and verification rules apply in both cases.

## Included tools

| Tool | Purpose | Changes infrastructure |
| --- | --- | --- |
| `deploy.py` | Validate, plan, render, install, and operate dARK | Only its operational commands |
| Operator inventory v2 | Describe a standard installation compactly | No |
| Complete v3 inventory | Describe the full execution contract directly | No |
| `inventory-edit` | Edit inventory sections through a Textual TUI | Only the file after confirmation |
| `web-wizard/` | Design, visualize, and adapt operator inventories through a local web interface | Only creates a new copy |
| `chain-*` commands | Create, export, and verify Besu artifacts | Some explicitly create artifacts |
| `secrets-init` | Prepare managed secrets for a new installation | Yes, at the requested destination |

The `web-wizard` never runs Docker, SSH, Git, or deployment operations, and it
does not read secret values. It uses the deployer's resolver and planner only to
validate and explain the inventory being designed.

## Requirements

- Python 3.10 or newer; Python 3.12 is recommended.
- Docker Engine and Docker Compose v2 for local execution.
- SSH access from the controller for remote deployments.
- Reachable LAN/VPN addresses as declared by the inventory.
- Private material referenced by the inventory, stored outside Git.

Basic setup:

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
docker version
docker compose version
```

To use `inventory-edit`:

```bash
venv/bin/python -m pip install -r requirements-tui.txt
```

## The two inventory formats

### Operator inventory v2

This is the recommended format for new installations that follow the
`dark-platform-baseline-v1.0` catalogue. It describes the decisions that usually differ
between installations:

- identity and profile;
- local or SSH machines;
- LAN/VPN networks and existing routes;
- validator and observer groups;
- RPC nodes and an explicit primary RPC;
- placement of groups on machines;
- Kubo/IPFS Cluster peers and replication policy;
- proxies, TLS, public hosts, and routes;
- artifact and secret references;
- typed component, setting, and advertised-endpoint overrides.

The resolver expands these decisions into a complete v3 inventory before
validation, planning, rendering, or execution. That expanded output does not
need to be saved in order to install.

Examples live in [examples/operator-inventory/](examples/operator-inventory/).

### Complete v3 inventory

This is the expanded execution contract: it contains every service, connection,
exposure, group, secret, component, and infrastructure policy. Use it to review
the effective result or maintain an architecture deliberately outside the
operator catalogue.

Examples live in [examples/deployment-v3/](examples/deployment-v3/).

Neither format contains secret values; they contain secret references only.

## Profiles

A profile sets validation policy and warnings; it never changes the
architecture implicitly.

| Profile | Use | Expected behavior |
| --- | --- | --- |
| `local` | Development and functional testing on one Docker host | Allows minimal topologies with no fault tolerance |
| `lab` | Replication, role, and distributed-scenario testing | Allows shared placement and warns that multiple instances on one host are not real HA |
| `production` | Real servers | Requires explicit availability objectives or typed acknowledgements |

Availability is analyzed per validator, group, and machine. Observers keep an
additional synchronized chain copy, but they do not count towards quorum.

## Maintained scenarios

| Operator file | Profile | Machines | What it exercises |
| --- | --- | ---: | --- |
| `local-simple.json` | `local` | 1 | Minimal functional path with one validator, RPC, and storage peer |
| `local-observer.json` | `local` | 1 | A Besu observer and Resolver bound to its private RPC |
| `local-ha.json` | `lab` | 1 | Logical HA groups and two storage copies on one Docker host |
| `lima-five-host.json` | `lab` | 5 | Reproducible SSH distribution across Lima machines |
| `production-five-host.json` | `production` | 5 | Apps, two blockchain domains, and two storage domains |
| `production-six-host.json` | `production` | 6 | Dedicated public Resolver with a local observer and RPC |

Production examples use documentation-reserved ranges and `REPLACE` values.
Never run them until addresses, routes, SSH keys, public origins, artifacts, and
secret sources have been replaced.

`local-ha` demonstrates replication and logical separation, but every instance
depends on the same Docker daemon: it does not tolerate physical host loss.

## Local quick start

To run the maintained local HA scenario directly:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json \
  --verbose
```

`install` validates the inventory, resolves the catalogue, runs preflight,
acquires or reuses components, prepares managed inputs, renders, starts services
by phase, and runs final verification.

For a new local chain, it can generate a guarded master wallet:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json \
  --create-master-wallet \
  --verbose
```

The generator never silently replaces `blockchain/master-wallet.txt`. If it
already exists, the installer asks whether it should be reused.

After `verify`, run the [local-ha acceptance notebook](notebooks/scenarios/local-ha-deposit-lifecycle.ipynb)
to exercise a complete deposit through the gateway and inspect the resulting
private storage replication. The [notebook index](notebooks/README.md) explains
the distinction between scenario acceptance and the generic API notebooks.

For the distributed Lima lab, use the [Lima five-host acceptance notebook](notebooks/scenarios/lima-five-host-deposit-lifecycle.ipynb) after its own `verify`.

## Create and inspect an inventory

Creating an editable template copy is optional:

```bash
venv/bin/python deploy.py inventory-create \
  --template operator-production-six-host \
  --output inventory.json
```

Edit it with Textual:

```bash
venv/bin/python deploy.py inventory-edit --inventory inventory.json
```

Safe inspection sequence:

```bash
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json --json

venv/bin/python deploy.py inventory-resolve \
  --inventory inventory.json \
  --output resolved-inventory.json

venv/bin/python deploy.py inventory-explain \
  --inventory inventory.json \
  --path /services/resolver-api/connections/rpc
```

`validate`, `plan`, `inventory-resolve`, `inventory-explain`, and
`inventory-diff` are inspection operations: they do not contact Docker, SSH, or
Git, and they do not read secret contents. `inventory-resolve` refuses to
overwrite its output file.

To compare a revision with a previous one:

```bash
venv/bin/python deploy.py inventory-diff \
  --before previous-inventory.json \
  --after inventory.json
```

## Web wizard

The web workbench shows machines, groups, services, networks, connections, and
failure domains. It can move groups, change cardinalities, configure RPC,
storage, proxies, and routing, and validates every operation against the real
resolver.

Run it from the repository root:

```bash
./web-wizard/run.sh \
  --inventory examples/operator-inventory/production-six-host.json
```

The launcher creates its own `web-wizard/.venv`, installs only its own
dependencies, and prints a local token-protected URL. The opened inventory is
never overwritten: saving creates a new file with exclusive creation.

See [web-wizard/README.md](web-wizard/README.md) for the complete guide.

## Recommended operational sequence

### Coordinated local installation

```bash
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json
venv/bin/python deploy.py install --inventory inventory.json --verbose
venv/bin/python deploy.py status --inventory inventory.json
venv/bin/python deploy.py verify --inventory inventory.json
```

### Explicit remote installation

```bash
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json
venv/bin/python deploy.py preflight --inventory inventory.json
venv/bin/python deploy.py push --inventory inventory.json
venv/bin/python deploy.py apply --inventory inventory.json
venv/bin/python deploy.py verify --inventory inventory.json
```

`push` transfers public bundles over SSH. `apply` validates host roles, paths,
artifacts, and host capabilities before starting phases. Secrets are transferred
separately and only to the machines hosting their consumers.

`install` can also coordinate the complete remote flow:

```bash
venv/bin/python deploy.py install \
  --inventory inventory.json \
  --verbose
```

After fixing the cause of an interrupted execution:

```bash
venv/bin/python deploy.py resume --inventory inventory.json
```

To rebuild one service without restarting all services on its machine:

```bash
venv/bin/python deploy.py recreate \
  --inventory inventory.json \
  --service dashboard \
  --build
```

## Networking and exposure

Dependencies on the same machine use Docker DNS and do not publish host ports.
Every cross-machine dependency explicitly selects LAN or VPN and derives a
private provider exposure.

`routes` declares directional connectivity that already exists at the site; the
deployer does not create routers, VPNs, cloud rules, or firewalls. Rendering
produces an auditable firewall suggestion containing API/web endpoints and Besu,
Kubo, and Cluster P2P ports.

An `edge-proxy` is the normal public entry point. Proxy routes—not the mere
existence of a backend—decide which services are published. The six-machine
example exposes Resolver API at `/`, while `resolver-api` consumes the local
`observer01` RPC inside Docker.

When an empty Docker bridge network overlaps the requested subnet, `install`,
`apply`, and `resume` can remove it only with
`--clean-empty-network-conflicts`. A network with attached containers is never
removed automatically.

## Blockchain, RPC, and observers

Each blockchain group contains one or more validators. An inventory can declare
multiple groups, multiple RPCs, and an explicit primary RPC. All Besu nodes join
P2P and receive independent persistent storage.

An observer:

- retains a synchronized copy of the chain;
- does not participate in the QBFT validator set;
- has RPC enabled for explicitly bound private consumers;
- is not automatically published on the host;
- can reside with Resolver so Resolver API uses its local RPC.

Artifact commands include:

```bash
venv/bin/python deploy.py chain-init --help
venv/bin/python deploy.py chain-static-nodes --help
venv/bin/python deploy.py chain-export --help
venv/bin/python deploy.py chain-verify --help
```

Do not regenerate genesis, identities, or node keys for an existing chain.
Group exports provide each machine with only genesis, static nodes, and the
private keys for its own nodes.

## Storage and publication

Every storage peer creates a one-to-one pair:

```text
Kubo + IPFS Cluster
```

The replication policy must satisfy:

```text
1 <= publish_after_replicas <= target_replicas <= number of peers
```

Store API returns a CID when Cluster accepts the payload. Minter requires real
pins for metadata levels before publishing to blockchain. Final replication and
payload cleanup are later worker activity, not part of a client HTTP request.

## Security and persistence

- Inventories contain secret paths and consumers, not values.
- Public bundles contain neither private keys nor secret hashes.
- Existing components are updated with `fetch` and `merge --ff-only`; divergent
  or modified checkouts stop acquisition.
- Installation never automatically deletes volumes or persistent data.
- Blockchain artifacts are verified against nodes, roles, groups, chain ID,
  endpoints, and checksums in the current inventory.
- Review `plan`, exposures, and firewall suggestions before any remote install.

## Main commands

| Command | Function |
| --- | --- |
| `validate` | Validate an operator or v3 inventory |
| `plan` | Show machines, groups, phases, and dependencies |
| `render` | Generate bundles without applying them |
| `preflight` | Check local/remote prerequisites |
| `push` | Transfer bundles to SSH hosts |
| `apply` | Apply a prepared bundle |
| `install` | Coordinate acquisition, preparation, application, and verification |
| `resume` | Continue an interrupted execution |
| `status` | Show a concise operational status |
| `verify` | Run functional and infrastructure verification |
| `recreate` | Rebuild/recreate one specific service |
| `inventory-create` | Create a copy from a maintained template |
| `inventory-edit` | Edit an inventory with Textual |
| `inventory-resolve` | Expand operator v2 to v3 |
| `inventory-explain` | Explain the provenance of a resolved field |
| `inventory-diff` | Compare two resolved inventories semantically |
| `chain-*` | Create, export, and verify Besu artifacts |
| `secrets-init` | Initialize managed private material |

Check command-specific help before using operational options:

```bash
venv/bin/python deploy.py install --help
venv/bin/python deploy.py apply --help
venv/bin/python deploy.py chain-init --help
```

## Essential documentation

| Document | Contents |
| --- | --- |
| [Operations manual](OPERATIONS-MANUAL.md) | Installation, configuration, verification, recovery, and troubleshooting |
| [Deployer reference](docs/deployer.md) | Contracts, commands, security, rendering, and execution |
| [Operator inventory](docs/deployment-v3-operator-inventory-proposal.md) | Compact format and catalogue design |
| [Inventory and artifact flow](docs/deployment-v3-inventory-and-artifact-flow.md) | Resolution, bundles, secrets, and blockchain artifacts |
| [Service placement](docs/deployment-v3-service-placement.md) | Groups, connections, endpoints, and placement constraints |
| [Networking](docs/networking-v3.md) | LAN, VPN, routes, NAT, ports, and firewalling |
| [Architecture](docs/architecture.md) | dARK components and ARK lifecycle |
| [Lima testing](docs/lima-five-host-test.md) | Five-host distributed lab |
| [AWS deployment](docs/aws-single-az-five-host.md) | Private EC2 scenario in one Availability Zone |
| [Web wizard](web-wizard/README.md) | Local workbench usage and guarantees |
| [History](docs/history.md) | Archived decisions and superseded documentation |

## Repository layout

```text
deploy.py                       supported CLI
deployment_v3/                 contracts, resolver, planner, rendering, and runtime
examples/operator-inventory/   maintained compact inventories
examples/deployment-v3/        maintained complete inventories
web-wizard/                    local web workbench for operator inventories
blockchain/                    Besu runtime and tools
components/                    dARK component checkouts
docs/                          technical documentation and scenarios
tests/                         deployer tests
```

## Tests

Deployer:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Web wizard:

```bash
PYTHONPATH=web-wizard:$PYTHONPATH \
  venv/bin/python -m pytest web-wizard/tests -q
```

## License

Software: AGPL-3.0-or-later. Documentation: CC BY 4.0 unless stated otherwise.
