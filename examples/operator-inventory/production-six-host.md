# production-six-host

`production-six-host.json` extends the five-server production shape with a
dedicated Resolver host. That host also runs a Besu observer so Resolver can use
a local chain RPC while remaining outside the validator set.

Use it for production designs where public resolution is isolated from the main
application host and backed by an observer chain copy.

## Shape

- Profile: `production`.
- Machines: `apps`, `resolver`, `blockchain-a`, `blockchain-b`, `storage-1`,
  and `storage-2`.
- Networks: `lan` and `vpn`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- Resolver-observer bundle: `resolver-api`, its local read-only Store API reader, one Besu
  observer, and the Resolver proxy share the `resolver-observer` group on the
  `resolver` machine.
- RPC: one primary RPC named `rpc01`.
- Storage: two Kubo/IPFS Cluster peers, one per storage machine.
- Explorer placement: `explorer` is placed with the application stack.
- Proxies: a Resolver public route and an application public route.

## Parameters to change

- Deployment identity, labels, and profile acceptance fields.
- `defaults.ssh`, machine SSH overrides, and machine `management_address`.
- `defaults.paths` for production directories.
- `networks.lan.cidr`, `networks.vpn.cidr`, and machine LAN/VPN addresses.
- `placement.resolver-observer` if the Resolver host changes.
- `blockchain.validator_groups`, `blockchain.observer_groups`, RPC settings, and
  chain artifact paths.
- Resolver and application proxy hosts, origins, listener ports, TLS settings,
  and routes.
- `storage.peers` and replication limits.
- Component overrides for Resolver, Minter, Admin, Dashboard, Explorer, Store,
  and workers.
- Firewall policy derived from public proxies, private APIs, P2P ports, and
  storage traffic.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/production-six-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/production-six-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/production-six-host.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/production-six-host.json --verbose
```

Do not install this inventory unchanged. Replace documentation addresses,
domains, SSH settings, secret references, and artifact sources before preflight.
