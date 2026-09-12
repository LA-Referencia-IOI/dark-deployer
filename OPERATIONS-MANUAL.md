# dARK Operations Manual

This is the practical guide for installing, inspecting, operating, and troubleshooting dARK. Start here after reading the short repository overview in [README.md](README.md). Use the supported `deploy.py` interface; do not edit generated Compose files or `.env.integration` files by hand.

## 1. Choose an inventory path

An **inventory** is the source of truth for an installation: machines, placement, networks, components, secrets, blockchain, storage, and access. There are two supported authoring paths.

| Path | Start with | Best for | Trade-off |
| --- | --- | --- | --- |
| Compact operator inventory | `examples/operator-inventory/` | New local and standard five-host deployments | Uses the supported dARK catalogue shape |
| Complete v3 inventory | `examples/deployment-v3/` | Existing v3 installations or a shape outside the catalogue | More fields and relationships to maintain |

The compact format records operator choices. The resolver derives services, typed dependencies, secret consumers, groups, and private exposures, then validates the resulting v3 inventory. The v3 format is already the complete execution contract. Both formats contain secret *references*, never values.

Use a compact template for a new deployment:

```bash
venv/bin/python deploy.py inventory-create \
  --template operator-local-simple \
  --output deployment-inventory.json
```

Available templates are `operator-local-simple`, `operator-local-ha`, and `operator-production-five-host`; their complete-v3 equivalents omit the `operator-` prefix. `local-simple` has one storage peer. `local-ha` has two peers on one Docker host and tests replication, not host-loss tolerance. `production-five-host` is documentation-only until every `REPLACE` value is replaced with real infrastructure values.

## 2. Prepare the controller

```bash
cd /Users/lmatas/source/dark-deployer
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
docker version
docker compose version
```

For remote deployment, the controller also needs SSH access to every declared machine and the private material named by `secrets` and `blockchain.artifact.source`. Keep these files outside Git. The deployer copies each secret only to its declared consumers with the declared mode (normally `0600`); public bundles never contain secret values.

## 3. Inspect before changing infrastructure

The compact inspection commands are offline: they do not use Docker, SSH, Git, or secret contents.

```bash
# Expand compact decisions into the effective v3 contract.
venv/bin/python deploy.py inventory-resolve \
  --inventory deployment-inventory.json \
  --output resolved-inventory.json

# Explain whether a field is explicit, inherited, or catalogue-derived.
venv/bin/python deploy.py inventory-explain \
  --inventory deployment-inventory.json \
  --path /services/store-api

# Compare two inventory decisions semantically.
venv/bin/python deploy.py inventory-diff \
  --before previous-inventory.json \
  --after deployment-inventory.json

# Validate and construct an execution plan without applying it.
venv/bin/python deploy.py validate --inventory deployment-inventory.json
venv/bin/python deploy.py plan --inventory deployment-inventory.json --json
```

`inventory-resolve` refuses to overwrite an existing output. `plan` is the last safe review point: examine group placement, cross-host routes, exposure, and the phases (`validators`, `rpc`, `contracts`, `storage`, `data`, `applications`, `verify`) before installing.

## 4. Configure a compact inventory

### Local functional test

Keep `operator-local-simple` unchanged for the smallest functional installation. If a new local chain is needed, provide a master wallet file (the installer derives its public address and contract signer) or use the guarded wallet-generation option documented by `deploy.py --help`.

```bash
venv/bin/python deploy.py install \
  --inventory deployment-inventory.json \
  --master-wallet-file /secure/dark/master-wallet.key
```

### Local replication simulation

Create `operator-local-ha`. Its five logical groups run on one Docker daemon: `apps`, `blockchain-a`, `blockchain-b`, `storage-1`, and `storage-2`. It proves that two storage peers can replicate; it does not prove that the system survives loss of the single host.

### Standard five-host deployment

Start with `operator-production-five-host`, then replace all sample network addresses, SSH settings, paths, source references, and `access` settings. The normal placement is apps, two blockchain hosts, and two storage hosts. Route traffic intentionally:

```json
{
  "routing": {
    "blockchain_p2p": "vpn",
    "storage_p2p": "vpn",
    "storage_api": "vpn",
    "application_api": "lan"
  },
  "access": {"mode": "gateway", "bind": "public", "port": 8080}
}
```

Same-machine consumers use Docker DNS. A cross-host dependency receives the selected network, TCP, and a derived private provider exposure. The resolver never publishes a dependency publicly merely to make a route work.

### Storage and replication

Declare each logical peer once. The resolver creates its Kubo/Cluster pair and updates Store API relationships.

```json
{
  "storage": {
    "peers": {
      "storage-a": {"group": "storage-1"},
      "storage-b": {"group": "storage-2"}
    },
    "replication": {
      "publish_after_replicas": 1,
      "target_replicas": 2
    }
  }
}
```

`publish_after_replicas` is the availability threshold required before chain publication. `target_replicas` is later durability work. A target cannot exceed the number of peers.

### Controlled overrides

The compact catalogue supplies component defaults and settings. Use only the typed override areas it supports. For example, the Minter shoulder uses the `2MM` form:

```json
{
  "overrides": {"settings": {"minter": {"shoulder": "200"}}}
}
```

Use the complete v3 inventory when the desired service layout is outside the catalogue's fixed four-validator, one-RPC, and one-or-two-peer model.

## 5. Render, preflight, install

For reviewable output without applying it:

```bash
venv/bin/python deploy.py render \
  --inventory deployment-inventory.json \
  --output /tmp/dark-render
```

The output includes plan evidence, one public bundle per machine, a firewall suggestion, and the resolved v3 inventory. Its retained path `shared/deployment-topology.json` is a legacy bundle filename, not the model name; `shared/inventory-resolution.json` records input and catalogue digests.

For remote machines, check prerequisites before mutation:

```bash
venv/bin/python deploy.py preflight --inventory deployment-inventory.json
venv/bin/python deploy.py push --inventory deployment-inventory.json
venv/bin/python deploy.py apply --inventory deployment-inventory.json
```

`install` coordinates validation, acquisition, runtime secret preparation, chain-artifact handling, preflight, rendering, application, and verification. It acquires components with `fetch` and `merge --ff-only`; a divergent or dirty checkout stops rather than being overwritten. Use `--skip-acquire` only for checkouts you intentionally prepared.

```bash
venv/bin/python deploy.py install --inventory deployment-inventory.json
```

For a new chain, use `chain-init` only as an explicit initialization action. Do not regenerate genesis, validator identities, or static nodes for an existing network. See [blockchain/README.md](blockchain/README.md) for the role-artifact workflow.

## 6. Verify and operate

```bash
venv/bin/python deploy.py status --inventory deployment-inventory.json
venv/bin/python deploy.py verify --inventory deployment-inventory.json
```

Confirm block production, the expected Besu nodes, storage peers, API health, and the three Minter workers. `status` is a short summary; `verify` performs bounded diagnostic checks. The functional acceptance path is `notebooks/dark_platform_deposit_lifecycle.ipynb`: reserve an ARK, repeat the request with the same client item ID, complete metadata, wait for `PUBLISHED`, inspect both CIDs, and resolve the ARK.

The depositing application must not wait for IPFS or blockchain inside its web request. It stores the ARK and checks later through its own background task.

To rebuild one service without restarting the rest of its machine:

```bash
venv/bin/python deploy.py recreate \
  --inventory deployment-inventory.json \
  --service dashboard --build
```

## 7. Recovery, evidence, and safety

Inspect the affected group logs before restarting anything. Use `resume` only after correcting the cause of an interrupted remote transfer or apply:

```bash
venv/bin/python deploy.py resume --inventory deployment-inventory.json
```

If Docker reports an overlapping subnet, the deployer lists the conflicting
bridge networks. It automatically reuses only the network with the expected
deployment name and exact subnet. To remove conflicting networks only when they
are empty, opt in explicitly:

```bash
venv/bin/python deploy.py install \
  --inventory deployment-inventory.json \
  --clean-empty-network-conflicts
```

The option never removes a network with attached containers. Change the
inventory subnet or investigate the other deployment when Docker reports an
occupied conflict.

During an interactive `install`, an empty conflict also produces a
`Remove them and continue? [y/N]` prompt. Declining preserves every network and
stops the installation with the exact conflicting name and subnet. `--yes` and
`--non-interactive` suppress that prompt; automation must use the explicit
cleanup option to authorize removal.

Preserve status and verification JSON, the applied bundle hash, genesis and static-node hashes, source-commit evidence, the resolution evidence, and notebook results. A mutable branch is not an immutable release: recorded commit hashes identify what actually ran.

Never remove production volumes as part of routine recovery. For a local trial, stop only projects generated by the selected inventory and keep explicitly excluded external containers intact.
