# local-ha

`local-ha.json` is a local laboratory inventory that keeps all services on one
Docker host while exercising the high-availability authoring shape: two
validator groups and two storage peers.

Use it to test dynamic validator groups, storage replication behavior, generated
connections, proxy routes, and install/verify behavior without needing remote
machines.

## Shape

- Profile: `lab`.
- Machines: one local machine named `local`.
- Network: one local LAN named `lab`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- RPC: one primary RPC named `rpc01` in the `apps` group.
- Storage: two Kubo/IPFS Cluster peers, `storage-a` and `storage-b`.
- Replication: publish after 1 replica, target 2 replicas.
- Proxy: loopback HTTP gateway on `localhost`.

## Parameters to change

- `deployment.id` and `deployment.label` when running side-by-side test
  deployments.
- `defaults.paths` for workspace, data, and secret locations.
- `networks.lab.cidr` and `machines.local.addresses.lab` if the lab subnet
  overlaps with another Docker network.
- `blockchain.validator_groups.*.validator_count` to exercise different QBFT
  validator cardinalities.
- `storage.peers` and `storage.replication` to test one, two, or more storage
  peers and replica targets.
- Proxy `host`, `public_origin`, listener, and route paths when testing a
  different local entrypoint.
- `overrides.settings` for component-level settings such as the Minter shoulder.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-ha.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-ha.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/local-ha.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json --verbose
```

The deployer will warn that the validator and storage replicas share one host.
That is expected: this scenario validates logical replication, not host-failure
tolerance.
