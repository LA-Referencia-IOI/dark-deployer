# local-observer

`local-observer.json` is a single-host local inventory focused on the Besu
observer role. It keeps one observer node beside the applications so Resolver
can use a local observer RPC without making that node part of consensus.

Use it to verify observer rendering, observer RPC wiring, local Resolver
configuration, and observer-aware chain artifact behavior.

## Shape

- Profile: `local`.
- Machines: one local machine named `local`.
- Network: one local LAN named `lab`.
- Blockchain: one validator group named `validators` with 4 validators.
- Observer: one observer group named `observers` with 1 observer.
- RPC: one primary RPC named `rpc01` in the `apps` group.
- Storage: one Kubo/IPFS Cluster peer named `storage-a`.
- Proxy: loopback HTTP gateway on `localhost`.

## Parameters to change

- `deployment.id` and `deployment.label` for separate observer experiments.
- `defaults.paths` for local workspace, data, and secret roots.
- `networks.lab.cidr` and `machines.local.addresses.lab` if there is a subnet
  collision.
- `blockchain.observer_groups.*.observer_count` to test one or more observers.
- Resolver-related placement and overrides if you want Resolver to move away
  from the observer group in a derived inventory.
- `blockchain.chain_id` when generating a fresh chain artifact.
- Proxy `host`, `public_origin`, and route paths for alternate local URLs.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-observer.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-observer.json
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-observer.json \
  --output /tmp/dark-local-observer-resolved.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-observer.json --verbose
```

Observers keep a synchronized chain copy and may expose RPC internally for
Resolver, but they do not validate blocks and do not increase QBFT quorum
tolerance.
