# dARK blockchain runtime

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
uses VPN addresses recorded in `deployment-topology.json`, while the Docker
`dark-backbone` network is solely a developer HA simulation.
