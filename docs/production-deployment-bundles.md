# Production deployment bundles

This document describes the supported first production release: one site, one
Blockchain host, one Apps host, and two independent IPFS hosts. The VPN is the
application trust boundary. IPFS tolerates one server failure; Blockchain,
Apps, and the entire site remain explicit single points of failure.

## 1. Prepare the branch configuration

Configure the deployer and component branches in `.env`. Production validation
requires the current deployer checkout to match `DEPLOYER_BRANCH`; components
are cloned or updated from their corresponding `*_REPOSITORY_BRANCH` values.

```ini
DEPLOYER_BRANCH=codex/production-deployment-readiness
PRODUCTION_MINTER_REPOSITORY_BRANCH=codex/production-deployment-readiness
PRODUCTION_IPFS_REPOSITORY_BRANCH=codex/global-ipfs-cluster
```

There is no component commit lock. Moving a configured branch changes the code
that a later installation or `rebuild --pull` receives.

## 2. Create the inventory

```bash
cp deployment-inventory.example.json deployment-inventory.json
```

Edit the copied file in one administrative checkout. Configure:

- `network.vpn_cidr`: the private network containing all service addresses;
- `network.trusted_minter_clients`: exactly one client address or CIDR;
- `signing.platform_key_target`: the absolute path used on Blockchain and Apps;
- the two IPFS secret target paths;
- management names and VPN addresses for all four hosts;
- optional advertised Apps URLs.

The supported role shape is exactly:

| Host count | Role | Default VPN ports |
| ---: | --- | --- |
| 1 | `blockchain` | RPC `8545`, WebSocket `8546` |
| 1 | `apps` | Admin `8000`, Minter `8001`, Resolver `8002`, Store `8003`, Dashboard `8081` |
| 2 | `storage-node` | Kubo/Cluster `4001`, `5001`, `9094`–`9096` |

Cluster targets both peers, while Store API confirms one pinned copy during a
peer outage so minting remains available. Metadata reconciliation retains the
database payload and restores the second copy after recovery.

## 3. Validate and render

```bash
python3.12 install.py deployment validate \
  --inventory deployment-inventory.json

python3.12 install.py deployment render \
  --inventory deployment-inventory.json \
  --output dist/dark-site-a-1
```

The output is deterministic and contains:

```text
manifest.json
shared/storage-topology.json
shared/deployment-endpoints.json
shared/firewall-policy.json
hosts/<host-id>/host.json
hosts/<host-id>/.env.public
CHECKLIST.md
```

Every host configuration records the deployment, inventory, topology,
environment hash and deployer branch. Rendering refuses a non-empty output
directory. This prevents silently mixing two bundles.

The bundle is public configuration. It contains target paths for secrets, but
not key contents, passwords, private swarm material, or signer material.

## 4. Provision secrets outside the bundle

Generate the platform signer once in an approved secret-management workflow.
Install the same raw 32-byte hexadecimal key on Blockchain and Apps at the
configured absolute path:

```bash
sudo install -d -m 0700 /opt/dark-deployer/secrets
sudo install -m 0600 /approved/source/platform.key \
  /opt/dark-deployer/secrets/platform.key
```

Generate the Kubo swarm key and the separate Cluster secret once. Install both
on both IPFS hosts at their configured paths with mode `0600`. Never install
the platform key on an IPFS host and never place any secret in `dist/`.

The shared signer is intentional in V1. It funds genesis, deploys contracts,
and supplies the existing Admin and Minter component variables. The installer
derives its public address, includes only that address in the Blockchain
handoff, and rejects a different key on Apps.

## 5. Copy and inspect each host bundle

Copy the entire public release directory to each host, plus only the appropriate
secret files through a protected channel. On every host, use its own config:

```bash
python3.12 install.py host validate --config /approved/release/hosts/<host-id>/host.json
python3.12 install.py host plan --config /approved/release/hosts/<host-id>/host.json
```

`host validate` verifies public file hashes and secret file permissions. `host
plan` is read-only and prints no secret values.

Apply the generated configuration only from a checkout on the recorded branch:

```bash
python3.12 install.py host apply --config /approved/release/hosts/<host-id>/host.json
```

The command materializes `.env` and `storage-topology.json`, then runs the
non-interactive role installation. Every component follows the branch recorded
in the generated `.env`.

## 6. Deployment order

1. Apply the Blockchain host. The installer funds the platform signer in
   genesis, deploys contracts, and writes public `.env.integration`.
2. Copy only `.env.integration` from Blockchain to the deployer root on Apps.
3. Apply the first IPFS host, followed by the second IPFS host.
4. Run `python3.12 install.py storage audit` and confirm two peers.
5. Apply Apps. The installer verifies `DARK_PLATFORM_ADDRESS` against its local
   platform key before generating Admin and Minter environments.
6. Collect `host status --json` from all hosts as the deployment receipt.

There is no `.env.integration.secrets` in the new Production flow. A file with
that name is a legacy handoff and should not be copied for this deployment.

## 7. Network controls

Compose publishes application and Blockchain ports on the inventory VPN
address. Database and cache ports remain on loopback. The generated
`shared/firewall-policy.json` is the minimum ingress contract:

- Admin, Resolver, Store and Dashboard accept the VPN CIDR;
- Minter accepts only `network.trusted_minter_clients`;
- Blockchain RPC accepts the VPN CIDR;
- IPFS and Cluster ports accept the VPN CIDR;
- non-VPN ingress is denied.

The deployer does not mutate the host firewall. Apply the generated policy with
the site's firewall tooling and verify from both trusted and untrusted test
clients. With `MTLS_ENABLED=false`, VPN membership and these firewall rules are
the authentication boundary.

## 8. Acceptance tests

Production is ready only when all of the following pass:

- every host reports the same inventory and topology hashes;
- every installed component is on the branch configured in `.env`;
- no public artifact or log contains a private key;
- Apps and Blockchain ports listen only on their VPN address;
- PostgreSQL, MySQL and Redis are not reachable from the VPN;
- Minter is reachable from the one trusted client and not other VPN clients;
- authority creation, minting and resolver lookup complete end to end;
- the local developer notebooks complete against the advertised Apps URLs;
- stopping either IPFS host preserves minting and metadata resolution;
- after restarting it, `storage reconcile` returns every CID to two copies.

Loss of Blockchain, Apps, both IPFS hosts, or the whole site is expected to
cause an outage in this release. Multi-site and Apps/Blockchain HA are separate
future work.
