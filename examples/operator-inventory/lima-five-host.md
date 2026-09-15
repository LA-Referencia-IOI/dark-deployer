# lima-five-host

`lima-five-host.json` is a concrete five-machine Lima lab inventory. It uses
SSH-managed local virtual machines to exercise a remote-style deployment while
remaining reproducible on a workstation.

Use it to test remote execution, bundle transfer, multi-machine placement,
private service exposure, and the same two-validator-group, two-storage-peer
shape used by production-style examples.

## Shape

- Profile: `production`.
- Machines: `apps`, `blockchain-a`, `blockchain-b`, `storage-1`, and
  `storage-2`.
- Network: one LAN named `lab`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- RPC: one primary RPC named `rpc01`.
- Storage: two Kubo/IPFS Cluster peers, one per storage machine.
- Explorer placement: `explorer` is placed with `blockchain-a`.
- Proxy: HTTP gateway using the Lima lab host address.

## Parameters to change

- Machine `management_address` values if your Lima addresses differ.
- `defaults.ssh` and per-machine SSH fields for your local Lima user, port, and
  key.
- `networks.lab.cidr` and machine LAN addresses if the Lima subnet changes.
- `placement` when testing different service distribution.
- `blockchain.validator_groups` to exercise different validator counts.
- `storage.peers` and `storage.replication` for different durability settings.
- Proxy `host`, `public_origin`, listener, and routes for alternate ingress.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/lima-five-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/lima-five-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/lima-five-host.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-five-host.json --verbose
```

This inventory is operational, but it is still a lab. Confirm Lima networking,
SSH access, Docker availability, and chain artifact compatibility before
installing.
