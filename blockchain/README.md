# dARK blockchain runtime

Besu state is stored in the host bind-mount root from `BLOCKCHAIN_DATA_ROOT`.
Each node uses `<root>/<node>` as its complete `/data` directory; keys,
`static-nodes.json` and the database must be in that directory. The deployer
creates and validates this layout before Compose starts. It never falls back
silently to the historical `nodes/<node>/data` layout.

This directory is the versioned Besu runtime used by the deployer. It replaces
the former external blockchain runtime repository.

## Roles

- `rpc`: non-validator `rpc01`, installed with apps.
- `validators-a`: `validator01` and `validator02`.
- `validators-b`: `validator03` and `validator04`.
- `all`: developer-only local simulation of the three roles.

`setup.sh` creates a chain only for `all` or imports the role artefact supplied
by a production deployment. `scripts/start-role.sh` starts exactly one Compose
profile. The artifacts and node keys are deliberately ignored by Git.

## Greenfield chain and artifacts

On a protected initialization workstation, set `BLOCKCHAIN_RUNTIME_ROLE=all`,
run `./setup.sh`, then export the role artifacts with
`scripts/export-role-artifact.sh`. A production host imports only its matching
artifact through `BLOCKCHAIN_RUNTIME_CHAIN_ARTIFACT_DIR`. The deployer refuses
to initialize a role host without that directory.

Artifacts must share the same genesis, chain ID and static nodes. Verify this
before a rollout; never copy node private keys to another host. Production P2P
uses VPN addresses recorded in the deployment inventory, while the Docker
`dark-backbone` network is solely a developer HA simulation.
