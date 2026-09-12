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

Lima's integrated network supplies VM connectivity without `socket_vmnet` or
additional privileges. Addresses are assigned by the lab network and must be
discovered before generating the inventory; do not invent static addresses.

`lima-up.sh` provisions one temporary base VM and clones it into the five
roles. The base contains no dARK data, IPFS state, Besu state, or secrets.

## Use

```bash
brew install lima
local-infra/lima-up.sh --store /Volumes/Test/dark-lab
local-infra/lima-status.sh --store /Volumes/Test/dark-lab
venv/bin/python local-infra/generate-lima-inventory.py \
  --store /Volumes/Test/dark-lab
```

If the store path does not exist, the setup script asks before creating it;
add `--yes` for unattended setup. The generator writes
`examples/deployment-v3/lima-five-host.json`. Review its discovered addresses,
SSH forwarding, key path, and known-hosts file before running `preflight`.

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
