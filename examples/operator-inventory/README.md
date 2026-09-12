# Compact operator inventories

These files are the compact authoring format for Deployment v3. They describe
operator decisions and resolve into the complete v3 execution contract; they do
not bypass v3 validation, planning, rendering, secret handling, or verification.

Use `operator-local-simple` for a quick end-to-end test, `operator-local-ha`
to exercise two storage peers on one Docker host, and
`operator-production-five-host` as a five-machine SSH starting point. The last
template contains documentation network ranges and `REPLACE` values and must
be adapted before preflight.

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha.json \
  --output /tmp/dark-local-ha-resolved.json
venv/bin/python deploy.py plan --inventory examples/operator-inventory/local-ha.json
```

The compact inventory has one source of truth for `placement` and `storage`.
The catalogue derives application services, connections, private endpoint
exposures, secret consumers, and v3 groups. Review the resolved output or run
`inventory-explain` before applying changes. `inventory-resolve`, `plan`, and
`validate` are safe offline inspection commands; `install`, `push`, and
`apply` are operational actions.

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
