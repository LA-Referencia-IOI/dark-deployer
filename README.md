# dARK Deployer

Installer and operational tooling for the dARK blockchain, application and
global IPFS storage infrastructure.

## Architecture

The storage model has one global Cluster and any number of storage nodes:

```text
one global CRDT IPFS Cluster
├── storage nodes: Kubo + Cluster peer
└── application tiers: Store API + Minter + Resolver
```

Each Store API uses a generated endpoint pool selected from the shared
topology. Cluster controls global replication; Store APIs do not replicate to
or control one another. See [IPFS architecture](docs/ipfs-architecture.md) for the
failure model and design rationale.

For a single technical entry point covering the complete system, APIs, site
layout, deployment and operations, see the
[dARK technical reference](docs/dark-technical-reference.md).

For high-volume publication, see
[publication latency control](docs/publication-latency-control.md).

The `developer` profile has local-only modes: one lightweight peer or two
independent peers on the same machine as Store API. The mode is a generated
runtime preset, not a manually maintained topology.

## Requirements

- Python 3.10 or newer
- Docker Engine and Docker Compose v2
- VPN reachability between storage servers
- one Kubo private-swarm key shared by all storage nodes
- one separate 32-byte hexadecimal Cluster secret shared by all storage nodes

Administrative APIs (`5001`, `9094`, `9095`) must remain private. Kubo swarm
`4001/tcp+udp` and Cluster swarm `9096/tcp+udp` must be reachable between peers.

## Configure

For a single-machine developer installation, copy only the environment example
and run the wizard:

```bash
cp deployment-topology.example.json deployment-topology.json
venv/bin/python deploy.py install --inventory deployment-topology.json
```

The wizard offers two storage modes:

- `Simple`: one local Kubo + Cluster peer.
- `HA simulation`: two independent local peers.

Both modes share one private-swarm key and one Cluster secret under
`.dark-secrets/developer/`. Runtime configuration is regenerated under
`.generated/storage/`; secrets, identities and volumes are never removed.
HA simulation uses
separate containers, identities, networks and volumes, but both peers still
share the same physical Docker host.

All host, blockchain and storage placement is defined in the single
`deployment-topology.json` (copy `deployment-topology.example.json` to start).
The same file drives developer simulation and production rendering; only the
SSH and VPN values differ. It lists storage nodes and the replication policy,
not geographical constraints:

```json
{
  "version": 3,
  "cluster_name": "dark-global",
  "replication": {
    "publish_after_replicas": 1,
    "target_replicas": 2
  },
  "nodes": [
    {"id": "storage-a", "address": "10.200.1.11"},
    {"id": "storage-b", "address": "10.200.1.12"}
  ],
  "access_groups": {"apps-a": ["storage-a", "storage-b"]}
}
```

The live file is excluded from Git. Copy the same topology to every host. In
`.env`, select the active profile and point `DEPLOYMENT_TOPOLOGY_FILE` at that
file. `.env.example` is the versioned template and `.env` must keep the same
keys.

Generate two different secrets outside the repository:

```bash
umask 077
{
  echo /key/swarm/psk/1.0.0/
  echo /base16/
  openssl rand -hex 32
} > /run/dark-secrets/ipfs-swarm.key
openssl rand -hex 32 > /run/dark-secrets/ipfs-cluster-secret
chmod 600 /run/dark-secrets/ipfs-swarm.key /run/dark-secrets/ipfs-cluster-secret
```

Then select the active profile and configure its role and storage selector:

```ini
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=storage-node
PRODUCTION_DEPLOYMENT_TOPOLOGY_FILE=.generated/deployment-topology.json
PRODUCTION_STORAGE_NODE_ID=storage-a
PRODUCTION_IPFS_SWARM_KEY_FILE=/run/dark-secrets/ipfs-swarm.key
PRODUCTION_IPFS_CLUSTER_SECRET_FILE=/run/dark-secrets/ipfs-cluster-secret
```

For an apps host, use `PRODUCTION_INSTALL_COMPONENTS=apps`, omit the node and
storage secret fields, and set `PRODUCTION_STORAGE_ACCESS_GROUP=apps-a`.
The installer derives its Store API failover endpoints from that group.

For a complete production topology with one apps/RPC server, two validator
servers and two IPFS servers on one VPN, see
[Production five-server installation](docs/production-single-site-four-server-installation.md).

For a reproducible local lab that simulates those four Linux servers in
Docker, see [local-infra/README.md](local-infra/README.md).

The complete documentation map, including the distinction between current
guides and historical analyses, is in [docs/README.md](docs/README.md).
For code-based evidence of the Metadata → Replication → Chain cycle, timing,
state transitions and Store API pin semantics, see [Worker cycle evidence](docs/worker-cycle-evidence.md).

Production uses `deployment-topology.json` as its canonical configuration.
It defines host addresses, SSH references, component branches and storage
policy; `.env` is rendered per host and is not a source of production versions.
Start from `deployment-topology.example.json` and render the public host bundles:

```bash
cp deployment-topology.example.json deployment-topology.json
# Set addresses and secret target paths in the topology.
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py render --inventory deployment-topology.json \
  --output .generated/deployment-v2/dark-site-a-1
venv/bin/python deploy.py verify --inventory deployment-topology.json
```

The renderer creates one shared topology, endpoint and firewall policy plus one
host configuration per server. The bundle contains secret paths but never
 secret contents. See [the production installation runbook](docs/production-single-site-four-server-installation.md).

## Roles

| Value | Installed on this server |
| --- | --- |
| `storage-node` | one Kubo daemon and one Cluster peer |
| `apps` | RPC node, contracts, core library, Admin API, Store API, Minter, Resolver and dashboard |
| `blockchain-a` | validators 01/02 and explorer |
| `blockchain-b` | validators 03/04 |
| `all` | local developer simulation of every role |

There is no legacy `storage` role and no single-host three-peer IPFS mode.
Store API always belongs to `apps`, never to a storage node.

## Install

Validate before changing the host:

```bash
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py plan --inventory deployment-topology.json
venv/bin/python deploy.py install --inventory deployment-topology.json
```

The interactive wizard selects only the profile and role. It does not collect
secrets or topology addresses. Missing fields are reported before repositories,
containers or networks are changed.

If an installation stops after completing earlier stages, resume from the
first stage that did not finish instead of rerunning blockchain setup and
contract deployment:

```bash
venv/bin/python deploy.py resume --inventory deployment-topology.json
```

The installation journal records each generated group and can be resumed with
the same inventory after a transient failure.

### Recommended storage sequence

1. Deploy the first storage node listed in `deployment-topology.json`; it seeds the Cluster.
2. Deploy the remaining nodes using their individual `storage_node_id`.
3. Verify the cluster with `venv/bin/python deploy.py verify --inventory deployment-topology.json`.
4. Deploy the apps role with its `storage_access_group`.
5. Add nodes by editing the shared topology and rerun `storage reconcile`.

Kubo and Cluster identities are generated once and retained in named Docker
volumes. Do not copy one node's identity volume to another node.

## Blockchain handoff

A blockchain-only installation generates:

- `.env.integration`: RPC URL, chain ID, contract addresses and ABIs;
- `DARK_PLATFORM_ADDRESS`, when `SIGNER_MODE=shared` is active.

Production uses one externally provisioned `MASTER_WALLET_KEY_FILE` on
Blockchain and Apps. The private key is not placed in either handoff file.
Copy only the public `.env.integration` from Blockchain to Apps. Storage
nodes need neither blockchain handoff nor the platform signer.

## Store API durability

The topology defines the two thresholds explicitly:

| Topology | Example policy | Successful Store API write |
| --- | --- | --- |
| developer simple | `publish=1`, `target=1` | Cluster accepts the CID |
| developer HA | `publish=1`, `target=2` | Cluster accepts the CID |
| production | chosen in topology | Cluster accepts the CID |

Store API returns the CID after Cluster accepts the add. The Minter publishes
only after its reconciler observes the configured publication threshold for L1
and L2; payloads are purged at the independent target threshold.

Health endpoints:

| Endpoint | Consumer |
| --- | --- |
| `/health/live` | container runtime |
| `/health/read` | Resolver/load balancer |
| `/health/write` | Minter/load balancer |
| `/health` | write-readiness compatibility alias |

## Operations

```bash
venv/bin/python deploy.py status --inventory deployment-topology.json
venv/bin/python deploy.py verify --inventory deployment-topology.json
venv/bin/python deploy.py recreate --inventory deployment-topology.json --group apps --service store-api --build
```

The deployer never deletes persistent data automatically. Remove generated
runtime data only after verifying the inventory and the intended deployment.

## Repository layout

```text
dark-deployer/
├── deploy.py
├── deployment-topology.example.json
├── deployment_v2/              # inventory, rendering and runtime orchestration
├── blockchain/                 # internal Besu runtime templates/scripts
├── dark_deployer/
│   ├── commands.py
│   ├── files.py
│   ├── process.py
│   └── storage.py
├── docs/
└── components/                 # cloned component repositories; Git-ignored
```

## Tests

```bash
python3 -m unittest discover -s tests -v

cd components/dark-store-api
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

## License

Software is licensed under AGPL-3.0-or-later. Documentation is licensed under
CC BY 4.0 unless noted otherwise.
