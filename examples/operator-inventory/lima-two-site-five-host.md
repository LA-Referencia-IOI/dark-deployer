# lima-two-site-five-host

`lima-two-site-five-host.json` is a five-machine Lima lab that models two sites
joined by a mesh VPN. It is the remote-lab counterpart to `local-two-site.json`.

Use it to test site-aware routing on real SSH-managed hosts, with application
services in one site, validator and storage groups split across two sites, and
cross-site P2P over the declared VPN.

## Shape

- Profile: `lab`.
- Machines: `apps`, `blockchain-a`, `blockchain-b`, `storage-1`, and
  `storage-2`.
- Sites: `site-a` and `site-b`.
- Networks: `site-a-lan`, `site-b-lan`, and `mesh-vpn`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- RPC: one primary RPC named `rpc01`.
- Storage: two Kubo/IPFS Cluster peers.
- Explorer placement: `explorer` is placed with `blockchain-a`.
- Proxy: HTTP gateway using the Lima lab application host.

## Parameters to change

- `machines.*.management_address` for the actual Lima or SSH target addresses.
- `defaults.ssh` and per-machine SSH overrides for your local VM setup.
- `sites`, machine `site`, and network CIDRs to match the two-site lab.
- Per-machine `addresses` on the LAN and VPN networks.
- `routing.defaults` and role-specific routing policies to choose LAN versus
  VPN paths.
- `routes` if the host network provides additional directional paths.
- `placement` to move apps, validators, Explorer, or storage groups.
- Proxy host, origin, listener, and public routes.

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/lima-two-site-five-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/lima-two-site-five-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/lima-two-site-five-host.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-two-site-five-host.json --verbose
```

This inventory assumes the site LANs and mesh VPN already exist. The deployer
uses those declarations for rendering, connectivity checks, and endpoint
selection; it does not create the VPN.
