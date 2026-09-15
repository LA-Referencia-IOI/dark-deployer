# production-five-host

`production-five-host.json` is the generic five-server production starting
point. It separates applications, two validator groups, and two storage peers
across distinct SSH-managed machines.

Use it as the base for a real small production deployment after replacing every
documentation address, DNS name, SSH value, path, artifact source, and secret
source with environment-specific values.

## Shape

- Profile: `production`.
- Machines: `apps`, `blockchain-a`, `blockchain-b`, `storage-1`, and
  `storage-2`.
- Networks: `lan` and `vpn`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- RPC: one primary RPC named `rpc01`.
- Storage: two Kubo/IPFS Cluster peers, one per storage machine.
- Explorer placement: `explorer` is placed with `blockchain-a`.
- Proxy: public HTTP gateway for the application domain.

## Parameters to change

- `deployment.id` and `deployment.label` to the production deployment identity.
- `defaults.ssh` and machine SSH overrides.
- `defaults.paths` for production workspace, data, and secret roots.
- `networks.lan.cidr`, `networks.vpn.cidr`, and every machine address.
- Machine `management_address` values.
- `routing` to select which traffic uses LAN or VPN.
- `blockchain.chain_id`, validator group sizes, RPC placement, and chain
  artifact paths.
- `storage.cluster_name`, peers, and replication limits.
- Proxy `host`, `public_origin`, TLS mode, listener, and route paths.
- `overrides.settings` for component-specific production settings.
- Production availability objectives and acknowledgements if required by the
  profile validation.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/production-five-host.json
venv/bin/python deploy.py inventory-explain \
  --inventory examples/operator-inventory/production-five-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/production-five-host.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/production-five-host.json --verbose
```

Do not run this file unchanged. It contains example addressing and placeholder
production assumptions that must be adapted to the target infrastructure.
