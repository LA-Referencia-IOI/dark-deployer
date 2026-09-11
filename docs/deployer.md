# dARK Deployer

## Single source of truth

`deployment-topology.json` is the only deployment inventory. Maintained
templates are `examples/deployment-v3/local-simple.json`, `local-ha.json`, and
`production-five-host.json`. The inventory describes hosts, execution mode,
Docker networks per machine, explicit server groups and service placement, Besu assignments, storage peers, component repositories
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
run. `recreate --service <service> --build` rebuilds one service without
restarting other services on its machine. `--skip-acquire` reuses prepared
checkouts.

## Acquisition and safety

Acquisition clones missing components, fetches the configured branch, and uses
`merge --ff-only`. Divergent or locally modified checkouts stop instead of being
overwritten. Rendering creates public bundles and manifests; private keys and
secret contents are never copied into them. Apply validates host role, artifact
hashes, paths and required Docker capabilities before mutation.

## Wallet and chain artifacts

An inventory may define `secrets.master-wallet.source`. If that file exists,
install confirms it and derives the public address automatically. The explicit
`--master-wallet-file` takes precedence. `--create-master-wallet` invokes the
guarded generator when no wallet exists. Existing networks use a provisioned
chain artifact; genesis and identities are not regenerated.

## Local and remote execution

Local inventories run Compose on the current Docker host. Remote inventories
render one bundle per declared host and execute over SSH. Production uses local
Docker networks per host; every cross-host dependency explicitly names the LAN
or VPN it uses. Docker DNS is never assumed between physical hosts.

## Rendering and safety

Validation checks schema, role assignments, addresses on every declared
network, explicit remote routes and protocols, replica limits, safe secret paths, consumers,
and component references. Rendering produces deterministic Compose and
environment files, endpoint/firewall data, source evidence and a hash manifest.
`shared/firewall-suggestion.json` contains both declared API/web exposures and
policy-derived Besu, Kubo and Cluster peer-to-peer ports; it is an auditable
input to host firewall automation, not a replacement for it.
Remote apply transfers public bundles over SSH and then transfers each secret
only to its declared consumer hosts. Chain artifacts are filtered by node, so a
machine receives only genesis, static nodes and its own node key. Private
values never enter public bundles or reports.

The default order is validators, RPC quorum, contracts, storage peers, data
services, applications, and the edge proxy. `recreate` targets one service
while preserving persistent data; `resume` revalidates uncertain phase gates.

The recommended HA shape is five logical server groups: `apps`,
`blockchain-a`, `blockchain-b`, `storage-1` and `storage-2`. Local Docker may
map all five groups to one daemon; a remote deployment maps them to five
machines. The group boundary is retained in the rendered plan.

## Networks, ports and storage bootstrap

The inventory declares named `lan`/`vpn` networks and each machine has one
address per network. Private service exposures state their network, port and
protocol. A cross-host connection only names an endpoint already exposed on
the shared network; Docker DNS is never assumed across hosts.

The central `infrastructure` policy is the source of published ports:

- Besu uses the standard internal P2P port `30303`. Local peers use distinct
  derived Docker addresses and keep `30303` in `static-nodes.json`; only
  remote hosts use `p2p_port_start` plus deterministic node ordering for
  published host ports.
- Kubo publishes its API privately on `api_port` and its swarm on `swarm_port`
  (`tcp` and `udp`).
- IPFS Cluster publishes its REST API privately on `api_port` and its libp2p
  port on `p2p_port`.
- `edge-proxy` is the only `public` exposure and maps `/explorer/` to the
  explorer service and `/` to the dashboard.

Bootstrap environment variables use the peer API endpoint deliberately: the
Kubo and Cluster entrypoints resolve those endpoints to the peer's announced
P2P multiaddresses before joining. The firewall suggestion includes both the
declared API/web exposures and the policy-derived P2P ports.

## Inventory editor

`requirements-tui.txt` enables the Textual editor:

```bash
venv/bin/python deploy.py inventory-edit --inventory deployment-topology.json
```

The editor validates the complete document and saves atomically with a backup.
It never starts Docker or reads secret contents.

For the v3 service graph and endpoint rules, see
[deployment-v3-service-placement.md](deployment-v3-service-placement.md).
