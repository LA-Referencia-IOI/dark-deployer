# Deployment v3 examples

These are complete, executable inventory templates. Copy one outside this
directory, replace all placeholder network, SSH, repository, and secret values,
then validate it before installation:

```bash
cp examples/deployment-v3/production-five-host.json inventory.json
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py render --inventory inventory.json --output /tmp/dark-render
```

> Note: this directory demonstrates the legacy full deployment-v3 inventory
> format. The preferred compact operator-inventory format lives in
> [../operator-inventory/](../operator-inventory/); see the deployer
> documentation ([docs/deployer.md](../../docs/deployer.md)).

The inventory contains no secret values. Each `secrets` entry declares its
controller-side `source`, consumer services, destination `path`, and strict
`0600` mode. The deployer transfers only the files needed by each host; public
bundles contain neither their values nor their hashes.

## Templates

| Template | Intended use | Machines | Storage target |
| --- | --- | ---: | ---: |
| `local-simple.json` | Lightweight local functional test | 1 local Docker host | 1 copy |
| `local-ha.json` | Full deployment shape simulated on one Docker host | 1 local Docker host | 2 copies |
| `lima-five-host.json` | Lima reference snapshot; generate current VM data through `local-infra/generate-lima-inventory.py` | 5 SSH hosts | 2 copies |
| `production-five-host.json` | Production reference to adapt for a LAN/VPN | 5 SSH hosts | 2 copies |
| `production-six-host.json` | Dedicated public Resolver plus LAN/VPN apps gateway | 6 SSH hosts | 2 copies |

`local-simple` retains one local IPFS peer and exercises the Store API and
Cluster layout with a one-copy target. It is not a replication or two-host
failure test. `local-ha` retains both peers and uses a two-copy target.

The component key remains `dark-explorador` because that is the local checkout
directory and service name; its canonical Git origin is
`git@github.com:LA-Referencia-IOI/dark-explorer.git`.

## Top-level sections

| Section | What it defines | Operator action |
| --- | --- | --- |
| `deployment` | Stable deployment ID and human label | Change ID and label for each independent installation. |
| `defaults` | Default SSH account, source/data/secrets roots, Docker subnet pool | Replace the sample SSH key and production filesystem roots. |
| `networks` | Named LAN/VPN CIDRs used between hosts | Declare every usable network once; use `cidrs` for routed segments in one domain. |
| `routes` | Explicit directional routes between network domains | Declare an existing site route when a consumer lacks the provider network. |
| `machines` | Execution mode, management address, and one address per LAN/VPN | Use `local` only for the controller Docker host; use `ssh` for remote hosts. |
| `services` | Every runtime service, its host, typed dependencies, routes, and exposure | Remote connections must name their network and protocol. |
| `infrastructure` | Central Besu, Kubo, Cluster and web port policy | Do not duplicate P2P ports in service instances. |
| `blockchain` | Besu version, QBFT timing, chain ID, artifact location, five nodes | Do not alter the artifact location or identities for an existing chain. |
| `storage` | Cluster name, logical peers, publication and final durability policy | `target_replicas` cannot exceed the number of Kubo peers. |
| `components` | Source repository and branch for each build input | Pin a release branch when operating a controlled release. |
| `settings` | Minter/Store worker tuning rendered into containers | Tune only after measuring a representative workload. |
| `secrets` | Secret file references and consuming service IDs | Provision files out of band; never put their values in JSON. |

The production reference uses documentation ranges (`192.0.2.0/24` and
`198.51.100.0/24`) and explicit `REPLACE` values. Do not install it until the
network CIDRs, host addresses, SSH key, chain artifact, and secret sources
have been replaced for the real LAN/VPN.

## Service graph rules

Every connection is typed and validated. Same-machine consumers use Docker DNS.
A cross-host connection explicitly names a network and protocol. The consumer
must share that network with the provider or declare a directional route to it;
the provider must publish a matching `private` endpoint. `routes` validate
existing routing only and never configure a router, VPN, or firewall.

The following placement is intentionally fixed:

```text
minter-api + minter-postgres + metadata-worker
           + replication-worker + chain-worker
```

They must share one machine because their metadata payload directory is local.
All other service instances may be placed independently if their typed
connections and private endpoint requirements remain valid.

The maintained five-host reference assigns:

```text
apps          rpc01, contract job, Minter stack, Admin, Resolver, Store, dashboard
blockchain-a  validator01, validator02, explorer
blockchain-b  validator03, validator04
storage-1     ipfs-storage-a, cluster-storage-a
storage-2     ipfs-storage-b, cluster-storage-b
```

## Exposure modes

- `loopback`: bind only to `127.0.0.1`; suitable for an operator, local proxy,
  or local developer access. It cannot satisfy a remote service connection.
- `private`: bind to the machine address of the named LAN/VPN; use
  only for cross-host dependencies such as Store API to Cluster, explorer to
  RPC, or API consumers that reside on another host.
- `public`: bind on all interfaces. Only the `edge-proxy` should use it.
- omitted: no host port is published. Same-machine consumers still use Docker
  DNS and the container port.

## Public proxy rules

An `edge-proxy` is the only normal public entry point. Each machine can run at
most one proxy. Its `exposure.port` is the host listener; Nginx listens on
container port 80 for HTTP/external TLS and 443 for direct TLS. Every site
declares `host`, `public_origin`, and routes. `public_origin` is the user-facing
URL used by Dashboard redirects and assets, and can differ from an internal
load-balancer listener. The documented proxy examples publish HTTP on port 80;
an external load balancer may terminate TLS and forward to that listener.

## Required private material

For a new local chain, `deploy.py install --create-master-wallet` can create
the master wallet and generate the role artifact under the local generated
secret root. For SSH deployments, provision the source chain artifact,
contract signer, IPFS swarm key, and Cluster secret on the controller before
`apply`. The deployer copies each declared
secret directly to the host of its consumer and verifies its hash and mode;
the files never enter a public bundle.
