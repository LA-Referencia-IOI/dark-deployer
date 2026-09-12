# V3 Lima five-host acceptance test

This laboratory reproduces the production placement with five independent
Linux Docker hosts. It uses SSH between VMs and never relies on Docker DNS
across hosts. It uses a routable `socket_vmnet` network so the VMs can address
each other directly. This requires a one-time privileged setup.

The script provisions a shared `dark-lab-base` image once and then clones the
five VMs from it, so Ubuntu and rootless Docker are installed only in the base
image. The clones begin with the same OS/Docker state and none of the dARK
runtime data.

## Prepare

```bash
brew install socket_vmnet
sudo brew services start socket_vmnet
local-infra/lima-up.sh --store /Volumes/Test/dark-lab
local-infra/lima-status.sh --store /Volumes/Test/dark-lab
venv/bin/python local-infra/generate-lima-inventory.py \
  --store /Volumes/Test/dark-lab
```

The command defaults to the compact production operator example as input and
writes `examples/operator-inventory/lima-five-host.json`. It deliberately does
not generate the final v3 execution contract; normal deployer commands resolve
it through `dark-standard-1`. Both paths can be overridden:

```bash
venv/bin/python local-infra/generate-lima-inventory.py \
  --store /Volumes/Test/dark-lab \
  --input examples/operator-inventory/production-five-host.json \
  --output /tmp/my-lima-operator.json
```

The generated inventory is a local artifact and must be reviewed before use.
It contains the addresses resolved for the five Lima VMs and the SSH key used
by the selected `LIMA_HOME`; the generator points to `LIMA_HOME/_config/user`
and a generated `LIMA_HOME/_config/dark-deployer-known_hosts` file. The latter
is populated only for the five discovered lab addresses and keeps the
deployer's strict SSH checks isolated from the user's general SSH database.
Production inventories must use real VPN addresses and must not
generate or transfer private material.

If the VMs were recreated or their addresses changed, run the generator again
before `validate`/`preflight`; do not keep stale guest addresses in the
inventory.

## Install and verify

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-five-host.json \
  --verbose
venv/bin/python deploy.py verify \
  --inventory examples/operator-inventory/lima-five-host.json
```

For a new chain, the interactive installer offers to create or reuse the
master wallet, use it as the lab signer, generate managed secrets, and create
the private QBFT artifact. These private paths are retained under the
deployment run directory and reused by `--resume`; they are not added to the
operator inventory.

For unattended lab creation, specify every identity-changing decision with
`--new-chain --create-master-wallet --use-master-wallet-as-signer
--initialize-managed-secrets --non-interactive --yes`.

Before `apply`, V3 performs SSH, Docker, architecture, disk and private-address
preflight checks. The functional verifier must additionally prove Besu chain
identity/progress, Kubo swarm membership, Cluster peer membership, Store API
reachability, worker health and dashboard HTTP availability.

To test recovery, stop `dark-storage-2`, run verification (it must fail with a
peer-specific reason), start it again, and rerun verification. A subsequent
`deploy.py install --resume` must reuse completed steps and only revalidate the
uncertain storage steps.
