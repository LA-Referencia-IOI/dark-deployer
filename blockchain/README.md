# dARK blockchain runtime

Besu state is stored in the host bind-mount root from `BLOCKCHAIN_DATA_ROOT`.
Each node uses `<root>/<node>` as its complete `/data` directory; keys,
`static-nodes.json` and the database must be in that directory. The deployer
creates and validates this layout before Compose starts. It never falls back
silently to the historical `nodes/<node>/data` layout.

This directory is the versioned Besu runtime used by the deployer. It replaces
the former external blockchain runtime repository.

## Dynamic nodes and groups

The operator inventory is the only source of truth for validator, RPC and
observer nodes. Validator and observer identifiers are generated from their
declared groups; RPC identifiers are explicit. There are no built-in
`validators-a`, `validators-b`, four-validator, single-RPC or two-storage-peer
roles.

The old fixed Compose file has been removed. `setup.sh`, `reset.sh`, and the
old role scripts return an explicit migration error; none is a supported
deployment interface.

## Greenfield chain and artifacts

Create and export artifacts through the resolved inventory:

```bash
venv/bin/python deploy.py chain-init --inventory inventory.json
venv/bin/python deploy.py chain-export \
  --inventory inventory.json \
  --artifact-root PATH \
  --group GROUP \
  --output PATH
venv/bin/python deploy.py chain-verify \
  --inventory inventory.json \
  --artifact-root PATH
```

`install` distributes each group's private node material to its declared
machine. A group may contain any positive number of validators or observers.
Observers receive their own key and persistent database, participate in P2P,
do not validate, and keep JSON-RPC reachable only where the resolved connection
graph requires it.

Artifacts must share the same genesis, chain ID and static nodes. Verify this
before a rollout; never copy node private keys to another host. Production P2P
uses VPN addresses recorded in the deployment inventory, while the Docker
`dark-backbone` network is solely a developer HA simulation.
