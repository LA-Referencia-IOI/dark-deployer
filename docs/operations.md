# dARK Operations and Runbook

## Install

Use Python 3.12, Docker Engine and Compose v2. Create or adapt a complete v3 or
compact operator inventory, run `validate`, review `plan`, then run `install`.
For production, provision
SSH access, VPN addresses, secret files, chain artifacts and storage paths
before `preflight`. Apply in dependency order: validators-a, validators-b,
apps/RPC and contracts, storage, then application services and dashboard.

For a compact inventory, inspect the generated contract before an operational
run:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory deployment-topology.json --output resolved-topology.json
venv/bin/python deploy.py inventory-explain \
  --inventory deployment-topology.json --path /settings/minter/shoulder
venv/bin/python deploy.py plan --inventory deployment-topology.json --json
```

The compact Minter shoulder is set under
`overrides.settings.minter.shoulder`; the maintained examples show `200`.

## Verification

Run `status` for a cheap summary and `verify` for bounded diagnostics. Confirm
RPC block production, four validators, Store API health, two storage peers, API
health endpoints and the three Minter workers. The dashboard consumes the
internal status APIs; it does not call worker recovery actions.

## Worker interpretation

`working` means a cycle is executing. `sleeping_until_due` means no record is
eligible before the displayed time. `idle_no_work` means no eligible records
exist. Replication separates `first pins` (records blocking Chain) from
`published awaiting durability` (records already on Chain). Cluster states
`queued`, `pinning` and `initial_visibility` are expected waits. Durability is
deferred while metadata or first-pin work exists.

## Logs and monitoring

Inspect the container logs for the affected service and preserve JSON status,
verification output, bundle hashes and worker-cycle evidence. The lightweight
status endpoint must not perform RPC, IPFS, Store API or ARK-count scans. Full
diagnostics are on demand and bounded. Monitor CPU, memory, SQL latency, Store
API latency, Cluster queue depth and Besu block production.

## Recovery and cleanup

Workers retry transient storage and chain conditions using scheduled next
actions; permanent errors remain visible for administrative review. There is
no Recovery Worker in the current architecture. `recreate` preserves named
volumes and bind-mounted data. Never delete production data automatically;
cleanup of a local experiment must explicitly target only the inventory's
projects and volumes.

## Production layout

The normal deployment has apps, blockchain-a, blockchain-b and two storage
hosts. RPC is private and available to APIs through the apps network and the
explorer reaches it through the authorized LAN/VPN route. Storage peers share
Cluster state, while each host keeps its own local Docker network. The
`edge-proxy` in apps publishes dashboard and explorer over HTTP; direct public
exposure is otherwise avoided.

Every cross-host connection in the inventory names the shared LAN/VPN and its
protocol; Docker DNS is never assumed between hosts. The renderer emits a
firewall suggestion per machine with the declared private/public exposures and
the policy-derived Besu, Kubo and Cluster P2P ports.

Each cross-host connection is declared with its network and protocol in the
inventory; Docker DNS is never used between hosts. The rendered firewall
suggestion includes the declared private/public exposures plus the
policy-derived Besu, Kubo and Cluster P2P ports.

## Routine lifecycle and security

Before an upgrade, record `status`, review the inventory and inspect `plan`.
Acquire sources, render, preflight and apply; afterward run `verify` and retain
the manifest. Use `recreate` for one service and `resume` after a corrected
failure. Keep RPC, Kubo, Cluster administration and secret files private.

Metadata and Chain process bounded pages. Replication batches unique CIDs and
prioritizes first pins before durability. Diagnose queue age and rates, not
just depth: a future scheduled wake is healthy, while a growing oldest-wait
age indicates pressure.
