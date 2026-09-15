# local-two-site

`local-two-site.json` is an executable multi-site lab. It models two sites and
six logical machines through one Docker daemon, with site-local LANs connected
by a mesh VPN network.

Use it to test site-aware routing, cross-site P2P, network-qualified endpoint
aliases, two independent Resolver-observer bundles, and storage replication
across logical failure domains.

## Shape

- Profile: `lab`.
- Machines: `apps`, `resolver`, `blockchain-a`, `blockchain-b`, `storage-1`,
  and `storage-2`, all simulated locally.
- Sites: `site-a` and `site-b`.
- Networks: `site-a-lan`, `site-b-lan`, and `mesh-vpn`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- Resolver-observer bundles: Site B has `observer01`, `resolver-api`, and its
  read-only Store API reader; Site A has `observer02`, `resolver-api-site-a`,
  and `store-api-reader-site-a` on `blockchain-a`.
- RPC: one primary RPC named `rpc01`.
- Storage: two Kubo/IPFS Cluster peers, one per storage group.
- Proxies: the existing loopback application gateway plus independent Resolver
  endpoints at `http://localhost:8082/` (Site A) and
  `http://localhost:8083/` (Site B).

## Parameters to change

- `sites` and `machines.*.site` to model a different locality split.
- `networks.*.cidr` and per-machine `addresses` if a local Docker subnet
  collides with an existing network.
- `routing.defaults`, `routing.blockchain_p2p`, `routing.storage_p2p`, and
  `routing.storage_api` to choose LAN versus VPN paths.
- `placement.resolver-observer` and `placement.site-a-resolver-observer` to
  move either Resolver bundle.
- `blockchain.validator_groups` and `blockchain.observer_groups` to change
  validator and observer cardinality.
- `storage.peers` and `storage.replication` for different replication shapes.
- Proxy host, origin, listener, and routes for a different ingress layout. The
  two Resolver proxies use distinct ports because the lab shares one Docker
  daemon.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-two-site.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/local-two-site.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-two-site.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-two-site.json --verbose
```

This is a laboratory topology. It exercises routing and placement decisions, but
all logical hosts still share one physical Docker engine.
