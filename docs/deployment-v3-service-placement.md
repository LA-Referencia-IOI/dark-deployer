# Deployment v3: explicit service placement

## Purpose

Deployment v3 combines an explicit service graph with named server groups in
the inventory. Each item in `services` declares:

```json
"metadata-worker": {
  "type": "minter-worker",
  "machine": "apps",
  "configuration": {"worker": "metadata"},
  "connections": {
    "database": {"service": "minter-postgres"},
    "store_api": {"service": "store-api"},
    "rpc": {"service": "primary-rpc"},
    "contracts": {"service": "contracts-deploy"}
  }
}
```

The renderer derives an internal Docker endpoint when both services share a
machine. A connection across machines must name its network and protocol, for example
`{"service": "primary-rpc", "network": "lan", "protocol": "tcp"}`. Its provider must declare a
matching `"exposure": {"mode": "private", "network": "lan", "port": ...}`.
The generated consumer configuration then uses that machine's address on the
selected LAN/VPN. If the consumer has no interface in that domain, the
inventory must declare an existing directional route. A loopback exposure is
never accepted for a remote consumer.
Store API follows the same rule: it explicitly lists every IPFS Cluster peer it
uses, so no inter-host storage endpoint is inferred implicitly.

## Five-server deployment shape

The maintained HA inventory keeps the v2 operational units visible even when
they run on one local Docker daemon:

| Group | Role |
| --- | --- |
| `apps` | RPC and application services |
| `blockchain-a` | validators 01 and 02 |
| `blockchain-b` | validators 03 and 04 |
| `storage-1` | Kubo and Cluster peer A |
| `storage-2` | Kubo and Cluster peer B |

Groups are logical server units. Their `machine` selects the Docker daemon or
SSH host, while `services`/`members` preserve the role boundary. Local Docker
may map all five groups to one daemon; production maps them to five hosts.
Ports remain service-internal and standard. Only a cross-host transport gets a
published, inventory-derived port.

## Minter placement invariant

The supported operational model deliberately enforces one exception to fully
free placement. These services must live on the same machine:

- one `minter-api`;
- one `minter-postgres`;
- exactly three `minter-worker` instances configured as `metadata`,
  `replication`, and `chain`.

This keeps the metadata payload filesystem local to all Minter processes and
prevents an accidental network filesystem dependency. The validation failure
is explicit if an inventory violates this rule.

## Roles are composed, not assumed

The maintained production example maps service instances as follows:

| Machine | Instances |
| --- | --- |
| `apps` | RPC, contract job, Minter stack, Admin, Resolver, Store, dashboard and edge proxy |
| `blockchain-a` | validators 01/02 |
| `blockchain-b` | validators 03/04 |
| `storage-1` | Kubo and Cluster peer A |
| `storage-2` | Kubo and Cluster peer B |

This is a template, not hidden installer logic. Moving an independently
placeable service means changing its `machine` and declaring the required
private exposure and connection. The planner derives startup order from those
connections.

## Generated runtime layout

Each machine receives an aggregate Compose file for compatibility and one
separate Compose project per named group, with environment files only for its
assigned services. Groups on the same local Docker daemon share that machine's
bridge network, which preserves the five-server simulation; production does
not attempt to join Docker networks across physical hosts. Besu P2P uses the inventory's
network selected by `infrastructure.besu` and deterministic published P2P
ports; RPC may be private on a different declared LAN for the explorer. The
deployment-generated data directory contains bind-mounted
service data, while the secret directory contains private chain artifacts and
operator-provisioned secret files.

The renderer also emits one firewall suggestion per machine. It includes the
declared service exposures plus the P2P ports derived from the central Besu,
Kubo and Cluster policies. It does not configure the host firewall itself.

Run `deploy.py validate` and `deploy.py render` before `install`. The latter
is the safe way to inspect every generated endpoint and Compose assignment
without starting containers.
