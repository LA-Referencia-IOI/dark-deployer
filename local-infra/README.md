# dARK local acceptance infrastructure

This laboratory simulates five independent Linux servers with Lima VMs. Each
VM has its own Linux kernel and Docker Engine, making it suitable for testing
the SSH and private-network path without using production hosts.

| VM | Role |
| --- | --- |
| `dark-apps` | APIs, workers, dashboard, and non-validator RPC |
| `dark-blockchain-a` | Validators 01 and 02 |
| `dark-blockchain-b` | Validators 03 and 04 |
| `dark-storage-1` | Kubo and IPFS Cluster peer A |
| `dark-storage-2` | Kubo and IPFS Cluster peer B |

The lab uses a routable `socket_vmnet` network so the VMs can communicate
directly. Addresses are assigned by that network and must be discovered before
generating the inventory; do not invent static addresses.

`lima-up.sh` provisions one temporary base VM and clones it into the five
roles. The base contains no dARK data, IPFS state, Besu state, or secrets.

## Prerequisites

```bash
brew install lima
brew install socket_vmnet
sudo brew services start socket_vmnet
```

The store must be on a local writable volume, not a network filesystem. Lima
keeps VM disks, its SSH identity, and instance state under `STORE/.lima`.

## Create and inspect the lab

```bash
local-infra/lima-up.sh --store /Volumes/Test/dark-lab
local-infra/lima-status.sh --store /Volumes/Test/dark-lab
```

Use `--yes` to create a new store non-interactively. VM sizing is configurable:

```bash
local-infra/lima-up.sh --store /Volumes/Test/dark-lab \
  --cpus 2 --memory 4 --disk 30
```

## Generate the operator inventory

```bash
venv/bin/python local-infra/generate-lima-inventory.py \
  --store /Volumes/Test/dark-lab
```

If the store path does not exist, the setup script asks before creating it;
add `--yes` for unattended setup. The generator writes
`examples/operator-inventory/lima-five-host.json`. This is a compact operator
inventory, not the final v3 contract. Review its discovered addresses, SSH
forwarding, key path, and known-hosts file before running `plan` or `preflight`;
the normal resolver expands it to v3.

To inspect the derived contract without changing a VM:

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/lima-five-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/lima-five-host.json
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/lima-five-host.json \
  --output /tmp/dark-lima-v3.json
```

## Preflight, install, and verify

Preflight proves SSH access, Docker availability, architecture, free disk, and
the private route between the five VMs before changing their dARK runtime:

```bash
venv/bin/python deploy.py preflight \
  --inventory examples/operator-inventory/lima-five-host.json
```

For a new lab chain, run the interactive installer. It offers to reuse or
create a master wallet, use it as the test contract signer, generate managed
application/storage secrets, and initialize the private chain artifact. It
does not write those generated paths back into the operator inventory:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-five-host.json \
  --create-master-wallet \
  --verbose
```

The equivalent unattended invocation is explicit about every identity-changing
decision:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-five-host.json \
  --new-chain \
  --create-master-wallet \
  --use-master-wallet-as-signer \
  --initialize-managed-secrets \
  --non-interactive \
  --yes \
  --verbose
```

This is the first mutating dARK command: it writes runtime data and starts
containers in the VMs. Verify the complete system afterwards:

```bash
venv/bin/python deploy.py verify \
  --inventory examples/operator-inventory/lima-five-host.json
```

## Recovery test

Stop one storage host and verify that the failure identifies that peer:

```bash
LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl stop dark-storage-2
venv/bin/python deploy.py verify \
  --inventory examples/operator-inventory/lima-five-host.json
```

Start it again, then resume the deployment and verify recovery:

```bash
LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl start dark-storage-2
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-five-host.json \
  --resume
venv/bin/python deploy.py verify \
  --inventory examples/operator-inventory/lima-five-host.json
```

If the deployer or rendered configuration changed after an interrupted apply,
add `--refresh-bundle` to regenerate only the public bundle before resuming.
It does not remove managed inputs or persistent service data.

## Day-to-day VM control

To access, stop, or restart a lab VM:

```bash
LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl shell dark-apps
LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl stop dark-storage-2
LIMA_HOME=/Volumes/Test/dark-lab/.lima limactl start dark-storage-2
```

VM disks are not removed when stopped. `lima-down.sh` stops the lab only; it
does not provide a destructive cleanup command. The inventory carries storage
and network decisions. Do not edit a conceptual storage file with live
addresses as a substitute for the generated deployment inventory.
