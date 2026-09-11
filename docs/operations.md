# dARK Operations and Runbook

## Install

Use Python 3.12, Docker Engine and Compose v2. Create or adapt an inventory,
run `validate`, review `plan`, then run `install`. For production, provision
SSH access, VPN addresses, secret files, chain artifacts and storage paths
before `preflight`. Apply in dependency order: validators-a, validators-b,
apps/RPC and contracts, storage, then application services and dashboard.

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

Inspect the container logs for the affected group and preserve JSON status,
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
hosts. RPC is private and available to APIs through the apps network; the
explorer reaches it through the authorized private/VPN route. Storage peers
share Cluster state, while each host keeps its own Docker network.

## Routine lifecycle and security

Before an upgrade, record `status`, review the inventory and inspect `plan`.
Acquire sources, render, preflight and apply; afterward run `verify` and retain
the manifest. Use `recreate` for one service and `resume` after a corrected
failure. Keep RPC, Kubo, Cluster administration and secret files private.

Metadata and Chain process bounded pages. Replication batches unique CIDs and
prioritizes first pins before durability. Diagnose queue age and rates, not
just depth: a future scheduled wake is healthy, while a growing oldest-wait
age indicates pressure.
