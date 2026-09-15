# local-simple

`local-simple.json` is the smallest maintained operator inventory. It runs the
complete dARK stack on one local Docker host with one storage peer, one primary
RPC node, and one validator group.

Use it for quick functional checks, local development, and validating that the
deployer, component checkouts, secrets, blockchain artifact handling, rendering,
and verification pipeline work end to end.

## Shape

- Profile: `local`.
- Machines: one local machine named `local`.
- Network: one local LAN named `lab`.
- Blockchain: one validator group named `validators` with 4 validators.
- RPC: one primary RPC named `rpc01` in the `apps` group.
- Storage: one Kubo/IPFS Cluster peer named `storage-a`.
- Proxy: loopback HTTP gateway on `localhost`.

## Parameters to change

- `deployment.id` and `deployment.label` if you need separate local deployments.
- `defaults.paths` when `/srv/dark`, `/srv/dark/data`, or `/srv/dark/secrets`
  are not appropriate for your machine.
- `networks.lab.cidr` and `machines.local.addresses.lab` if the default lab
  address conflicts with another local Docker or host network.
- `blockchain.chain_id` when creating a fresh chain artifact for an isolated
  test.
- `overrides.settings.minter.shoulder` for a different ARK shoulder.
- Proxy `host`, `public_origin`, and route paths if testing a different local
  URL layout.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-simple.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-simple.json
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-simple.json \
  --output /tmp/dark-local-simple-resolved.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-simple.json --verbose
```

Expect no host-failure tolerance. This inventory is intentionally compact and
local; it is not a production availability model.
