# dARK Operations and Runbook

## Install

Use Python 3.12, Docker Engine and Compose v2. A maintained compact operator
inventory can be installed directly; copy and offline review commands are
optional.
For production, provision
SSH access, VPN addresses, secret files, chain artifacts and storage paths
before `preflight`. The generated plan applies every declared validator group,
the RPC nodes, observer groups, contracts, storage peers, and finally the
application services in dependency order.

For a compact inventory, install directly:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha.json --verbose
```

If you need to inspect the generated contract before an operational run:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory inventory.json --output resolved-inventory.json
venv/bin/python deploy.py inventory-explain \
  --inventory inventory.json --path /settings/minter/shoulder
venv/bin/python deploy.py plan --inventory inventory.json --json
```

The compact Minter shoulder is set under
`overrides.settings.minter.shoulder`; the maintained examples show `200`.

## Verification

Run `status` for a cheap summary and `verify` for bounded diagnostics. Confirm
RPC block production, every declared validator, Store API health, all storage peers, API
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

### Exact service lifecycle

Use `deploy.py deployments` to list the locally known managed deployments, then
call `stop`, `start`, `restart`, `recreate`, or `remove` with `--deployment ID`
and `--target service:ID`. The deployer resolves the service's machine, group
and Compose project from the reviewed deployment bundle, verifies the matching
Docker Compose labels, and then executes locally or through SSH. `recreate
--build` rebuilds from the current deployed Compose build context; it is the
appropriate operation after a source-code change. `remove` preserves all
bind-mounted data, volumes and networks. These commands intentionally do not
provide automatic data deletion.

Use `deploy.py services --deployment ID` first to query Docker on every managed
local or SSH host. It lists the deployment ID, each exact `service:ID` selector,
its runtime state, type, owner machine, group, functional description and the
lifecycle operations currently valid for it. `--json` is intended for scripts.
The legacy `--inventory FILE` form only locates the deployment ID; the deployed
topology snapshot remains the authority for both listing and mutations.

### Recreate versus apply

Use `recreate` when the deployed topology remains correct and only one
container must be replaced. Run it without `--build` to recreate from the
existing image and deployed Compose configuration:

```bash
venv/bin/python deploy.py recreate \
  --deployment dark-operator-local-ha \
  --target service:explorer
```

Add `--build` after changing source code for a service that has a `build:`
definition. The build context is the checkout path embedded in the deployed
Compose file, so this rebuilds code while preserving the deployed environment,
ports, volumes, secrets and service placement:

```bash
venv/bin/python deploy.py recreate \
  --deployment dark-operator-local-ha \
  --target service:explorer \
  --build
```

Use `--dry-run` before either command to show the resolved deployment, machine,
group, Compose project and file without contacting Docker for a mutation.

Do not use `recreate` to apply a changed inventory. Changes to generated
environment, ports, image declarations, service connections, secret consumers,
placement or networking require inventory review followed by `apply` (or the
full `install` workflow). In short, `recreate` replaces one runtime container;
`recreate --build` also rebuilds its code image; `apply` creates a new deployed
configuration revision.

## Production layout

The normal five-host deployment has apps, blockchain-a, blockchain-b and two
storage hosts. The six-host reference adds a dedicated Resolver host. RPC is
private and available to APIs through the apps network and the explorer reaches
it through the authorized LAN/VPN route. Storage peers share Cluster state,
while each host keeps its own local Docker network. Each machine may run one
`edge-proxy`: the standard gateway publishes Resolver at `/`, Dashboard at
`/admin/`, Explorer at `/explorer/`, and Minter API/documentation under `/api/`.
The six-host reference separates a public Resolver proxy from a LAN/VPN apps
proxy. Direct backend exposure is otherwise avoided.

Every cross-host connection in the inventory names its LAN/VPN and protocol;
the consumer must share that network or declare an existing directional route.
Docker DNS is never assumed between hosts. The renderer emits a
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
