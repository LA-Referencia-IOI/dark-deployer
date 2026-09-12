# Operator inventories

These files are the compact authoring format introduced for Deployment v3.
They resolve into the existing complete v3 contract; they do not bypass its
validation, planning, rendering, or secret handling.

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha.json \
  --output /tmp/dark-local-ha-resolved.json
venv/bin/python deploy.py plan --inventory examples/operator-inventory/local-ha.json
```

`local-simple` is a single-storage-peer functional deployment. `local-ha` has
two storage peers on one Docker host, so it tests replication but not loss of a
host. `production-five-host` uses documentation ranges and `REPLACE` source
values; replace them before any preflight or installation.
