# Compact operator inventories

These files are the compact authoring format for Deployment v3. They describe
operator decisions and resolve into the complete v3 execution contract; they do
not bypass v3 validation, planning, rendering, secret handling, or verification.

Use `operator-local-simple` for a quick end-to-end test, `operator-local-observer`
to exercise an observer and a private Resolver binding, `operator-local-ha`
to exercise two storage peers on one Docker host, and
`operator-local-two-site` to exercise two isolated Docker LANs connected by a
Docker VPN mesh, and
`operator-production-five-host` as a five-machine SSH starting point. The last
template contains documentation network ranges and `REPLACE` values and must
be adapted before preflight.

Install a maintained example directly; no copy or render step is required:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json --verbose
```

For offline inspection:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha.json \
  --output /tmp/dark-local-ha-resolved.json
venv/bin/python deploy.py plan --inventory examples/operator-inventory/local-ha.json
```

`local-two-site.json` is a separate executable laboratory. Its six logical
machines run through one Docker daemon, but they are attached to `site-a-lan`,
`site-b-lan`, and `mesh-vpn` exactly as declared. The network-qualified DNS
aliases make the chosen path explicit (`validator03-mesh-vpn`, for example):

```bash
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/local-two-site.json
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-two-site.json --verbose
```

The compact inventory has one source of truth for `placement`, `storage` and
`proxies`. Each proxy owns a machine, listener, public origin, TLS mode and
typed route destinations; a machine may run only one proxy.
The catalogue derives application services, connections, private endpoint
exposures, secret consumers, and v3 groups. Review the resolved output or run
`inventory-explain` before applying changes. `inventory-resolve`, `plan`, and
`validate` are safe offline inspection commands; `install`, `push`, and
`apply` are operational actions.

## Multiple sites

Operator format v3 may declare the LAN that belongs to each site and assign a
site to every machine. A locality policy keeps the authoring form compact while
the resolver produces explicit directed P2P edges for Besu, Kubo and Cluster:

```json
"format_version": 3,
"sites": {"eu": {"lan": "eu-lan"}, "us": {"lan": "us-lan"}},
"routing": {
  "defaults": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
  "blockchain_p2p": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
  "storage_p2p": {"same_site": "site_lan", "cross_site": "mesh-vpn"}
}
```

Each machine declares `site` plus its LAN address and, when it participates in
cross-site traffic, its VPN address. Inspect the resulting endpoint matrix
before applying it:

```bash
venv/bin/python deploy.py inventory-network-matrix \
  --inventory inventory.json
```

Version 2 inventories keep their existing global routing behavior.

`routes` declares a directional route already provided by the site network;
it does not configure a router or firewall. `networking.services` is reserved
for endpoints that differ from their bind address, such as NAT:

```json
"routes": [{"from": "apps-lan", "to": "storage-vpn", "via": "site-vpn"}],
"networking": {
  "services": {
    "rpc01": {"advertise_address": "203.0.113.20", "advertise_port": 18545},
    "ipfs-storage-a": {"p2p_advertise_port": 4101}
  }
}
```

API advertise fields require a private endpoint derived from a remote
dependency. `p2p_advertise_port` is available only to Besu, Kubo and Cluster;
the rendered Compose port mapping and Besu chain artifact use the same value.

For the complete workflow and examples of local, HA, remote, storage, routing,
and override configuration, see [the operations manual](../../OPERATIONS-MANUAL.md).
