# dARK Deployer

## Single source of truth

The complete v3 document is the execution inventory. It is written directly by
the maintained templates in `examples/deployment-v3/` or generated from the
compact operator format in `examples/operator-inventory/`. The compact format
describes operator decisions; the resolver expands it into the complete v3
document before validation, planning, rendering or execution. `.env` is
optional runtime input, not a second inventory.

Both formats contain no secret values. They may contain controller-side source
paths for private material. The generated `shared/deployment-topology.json` is
the fully resolved v3 inventory; its filename is retained only for bundle
compatibility and is not the name of the inventory model.

## CLI

```bash
venv/bin/python deploy.py inventory-create --template local-ha --output inventory.json
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json
venv/bin/python deploy.py render --inventory inventory.json
venv/bin/python deploy.py preflight --inventory inventory.json
venv/bin/python deploy.py install --inventory inventory.json
venv/bin/python deploy.py status --inventory inventory.json
venv/bin/python deploy.py verify --inventory inventory.json
```

For a compact operator inventory, install the maintained file directly:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json --verbose
```

`inventory-create` is optional when an editable copy is needed. The resolver
also runs implicitly for `validate`, `plan`, `render`, `push`, `apply` and
`install`; use `inventory-resolve` or `inventory-explain` only to inspect the
effective v3 contract before execution.

For offline inspection of a copied inventory:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory inventory.json --output resolved-inventory.json
venv/bin/python deploy.py inventory-explain \
  --inventory inventory.json --path /services/store-api
venv/bin/python deploy.py inventory-diff \
  --before previous-inventory.json --after inventory.json
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json --json
```

These inspection commands are offline. They do not access Docker, SSH, Git or
secret contents. `inventory-resolve` refuses to overwrite its output.

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

Docker bridge networks are named `<deployment-id>-<machine-id>` and are reused
only when that exact network already has the subnet derived from the inventory.
If another empty bridge network overlaps the requested subnet, `install`,
`apply`, and `resume` can remove it only with the explicit
`--clean-empty-network-conflicts` option. A network with attached containers is
never removed automatically.

The default order is validators, RPC quorum, contracts, storage peers, data
services, applications, and the edge proxy. `recreate` targets one service
while preserving persistent data; `resume` revalidates uncertain phase gates.

The recommended HA shape is five logical server groups: `apps`,
`blockchain-a`, `blockchain-b`, `storage-1` and `storage-2`. Local Docker may
map all five groups to one daemon; a remote deployment maps them to five
machines. The group boundary is retained in the rendered plan.

## Compact operator inventory

The compact format uses `format: "dark-operator-inventory"` and
`format_version: 2`. It selects the parameterized `dark-platform-baseline-v1.0` catalogue and
records the decisions that normally change between installations:

- `deployment`: stable ID and label;
- `machines`: local or SSH hosts and their network addresses;
- `placement`: the authoritative group-to-machine mapping;
- `blockchain`: chain ID, RPC identity and validator group placement;
- `storage`: peer IDs, peer groups, replica thresholds and optional local Store API replicas;
- `networks`, `routes` and `routing`: networks, existing directional routes,
  and traffic choices used by remote traffic;
- `networking`: optional NAT addresses and translated P2P ports;
- `proxies`: explicit proxy instances, listeners, TLS mode, virtual hosts and
  path-to-service rules. `access` is retained only for legacy inventories and
  cannot be combined with `proxies`;
- `secrets`: controller-side source root or explicit source files;
- `overrides`: supported changes to catalogue settings/components and placement.

The catalogue expands application services, dependencies, component defaults,
settings, ports, secret consumers and the complete v3 graph. It creates the
Kubo/Cluster pair for each declared peer. One peer is a one-copy deployment;
two peers on one Docker host test replication but do not provide host-failure
tolerance.

The Minter shoulder is explicit in all compact examples:

```json
{
  "overrides": {
    "settings": {
      "minter": {"shoulder": "200"}
    }
  }
}
```

The value must use the current `2MM` format. The resolver validates the whole
expanded document before a plan is built. Invalid shoulders, missing groups,
unsupported peers, ambiguous routes and invalid replica counts fail early.

The current catalogue supports dynamic validator groups, observer groups,
multiple RPC nodes with an explicit primary, the Minter stack, dashboard,
Store, and any positive number of storage peers. Node, group and peer names
come from the operator inventory rather than a fixed catalogue topology.

When a consumer must keep querying storage while the `apps` host is unavailable,
`storage.api_replicas` can place another stateless Store API next to that
consumer. Each replica declares its target group and the services whose
`store_api` connection it replaces. Every Store API receives the same Cluster
peer set; this improves application-path availability but does not create an
additional IPFS data copy. `production-six-host.json` places
`store-api-resolver` with `resolver-api` for that reason.

Same-machine dependencies use Docker DNS. A remote dependency receives the
network selected by `routing`, TCP, and a matching private provider exposure.
The consumer may use another local domain only when `routes` declares the
existing path to the provider network. NAT translations are described under
`networking`, without changing the service bind address.
Store's Kubo route is derived from each declared Cluster peer, so it does not
need to be duplicated in the compact file.

Catalogue private exposures disappear when no remote dependency needs them.
Active examples declare a proxy explicitly; a proxy listener does not make any
backend public. Its routes alone decide which application paths are published.

The resolver records the input digest, catalogue digest and resolver version.
Rendering writes this evidence to `shared/inventory-resolution.json` alongside
the fully resolved inventory.

The Textual editor detects compact inventories and presents decision-oriented
sections for placement, storage, routing, proxies, legacy access and overrides. It validates
the expansion when changing a section or saving, and keeps atomic saves and
timestamped backups.

Available compact templates are `operator-local-simple`, `operator-local-observer`, `operator-local-ha`,
`operator-production-five-host` and `operator-production-six-host`. Production
templates use documentation network ranges and `REPLACE` paths; replace them
before preflight.

## Networks, ports and storage bootstrap

The inventory declares named `lan`/`vpn` networks and each machine has one
address per network, or several `cidrs` for one routed logical domain. Private
service exposures state their network, port and protocol. A cross-host
connection names an endpoint on the provider network; the consumer must share
that network or declare a directional route. Docker DNS is never assumed
across hosts.

The central `infrastructure` policy is the source of published ports:

- Besu uses the standard internal P2P port `30303`. Local peers use distinct
  derived Docker addresses and keep `30303` in `static-nodes.json`; only
  remote hosts use `p2p_port_start` plus deterministic node ordering for
  published host ports.
- Kubo publishes its API privately on `api_port` and its swarm on `swarm_port`
  (`tcp` and `udp`).
- IPFS Cluster publishes its REST API privately on `api_port` and its libp2p
  port on `p2p_port`.
- `edge-proxy` can have several instances across different machines, with at
  most one proxy per machine. Each rule references a typed HTTP
  service connection, so local destinations use Docker DNS and remote ones use
  the selected private LAN/VPN endpoint. The standard gateway maps `/` to the
  Resolver's `/api/v1/arks/`, `/admin/` to Dashboard, `/explorer/` to Explorer,
  and `/api/` documentation/API routes to Minter.

Bootstrap environment variables use the peer API endpoint deliberately: the
Kubo and Cluster entrypoints resolve those endpoints to the peer's announced
P2P multiaddresses before joining. The firewall suggestion includes both the
declared API/web exposures and the policy-derived P2P ports.

## Inventory editor

`requirements-tui.txt` enables the Textual editor:

```bash
venv/bin/python deploy.py inventory-edit --inventory inventory.json
```

The editor validates the complete document and saves atomically with a backup.
It never starts Docker or reads secret contents.

For a compact inventory, the saved file remains compact. The resolved v3
document is produced explicitly with `inventory-resolve` or implicitly by
`validate`, `plan`, `render`, `push`, `apply` and `install`.

For the v3 service graph and endpoint rules, see
[deployment-v3-service-placement.md](deployment-v3-service-placement.md).
