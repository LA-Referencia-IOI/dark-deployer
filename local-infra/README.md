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

## Two-site variant

`lima-two-site-up.sh` is a separate five-VM laboratory. It preserves the
original lab and uses `dark-two-site-*` instance names, so both can coexist in
one store. Its distribution is intentionally asymmetric:

| Site | VM | Role |
| --- | --- | --- |
| site-a | `dark-two-site-apps` | Apps, RPC, resolver, dashboard, proxy |
| site-a | `dark-two-site-blockchain-a` | Validators 01 and 02 |
| site-a | `dark-two-site-storage-1` | Kubo and IPFS Cluster peer A |
| site-b | `dark-two-site-blockchain-b` | Validators 03 and 04 |
| site-b | `dark-two-site-storage-2` | Kubo and IPFS Cluster peer B |

The script assigns `site-a-lan` and `site-b-lan` addresses to local peers and
creates a WireGuard `wg0` mesh for cross-site traffic. The generated operator
inventory selects the local LAN for same-site Besu/IPFS peers and `mesh-vpn`
for cross-site peers. `socket_vmnet` is only WireGuard's transport underlay and
the SSH-forwarding network; dARK does not select it as a service network.

Unlike the original Lima lab, this variant switches only its own guests from
the rootless Docker supplied by Lima's template to Docker Engine's rootful
daemon. Rootless Docker cannot publish the same P2P port on both a site-LAN IP
and `wg0`; rootful Docker preserves those two explicit bind addresses. The
setup also stops the template's user-owned rootless daemon and restores the
masked `containerd` service required by the rootful engine.

This validates the important routing decision and the encrypted cross-site
path. It does not emulate physical isolation of two data centres: Lima still
provides one shared transport segment below the two logical site-LAN address
ranges. A routing/firewall appliance or distinct host networks is required to
test loss or isolation of that underlay.

```bash
local-infra/lima-two-site-up.sh --store /Volumes/Test/dark-lab
venv/bin/python local-infra/generate-lima-two-site-inventory.py \
  --store /Volumes/Test/dark-lab
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/lima-two-site-five-host.json
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/lima-two-site-five-host.json \
  --create-master-wallet --verbose
```

The generator requires `--store` and refuses to emit an inventory unless each
guest has its expected site-LAN and WireGuard addresses.

Stop only this variant with `local-infra/lima-two-site-down.sh --store PATH`.
To delete its five guests and its base image, while retaining the original lab,
use `local-infra/lima-two-site-clean.sh --store PATH`; it asks for confirmation.

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

## Instantiate a maintained inventory for a real site

`instantiate-inventory.py` turns any supported inventory (compact operator
inventory or v3) into a site-specific file for a real deployment. Unlike the
Lima generator it discovers nothing and assumes no topology: it derives its
questions from the input document, asks only for the site facts the deployer
consumes, and writes a new file validated through the same code path the CLI
uses. The input is never modified, and nothing is written unless the result
validates.

```bash
venv/bin/python local-infra/instantiate-inventory.py \
  --input examples/operator-inventory/dark2-prod-aws.json \
  --output deployment-aws-active-six.json
```

Required arguments:

- `--input PATH`: source inventory (compact operator inventory or v3).
- `--output PATH`: instantiated inventory to write; it refuses to replace an
  existing file unless `--overwrite` is passed.

Optional flags:

- `--known-hosts PATH`: where to write the collected SSH host keys (default:
  a `<output>-known_hosts` sibling of the output).
- `--skip-keyscan`: do not collect host keys over SSH.
- `--defaults-only`: accept every proposed value without asking; implies
  `--skip-keyscan`.
- `--overwrite`: allow writing over an existing output file.
- `--dry-run`: rehearse the questionnaire and validate, but write neither the
  inventory nor the known-hosts file; implies `--skip-keyscan`.
- `--json`: emit a machine-readable summary on stdout (prompts and progress
  go to stderr).

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

VM disks are not removed when stopped. `lima-down.sh` stops the lab only. To
permanently remove the six managed instances while keeping the store directory
itself, use the explicit cleanup script:

```bash
local-infra/lima-clean.sh --store /Volumes/Test/dark-lab
```

It asks for confirmation and removes only `dark-apps`, `dark-blockchain-a`,
`dark-blockchain-b`, `dark-storage-1`, `dark-storage-2`, and `dark-lab-base`.
Use `--yes` only for an unattended cleanup. The inventory carries storage
and network decisions. Do not edit a conceptual storage file with live
addresses as a substitute for the generated deployment inventory.
