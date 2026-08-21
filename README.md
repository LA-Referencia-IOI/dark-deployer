# dARK Deployer

Installer and operational tooling for the dARK blockchain, application and
global IPFS storage infrastructure.

## Architecture

The storage model has one supported shape:

```text
one global CRDT IPFS Cluster
└── N sites
    ├── two storage servers: Kubo + Cluster peer
    └── one application tier: Store API + Minter + Resolver
```

Each site-local Store API uses its two local storage servers. Cross-site block
and Cluster traffic travels over the VPN; Store APIs do not replicate to or
control one another. See [IPFS architecture](docs/ipfs-architecture.md) for the
failure model and design rationale.

The `developer` profile has local-only modes: one lightweight peer or two
independent peers simulating site HA on the same machine as Store API. The
single-peer topology is not accepted by `sandbox` or `production`.

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
cp .env.example .env
python3 install.py
```

The wizard offers two storage modes:

- `Simple`: one local Kubo + Cluster peer, using `storage-topology.json`.
- `HA simulation`: two independent peers in one site, using
  `storage-topology.developer-ha.json`.

Both modes share one private-swarm key and one Cluster secret under
`.dark-secrets/developer/`. Existing topology and secret files are never
overwritten, and all generated paths are excluded from Git. HA simulation uses
separate containers, identities, networks and volumes, but both peers still
share the same physical Docker host.

For sandbox infrastructure, configure the topology and secrets explicitly:

```bash
cp .env.example .env
cp storage-topology.example.json storage-topology.json
```

Edit `storage-topology.json` once for the whole infrastructure. Every site must
contain exactly two peers:

```json
{
  "version": 1,
  "cluster_name": "dark-global",
  "sites": [
    {
      "id": "site-a",
      "peers": [
        {"id": "site-a-storage-1", "vpn_address": "10.200.1.11"},
        {"id": "site-a-storage-2", "vpn_address": "10.200.1.12"}
      ]
    }
  ]
}
```

The live file is excluded from Git. Copy the same topology to storage and apps
hosts. In `.env`, select the active profile and configure its site/role fields:

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

Then select the active profile and configure its site/role fields:

```ini
TYPE=production
PRODUCTION_INSTALL_COMPONENTS=storage-node
PRODUCTION_STORAGE_TOPOLOGY_FILE=storage-topology.json
PRODUCTION_STORAGE_SITE_ID=site-a
PRODUCTION_STORAGE_NODE_ID=site-a-storage-1
PRODUCTION_IPFS_SWARM_KEY_FILE=/run/dark-secrets/ipfs-swarm.key
PRODUCTION_IPFS_CLUSTER_SECRET_FILE=/run/dark-secrets/ipfs-cluster-secret
```

For an apps host, use `PRODUCTION_INSTALL_COMPONENTS=apps` and omit the node and
storage secret fields. It still needs `PRODUCTION_STORAGE_SITE_ID` so the
installer can generate both local endpoint lists.

For a complete, address-by-address production runbook covering one blockchain
server, one apps server and two IPFS servers on one LAN, see
[Production four-server installation](docs/production-single-site-four-server-installation.md).

Production now uses a canonical inventory instead of four independently edited
`.env` files. Start from `deployment-inventory.example.json`, lock every
component revision, and render the public host bundles:

```bash
cp deployment-inventory.example.json deployment-inventory.json
python3 install.py lock
# Set the exact deployer commit, addresses and secret target paths in the inventory.
python3 install.py deployment validate --inventory deployment-inventory.json
python3 install.py deployment render \
  --inventory deployment-inventory.json \
  --output dist/dark-site-a-1
```

The renderer creates one shared topology, endpoint and firewall policy plus one
host configuration per server. The bundle contains secret paths but never
secret contents. See [Production deployment bundles](docs/production-deployment-bundles.md).

## Roles

| Value | Installed on this server |
| --- | --- |
| `storage-node` | one Kubo daemon and one Cluster peer |
| `apps` | core library, Admin API, Store API, Minter, Resolver and dashboard |
| `blockchain` | blockchain node, contracts and explorer |
| `all` | all three roles; mainly useful for constrained non-production setups |

There is no legacy `storage` role and no single-host three-peer IPFS mode.
Store API always belongs to `apps`, never to a storage node.

## Install

Validate before changing the host:

```bash
python3 install.py validate
python3 install.py plan
python3 install.py
```

The interactive wizard selects only the profile and role. It does not collect
secrets or topology addresses. Missing fields are reported before repositories,
containers or networks are changed.

If an installation stops after completing earlier stages, resume from the
first stage that did not finish instead of rerunning blockchain setup and
contract deployment:

```bash
python3 install.py resume --from ipfs
python3 install.py resume --from store-api
```

Valid stages, in order, are `blockchain`, `core-lib`, `admin`, `ipfs`,
`store-api`, `resolver`, `minter` and `dashboard`. Resume uses the saved `.env`
and blockchain handoff, does not open the wizard, and reports every preserved
stage.

### Recommended site sequence

1. Deploy the first peer listed in `storage-topology.json`; it seeds the Cluster.
2. Deploy its partner in the same site.
3. Verify both peers with `python3 install.py storage audit`.
4. Deploy the site's apps role; its Store API uses both local peers.
5. Add later sites two storage peers at a time.
6. Run `python3 install.py storage reconcile` after every topology expansion.

Kubo and Cluster identities are generated once and retained in named Docker
volumes. Do not copy one node's identity volume to another node.

## Blockchain handoff

A blockchain-only installation generates:

- `.env.integration`: RPC URL, chain ID, contract addresses and ABIs;
- `DARK_PLATFORM_ADDRESS`, when `SIGNER_MODE=shared` is active.

Production V1 uses one externally provisioned `PLATFORM_PRIVATE_KEY_FILE` on
Blockchain and Apps. The private key is not placed in either handoff file.
Copy only the public `.env.integration` from Blockchain to Apps. Legacy profiles
may still use `.env.integration.secrets` for backward compatibility. Storage
nodes need neither blockchain handoff nor the platform signer.

## Store API durability

The deployer calculates policy from topology size:

| Topology | Cluster min/max | Successful Store API write |
| --- | --- | --- |
| developer single node | `1/1` | 1 pinned peer |
| one site | `1/2` | 1 pinned peer; purge after 2 local copies |
| two or more sites | `3/(2 × sites)` | 1 pinned peer; purge after 2 local and 1 remote copies |

Store API returns `503` when it creates a CID but cannot observe one pinned peer
before timeout. Minter must not publish that CID on-chain until a retry succeeds.

Health endpoints:

| Endpoint | Consumer |
| --- | --- |
| `/health/live` | container runtime |
| `/health/read` | Resolver/load balancer |
| `/health/write` | Minter/load balancer |
| `/health` | write-readiness compatibility alias |

## Operations

```bash
# Read-only global status
python3 install.py storage audit

# Reapply topology replication factors to every existing pin; never unpins
python3 install.py storage reconcile

# Restart installed stacks and check topology-aware endpoints
python3 restart.py

# Stop stacks while preserving storage volumes
python3 stop.py

# Rebuild one application component
python3 install.py rebuild store-api

# Record or verify exact component commits
python3 install.py lock
python3 install.py lock --check

# Inspect one rendered production host bundle
python3 install.py host validate --config dist/dark-site-a-1/hosts/site-a-apps-1/host.json
python3 install.py host plan --config dist/dark-site-a-1/hosts/site-a-apps-1/host.json
python3 install.py host status --config dist/dark-site-a-1/hosts/site-a-apps-1/host.json --json
```

`clean.py` preserves the named Kubo and Cluster volumes even when cleaning the
rest of the development stack. Storage deletion is intentionally not automated.
Storage operations return exit code `2` when any CID is below the configured
minimum or reports a pin error, making the audit suitable for monitoring jobs.

## Repository layout

```text
dark-deployer/
├── install.py
├── restart.py / stop.py / clean.py
├── storage-topology.example.json
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

cd components/services/dark-store-api
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

## License

Software is licensed under AGPL-3.0-or-later. Documentation is licensed under
CC BY 4.0 unless noted otherwise.
