# dark-deployer

`dark-deployer` installs, upgrades, verifies, and operates the complete dARK
platform: a private Besu/QBFT blockchain, APIs, workers, contracts, Dashboard,
Explorer, Resolver, and distributed Kubo/IPFS Cluster storage. One declarative
inventory drives everything: validate it, resolve it against a maintained
catalogue, plan phases, render per-machine Docker Compose bundles, preflight
hosts, push/apply, and verify the result.

The same rules apply whether the stack runs on one local Docker host or across
remote SSH-managed servers.

## 1. What is dARK?

dARK is a decentralized identifier and publication platform. An external
repository deposits an object and its bibliographic metadata through the APIs;
dARK allocates an **ARK identifier** under the authority that owns the NAAN,
stores canonical metadata and payload in a **private IPFS swarm**, confirms
real pins, publishes the immutable record on-chain, and exposes HTTP
resolution. The public ARK lifecycle stays stable (`reserved`, `draft`,
`update`, `published`, `tombstone`) while internal processing state is owned by
PostgreSQL and the workers.

The publication pipeline is `reserve → complete metadata → persist → publish →
resolve`:

- the **metadata worker** persists level-1 and level-2 metadata and their CIDs;
- the **replication worker** confirms one real IPFS pin for both levels;
- only then the **chain worker** submits the record to the blockchain;
- after publication, durability promotion requests the configured target
  replicas as maintenance work, and payloads are cleaned up once both levels
  meet the target.

Three layers make this up (details in
[docs/platform-architecture.md](docs/platform-architecture.md)):

```text
SERVICE LAYER     Admin API · Minter API · Resolver API · Store API · Dashboard
                  │  dark-core-lib (Python SDK: DARKCoreClient, services)
BLOCKCHAIN LAYER  Authority.sol (access control) ← IAuthority.sol → dARK.sol (ARK registry)
                  │  private Besu/QBFT network, chain ID 2025
STORAGE LAYER     Kubo + IPFS Cluster peers · Store API (store/retrieve/status/replication)
```

**Authority-centric model.** `Authority.sol` binds one wallet to one authority
UUID and controls which NAANs each authority may write; `dARK.sol` holds the
ARK registry (`create_ark`, `update_ark`, `resolve`). Components are acquired
from their own repositories into `components/` at deploy time:

| Component | Role | Checkout | Default container port |
| --- | --- | --- | --- |
| `dark-core-admin-api` | Authority and operational administration | `components/dark-core-admin-api` | 8000 |
| `dark-core-minter-api` | ARK reservation, metadata, publication + 3 workers | `components/dark-core-minter-api` | 8001 |
| `dark-core-resolver-api` | Public ARK resolution (`?info`, `?metadata`, redirect) | `components/dark-core-resolver-api` | 8002 |
| `dark-store-api` | Storage facade over Kubo/Cluster: store, retrieve, status, replication | `components/dark-store-api` | 8003 |
| `dark-dapp` | Solidity contracts (`Authority.sol`, `dARK.sol`) and contract tooling | `components/dark-dapp` | — |
| `dark-core-lib` | Python SDK shared by services, tests, and notebooks | `components/dark-core-lib` | — |
| `dashboard-web` | Operator UI (Laravel) + liveness hub | `components/dashboard-web` | 8080 |
| `dark-explorador` | Chain/block explorer UI | `components/dark-explorador` | 80 |
| Besu runtime | QBFT network, genesis and chain tooling | `blockchain/` (in this repo) | 8545 |
| Kubo + IPFS Cluster | Private swarm; Cluster owns the global pinset | `components/dark-ipfs` | 5001 / 9094 |
| PostgreSQL / MySQL | Minter metadata DB / dashboard DB | — | 5432 / 3306 |
| `dark-monitoring` | Prometheus, Blackbox, node-exporter, cAdvisor, Grafana stack | `components/dark-monitoring` | separate stack |
| `web-wizard/` | Local web workbench to design inventories | `web-wizard/` (in this repo) | 8765 on localhost |

Supporting services (`minter-postgres`, `dashboard-mysql`, `edge-proxy`) are
part of the rendered bundle. Every service's platform role is described in
[docs/architecture.md](docs/architecture.md); the on-chain contracts and the
SDK in [docs/platform-architecture.md](docs/platform-architecture.md) and
[docs/sdk-guide.md](docs/sdk-guide.md).

## 2. How the deployer works

The deployer turns one inventory file into running, verified machines:

```text
operator inventory (v2/v3)
      │ resolve   ← catalogue dark-platform-baseline-v1.0 + typed overrides
      ▼
   full v3 inventory ──► plan (phases) ──► render (per-machine bundles)
        ▼
preflight ──► prepare ──► push (SSH) ──► apply ──► verify
        └──────────── install coordinates the whole flow locally ────────────┘
```

**Inventories.** The *operator inventory* (format `dark-operator-inventory`,
v2 or v3) is the recommended way to describe an installation: identity,
machines and execution (`local`, `docker-lab`, `ssh`), networks and existing
routes, validator groups, RPC nodes with an explicit primary, observers, Kubo/
Cluster peers and replication policy, proxies with TLS and public routes,
placement, and typed overrides. The resolver expands it into the full
*v3 inventory* — the complete execution contract (every service, connection,
exposure, group, secret, component). You never edit the expanded output.
Examples: [`examples/operator-inventory/`](examples/operator-inventory/)
(compact) and [`examples/deployment-v3/`](examples/deployment-v3/) (full).

**Phases.** Services start in dependency order: `validators → rpc →
observers → contracts → storage → data → applications`, followed by the
`verify:deployment` step.

**Profiles.** `local` allows minimal topologies, `lab` allows shared placement
(warned: several instances on one host is not real HA), `production` requires
explicit availability objectives. Availability is analyzed per validator,
group, and machine; observers keep a synchronized chain copy but do not count
towards quorum.

**Bundles and revisions.** Since the per-machine bundle model, `prepare` builds
a machine-scoped bundle whose revision id is a SHA-256 of the resolved
inventory; `push` transfers it and `apply` consumes it. Each host keeps
`bundles/<revision-id>/` with a `current` pointer, and per-service fingerprints
in `status.json` make deployment verification compare against what actually
runs. `apply --revision` selects an older prepared revision.

**Components and sources.** Components are acquired into `components/` with
`fetch` + `merge --ff-only`; the catalogue pins `main` for every component and
an inventory may override the branch for testing (see
[docs/development.md](docs/development.md)). Divergent or locally modified
checkouts stop acquisition.

**Secrets and wallet.** Inventories contain secret *references*, never values.
Managed secrets are generated at install time, private keys are transferred
only to machines hosting their consumers, and the master wallet lives in
`blockchain/master-wallet.txt` (mode `600`). Public bundles contain neither
private keys nor secret hashes.

**Networking.** Dependencies on the same machine use Docker DNS without
published host ports; every cross-machine dependency explicitly selects LAN or
VPN. `routes` declares connectivity that already exists — the deployer does not
create routers, VPNs, or cloud rules; it renders an auditable
`firewall-suggestion.json` instead. An `edge-proxy` is the normal public entry
point, and proxy routes decide what is published.

## 3. Quick start: dARK on one machine (`local-ha`)

`local-ha` keeps all services on one Docker host while exercising the
high-availability authoring shape: two validator groups (`blockchain-a`,
`blockchain-b`, 2 validators each), one primary RPC (`rpc01`), two Kubo/Cluster
peers (`storage-a`, `storage-b`) with `publish_after_replicas: 1` and
`target_replicas: 2`, and a loopback HTTP gateway. The deployer warns that the
replicas share one host: this scenario validates logical replication and
dynamic validator groups, not host-failure tolerance.

**Requirements:** Python 3.14, Docker Engine and Docker Compose v2.

**1 — Set up the tool.** `./deploy.sh` is the supported entry point: it runs
`deploy.py` with the repository venv, creating it on first use (and repairing
it when requirements change). It needs CPython 3.14; point `DARK_PYTHON` at one
if `python3.14` is not on `PATH`.

```bash
./deploy.sh                                   # prints the CLI help
docker version && docker compose version      # prerequisites
```

**2 — Inspect before you run.** All of these are read-only: they do not touch
Docker, SSH, Git, or secrets.

```bash
./deploy.sh validate --inventory examples/operator-inventory/local-ha.json
./deploy.sh plan     --inventory examples/operator-inventory/local-ha.json
./deploy.sh inventory-explain --inventory examples/operator-inventory/local-ha.json \
  --path /services/resolver-api/connections/rpc
```

**3 — Install.** On the first run let the installer create the guarded master
wallet and the managed secrets interactively (it reports every path it creates;
wallet credentials land in `blockchain/master-wallet.txt`, generated material
under `.generated/deployment-v3/<deployment>/controller/` — controller state,
never committed):

```bash
./deploy.sh install \
  --inventory examples/operator-inventory/local-ha.json \
  --create-master-wallet \
  --verbose
```

`install` validates, resolves the catalogue, runs preflight, acquires or reuses
components, prepares managed inputs, renders, starts services by phase, and
runs final verification. It never silently replaces an existing wallet, and it
never deletes volumes or persistent data.

**4 — Verify and explore.** After `verify` finishes:

```bash
./deploy.sh verify --inventory examples/operator-inventory/local-ha.json
./deploy.sh status --inventory examples/operator-inventory/local-ha.json
./deploy.sh services --deployment dark-operator-local-ha
```

The `local-ha` gateway publishes on `http://localhost` (loopback): the
dashboard at `/admin/`, the explorer at `/explorer/`, the Minter API under
`/api/v1/` with OpenAPI docs at `/api/docs`.

**5 — Operate and observe.**

```bash
./deploy.sh tui       # interactive operations console (lifecycle + log streams)
./deploy.sh metrics   # read-only Docker panel; safe to leave open
```

**6 — Accept the scenario end to end.** Run the
[local-ha acceptance notebook](notebooks/scenarios/local-ha-deposit-lifecycle.ipynb)
to exercise a complete deposit through the gateway and inspect private storage
replication. The [notebook index](notebooks/README.md) lists the other
scenarios' acceptance notebooks.

Creating your own inventory instead of using the example:

```bash
./deploy.sh inventory-create --template operator-local-ha --output inventory.json
./web-wizard/run.sh --inventory inventory.json   # design it in the browser
```

The web wizard runs a token-protected local server, validates every operation
against the real resolver, and never overwrites the opened inventory (saving
creates a new file). See [web-wizard/README.md](web-wizard/README.md).

## 4. Operating a deployment

**Lifecycle.** `deploy.py deployments` lists known managed deployments;
`deploy.py services --deployment ID` lists exact managed `service:ID`
selectors, runtime state, and valid actions. `build`, `stop`, `start`,
`restart`, `recreate`, `remove`, and `logs` act on one `service:ID` at a time —
locally or through the machine's configured SSH host. `remove` never deletes
volumes, persistent data, or networks; `build` does not restart the container
(use `recreate --build`); start lifecycle operations with `--dry-run`.

```bash
./deploy.sh recreate --deployment dark-operator-local-ha \
  --target service:store-api --build
./deploy.sh logs --deployment dark-operator-local-ha --target service:minter-api --tail 100
# Runtime-only: does not restart Besu and is reset by its next restart.
./deploy.sh log-level --deployment dark-operator-local-ha \
  --target service:rpc01 --level DEBUG
```

**Metrics and monitoring.** `metrics` samples each machine once a minute
through its own Docker daemon (CPU, memory, container and restart state) and
changes nothing. `metrics-export` writes the same collector contract as a
Prometheus textfile:

```bash
./deploy.sh metrics-export \
  --deployment dark-operator-local-ha \
  --include-disk \
  --output components/dark-monitoring/generated/dark.prom
```

`components/dark-monitoring` runs the separate Prometheus/Blackbox/
node-exporter/cAdvisor/Grafana stack; its installer derives targets from the
applied deployment snapshot, not a hand-written host list:

```bash
cd components/dark-monitoring
../../venv/bin/python install.py \
  --deployment-snapshot ../../.generated/deployment-v3/dark-operator-local-ha/bundle/shared/deployment-topology.json
```

The first run creates a private `.env` and asks you to replace the sample
Grafana administrator password before starting anything.

**Remote deployments.** The explicit sequence is:

```bash
./deploy.sh validate --inventory inventory.json
./deploy.sh plan     --inventory inventory.json
./deploy.sh preflight --inventory inventory.json
./deploy.sh prepare  --inventory inventory.json
./deploy.sh push     --inventory inventory.json
./deploy.sh apply    --inventory inventory.json
./deploy.sh verify   --inventory inventory.json
```

`push` transfers public bundles over SSH; `apply` validates host roles, paths,
artifacts, and capabilities before starting phases. `install` can coordinate
the complete flow (local or remote) in one command. After fixing the cause of
an interrupted execution, `resume` reuses the wallet, generated secrets, chain
artifact, source evidence, and prepared revision recorded for that deployment
— it never silently creates a different chain identity.

For AWS, provision with [CloudFormation
(`dark2-prod-aws`)](infrastructure/aws/cloudformation/README.md), then run the
controller from the `apps` host once `check-bootstrap.sh` passes on every host;
the inventory uses the private addresses reachable from that host. The
[Lima lab](docs/lima-five-host-test.md) reproduces a five-host SSH
distribution locally.

**Blockchain artifacts.** `chain-init`, `chain-static-nodes`, `chain-export`,
`chain-verify`, and `chain-bootstrap` create, export, and verify Besu
artifacts. Do not regenerate genesis, identities, or node keys for an existing
chain; group exports give each machine only its own nodes' keys.

**Storage and publication.** Every storage peer is a one-to-one Kubo + Cluster
pair, and the replication policy must satisfy
`1 <= publish_after_replicas <= target_replicas <= number of peers`. Store API
returns a CID when Cluster accepts the payload; the Minter requires confirmed
pins for metadata levels before publishing to the chain.

**Recovery.** `resume` continues an interrupted run. When an empty Docker
bridge network overlaps a requested subnet, `install`, `apply`, and `resume`
remove it only with `--clean-empty-network-conflicts`; a network with attached
containers is never removed automatically. `recreate` rebuilds one service
without applying inventory changes — altered ports, connections, placement, or
secrets require a reviewed `apply`.

**Command index.** The CLI exposes 36 subcommands, grouped by effect:

| Group | Commands | Side effects |
| --- | --- | --- |
| Inspect an inventory | `validate`, `plan`, `inventory-resolve`, `inventory-explain`, `inventory-network-matrix`, `inventory-diff` | Read-only except `inventory-resolve`, which writes its `--output` JSON; no Docker/SSH/Git, no secret values |
| Deploy | `render`, `install`, `preflight`, `prepare`, `push`, `apply`, `resume`, `verify`, `status` | `render` writes a bundle; the rest install, transfer, or start services (`verify`/`status` read-only) |
| Managed services | `services`, `logs`, `build`, `stop`, `start`, `restart`, `recreate`, `remove`, `log-level` | Act on one `service:ID` (take `--deployment` or `--inventory`); `log-level` is available only for services that report the runtime capability |
| Observe | `deployments`, `tui`, `metrics`, `metrics-export` | Read-only; `metrics-export` writes its `--output` `.prom` file; `tui` mutates only through explicit lifecycle actions |
| Inventories | `inventory-create`, `inventory-edit` | Create or edit a file after confirmation |
| Chain artifacts | `chain-bootstrap`, `chain-init`, `chain-static-nodes`, `chain-export`, `chain-verify` | Some create artifacts explicitly |
| Secrets | `secrets-init` | Creates managed private material at the requested destination |

## 5. Maintained scenarios

Every pair `.json`/`.md` under [`examples/operator-inventory/`](examples/operator-inventory/)
loads and renders with the current code. Production examples use
documentation-reserved ranges and `REPLACE` placeholders — never run them
until addresses, routes, SSH keys, public origins, artifacts, and secret
sources are replaced.

| Operator file | Profile | Machines | What it exercises |
| --- | --- | ---: | --- |
| [`local-simple.json`](examples/operator-inventory/local-simple.md) | `local` | 1 | Minimal functional path: one validator, RPC, and storage peer |
| [`local-observer.json`](examples/operator-inventory/local-observer.md) | `local` | 1 | A Besu observer and Resolver bound to its private RPC |
| [`local-ha.json`](examples/operator-inventory/local-ha.md) | `lab` | 1 | Logical HA groups and two storage copies on one Docker host |
| [`local-ha-monitoring.json`](examples/operator-inventory/local-ha-monitoring.md) | `lab` | 1 | local-ha plus the dark-monitoring stack for the deployment |
| [`local-two-site.json`](examples/operator-inventory/local-two-site.md) | `lab` | 6 logical | Two isolated Docker LANs plus an inter-site VPN mesh (network-behaviour lab, not physical HA) |
| [`lima-five-host.json`](examples/operator-inventory/lima-five-host.md) | `production` | 5 | Reproducible SSH distribution across Lima machines |
| [`lima-two-site-five-host.json`](examples/operator-inventory/lima-two-site-five-host.md) | `lab` | 5 | Two Lima sites joined through WireGuard: Apps + 2 validators + IPFS in site A, 2 validators + IPFS in site B |
| [`production-five-host.json`](examples/operator-inventory/production-five-host.md) | `production` | 5 | Apps, two blockchain domains, and two storage domains |
| [`production-six-host.json`](examples/operator-inventory/production-six-host.md) | `production` | 6 | Dedicated public Resolver with local observer, RPC, and Store API reader |
| [`one-server-aws-sandbox.json`](examples/operator-inventory/one-server-aws-sandbox.md) | `lab` | 1 | Single-EC2 sandbox run on the server, local-ha layout with public HTTP gateway |
| [`dark2-prod-aws.json`](examples/operator-inventory/dark2-prod-aws.md) | `production` | 6 | AWS-only active site: applications, Resolver/observer, five validators, two storage peers |
| [`aws-active-two-site-nine-host.json`](examples/operator-inventory/aws-active-two-site-nine-host.md) | `production` | 9 | AWS retains QBFT quorum; the remote site keeps chain and IPFS copies without application services |

## 6. Documentation map

**Understand the platform**

| Document | Depth |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Services, minter pipeline, state model, IPFS layout, chain, health endpoints |
| [docs/platform-architecture.md](docs/platform-architecture.md) | Authority-centric design: contracts, service layer, ports, data flows |
| [docs/sdk-guide.md](docs/sdk-guide.md) | Developer guide: `dark-core-lib` SDK, contract deployment, signer roles |
| [docs/api-reference.md](docs/api-reference.md) | HTTP API of every service, mTLS posture, on-chain reference |
| [notebooks/README.md](notebooks/README.md) | End-to-end notebooks: authority→resolver, deposit lifecycle per scenario |

**Go deeper on the deployer**

| Document | Depth |
| --- | --- |
| [docs/deployer.md](docs/deployer.md) | The deployer reference: formats, acquisition, rendering, execution, security |
| [docs/deployment-v3-inventory-and-artifact-flow.md](docs/deployment-v3-inventory-and-artifact-flow.md) | Resolution, bundles, secrets, blockchain artifacts |
| [docs/deployment-v3-service-placement.md](docs/deployment-v3-service-placement.md) | Groups, connections, placement constraints |
| [docs/networking-v3.md](docs/networking-v3.md) | LAN/VPN, routes, NAT, P2P ports, firewalling |
| [docs/multi-site-lan-vpn-proposal.md](docs/multi-site-lan-vpn-proposal.md) | Multi-site design (format v3) and its acceptance criteria |
| [docs/deployment-v3-revisions.md](docs/deployment-v3-revisions.md) | Bundle/revision reference: prepare, push, apply, fingerprints, status |
| [docs/operations.md](docs/operations.md) | Operational runbook: install, verification, workers, lifecycle |

**Scenarios and labs**

| Document | Depth |
| --- | --- |
| [docs/lima-five-host-test.md](docs/lima-five-host-test.md) | Five-host Lima lab procedure |
| [docs/aws-single-az-five-host.md](docs/aws-single-az-five-host.md) | Manual five-host EC2 runbook |
| [CloudFormation guide](infrastructure/aws/cloudformation/README.md) | `dark2-prod-aws`: isolated VPC, six hosts, EBS, ALB, TLS, bootstrap seal, SSM helpers |
| [AWS operator CLI](infrastructure/aws/cloudformation/dark2-prod-aws-operator-cli.md) | Operator profile, PEM upload, Dashboard forwarding, bootstrap check |
| [local-infra/README.md](local-infra/README.md) | Lima lab scripts and two-site generator |

**Development and design**

| Document | Depth |
| --- | --- |
| [docs/development.md](docs/development.md) | Test component branches without touching the catalogue; pre-PR validation |
| [docs/deployer-generalization.md](docs/deployer-generalization.md) | Active design: generalizing the deployer beyond the baseline catalogue |
| [docs/deployer-component-contract-v0.md](docs/deployer-component-contract-v0.md) | Companion spec: component definition, bindings, hooks |
| [web-wizard/README.md](web-wizard/README.md) | Inventory workbench usage and guarantees |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Environment, tests, linting, contribution flow |

**Reference**

| Document | Depth |
| --- | --- |
| [docs/README.md](docs/README.md) | Documentation index: the canonical map of every maintained document |
| [docs/monitoring.md](docs/monitoring.md) | Monitoring decision record and operating guide |
| [docs/known-issues.md](docs/known-issues.md) | Single living list of unresolved issues and pending acceptance work |

Archived proposals, diagnoses, and historical reviews live in
[`docs/old/`](docs/old/README-index.md) — valuable context, not current
instructions.

## 7. Repository layout

```text
deploy.py / deploy.sh           CLI and its self-maintaining launcher
create_venv.sh                  standalone venv creation (--with-tui adds Textual)
deployment_v3/                  contracts, resolver, planner, renderer, runner, metrics
examples/operator-inventory/    maintained compact inventories + per-scenario guides
examples/deployment-v3/         maintained complete inventories (legacy format)
web-wizard/                     local web workbench for operator inventories
blockchain/                     Besu runtime, genesis tooling, chain scripts
components/                     dARK component checkouts (acquired from their repos)
local-infra/                    Lima lab scripts and inventory instantiation
infrastructure/aws/             CloudFormation templates, parameters, SSM helpers
notebooks/                      end-to-end and per-scenario acceptance notebooks
docs/                           technical documentation (docs/old/ = archive)
tests/                          deployer tests (unittest)
```

## 8. Tests

Deployer:

```bash
venv/bin/python -m unittest discover -s tests -v
```

Web wizard:

```bash
PYTHONPATH=web-wizard:$PYTHONPATH \
  venv/bin/python -m pytest web-wizard/tests -q
```

## 9. License

Software: AGPL-3.0-or-later. Documentation: CC BY 4.0 unless stated otherwise.
