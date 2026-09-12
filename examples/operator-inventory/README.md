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

For the complete workflow and examples of local, HA, remote, storage, routing,
and override configuration, see [the operations manual](../../OPERATIONS-MANUAL.md).
