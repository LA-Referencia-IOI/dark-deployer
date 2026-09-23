# Deployment Inventory, Artifacts, and Connectivity

This reference describes the implemented v3 execution path. It complements the
[operator-inventory design](old/deployment-v3-operator-inventory-proposal.md)
(archived) and the practical [operations runbook](operations.md). Code under
`deployment_v3/` and the maintained templates are authoritative.

## Mental model

An inventory is a typed, declarative graph rather than a collection of Docker
environment variables. The compact operator format is resolved first; the
resulting v3 inventory follows the same path as a directly authored v3 file.
The input inventory describes desired intent; the resolved inventory is the
deployment contract; and the applied topology snapshot is the authority for
runtime service operations.

```text
inventory -> load and validate -> groups -> plan -> render
                                              |
                         secrets / chain artifact / sources
                                              |
                                              v
                  preflight -> prepare -> push/apply/install -> verify
```

The inventory captures intent and the plan turns it into ordered work. The
renderer produces reproducible public artifacts. The runner combines those
artifacts with selectively provisioned secrets and executes them locally or
over SSH.

For compact operator inventories, the inspection path is:

```text
compact inventory -> resolve -> explain / network matrix -> validate -> plan
```

`inventory-resolve` writes the complete v3 inventory, `inventory-explain`
summarizes derived placement and service decisions, and
`inventory-network-matrix` shows the selected paths between machines. These
commands are offline inspection operations; they do not apply changes.

## Inventory model

The complete v3 document contains these top-level sections:

| Section | Purpose |
| --- | --- |
| `deployment` | Stable installation ID and label |
| `defaults` | Shared SSH, filesystem, and Docker-network defaults |
| `networks`, `routes`, `machines` | Physical connectivity, existing directional routes, and Docker execution hosts |
| `groups`, `services` | Logical service ownership and typed graph instances |
| `components`, `settings` | Source references and runtime tuning |
| `blockchain`, `infrastructure`, `storage` | Besu, port policy, and IPFS/Cluster configuration |
| `secrets` | Private-source references and authorized consumers |

`deployment.id` scopes generated paths, Compose project names, status, journals,
and bundles. Never reuse it for installations that need independent persistent
data.

Each machine is a Docker daemon with `local`, `ssh`, or `auto` execution.
Remote management addresses are independent from the LAN/VPN addresses used by
services. This distinction lets Lima use forwarded localhost SSH while
containers communicate through guest addresses.

One logical network may contain several routed `cidrs`. A `routes` entry
asserts an existing directional path between domains; the deployer verifies it
from the consumer host but does not configure routers, VPNs, or firewalls.

Groups preserve the five-server operational boundary even in a local HA
simulation where several groups share one Docker daemon. A service has one
unambiguous group. The Minter API, PostgreSQL, and metadata, replication, and
chain workers must share a machine because their payload storage is local.

## Connections, endpoints, and exposure

A service connection names its provider. A connection across machines also
names a declared network and `tcp` or `udp`. The endpoint rules are strict:

- on one machine, consumers receive Docker DNS and an internal port;
- across machines, the provider must expose a matching `private` endpoint,
  and consumers receive the provider machine's address and published port;
  the consumer may reach that network through a declared route;
- a loopback or Docker name is never used as a cross-host endpoint.

Exposures are `none`, `loopback`, `private`, or `public`. Private exposures
state their network. Central infrastructure policy fixes standard API and P2P
ports, allowing validation to reject collisions and unsafe remote routes.

The planner allocates a Docker subnet per daemon from the configured pool,
rejecting overlap with declared LAN/VPN networks. It builds dependency phases:

```text
preflight -> validators -> rpc -> observers -> contracts -> storage -> data
          -> applications -> verify
```

Readiness gates appear between phases, so a consumer cannot start before its
provider is functionally available.

## Rendered public bundle

`render` requires a new or empty output directory and creates a public,
reviewable bundle similar to:

```text
<output>/
  shared/
    deployment-topology.json       # resolved v3 inventory; legacy path
    plan.json
    firewall-suggestion.json
    inventory-resolution.json      # present for compact input
  machines/<machine>/
    machine.json
    compose.yaml
    env/<service>.env
    config/
    groups/<group>/
  manifest.json
```

`shared/deployment-topology.json` is retained for compatibility with older
bundles; it contains a resolved v3 **inventory**, not a deployment-topology
contract. The manifest hashes public artifacts. Secret values and private
artifact fragments are absent.

The deployed bundle is also the authority for runtime service operations.
`deploy.py deployments` lists locally known deployment IDs, and the read-only
`deploy.py services --deployment ID` command uses the selected topology snapshot
to list exact `service:ID` selectors and valid actions on each managed host.
Mutating commands (`stop`, `start`, `restart`, `recreate`, `remove`) resolve from
that snapshot; an inventory can locate the deployment but does not redefine an
active revision.

The renderer translates the graph into only the variables each image needs.
For example, Minter receives database, Store, RPC, chain, and worker settings;
Kubo and Cluster receive their derived peers and bootstrap addresses; Store
receives its storage endpoint file. It does not copy arbitrary inventory JSON
into containers.

The firewall suggestion lists declared service exposures and derived Besu,
Kubo, and Cluster P2P ports. It is an auditable input to firewall automation,
not a command that changes a host firewall.

## Bundles and revisions

`prepare` materializes the rendered bundle into an immutable run root
(`.generated/deployment-v3/<deployment-id>/`) and records a revision whose id
is the SHA-256 of the topology, source evidence, and per-machine manifests.
`push` validates and transfers every machine bundle, switching each machine's
`current` symlink atomically; `apply` activates the currently prepared
revision and service fingerprints make re-applies idempotent. The complete
reference is [deployment-v3-revisions.md](deployment-v3-revisions.md).

The operational sequence is:

```text
prepare -> push -> apply
```

`prepare` renders and stages an immutable bundle, records source evidence and
machine manifests, and creates a topology-derived revision ID. `push` validates
and transfers every machine bundle; it never performs a partial transfer.
`apply` distributes secrets and chain artifacts, activates the prepared
revision, and runs the dependency phases. For local deployments, `install`
performs the complete cycle. `resume` reuses the reviewed immutable bundle;
it does not silently resolve a new inventory.

The revision identity covers the resolved topology, source evidence, and all
machine manifests. Therefore a named revision cannot be pushed when its source
evidence or machine bundle digests no longer match the prepared record.

## Source, secrets, and chain artifacts

Before apply, source acquisition records branch, resolved commit, origin, and
dirty state. The deployer may clone missing components and advances an existing
checkout only with `fetch` plus `merge --ff-only`; it refuses divergent local
work. An SSH host receives controller-validated sources rather than selecting
its own branch.

Secrets have three independent responsibilities: permitted generation,
container mount paths, and runner preflight checks. Generatable runtime secrets
are distinct from operator-controlled master wallet, signer, and existing chain
artifact material. The runner transfers a secret only to declared consumer
hosts and checks its hash and mode.

The blockchain artifact provides a consistent genesis, static nodes, and
role-specific identities. Do not regenerate those values for an existing chain.
`chain-init`, `chain-static-nodes`, `chain-export`, and `chain-verify` are
explicit lifecycle commands, not side effects of inventory resolution.

## Execution modes

For a local inventory, `install` performs validation, acquisition, secret and
artifact preparation, rendering, phased Compose application, and final
verification. For remote inventories, use `preflight`, `push`, and `apply` to
separate inspection, transfer, and mutation. `resume` reuses the reviewed
bundle after a corrected interruption; it does not silently re-resolve with new
catalogue defaults.

```bash
venv/bin/python deploy.py validate --inventory inventory.json
venv/bin/python deploy.py inventory-resolve --inventory inventory.json \
  --output /tmp/dark-resolved.json
venv/bin/python deploy.py inventory-explain --inventory inventory.json
venv/bin/python deploy.py inventory-network-matrix --inventory inventory.json
venv/bin/python deploy.py plan --inventory inventory.json --json
venv/bin/python deploy.py render --inventory inventory.json --output /tmp/dark-render
venv/bin/python deploy.py preflight --inventory inventory.json
venv/bin/python deploy.py prepare --inventory inventory.json
venv/bin/python deploy.py push --inventory inventory.json
venv/bin/python deploy.py apply --inventory inventory.json
venv/bin/python deploy.py install --inventory inventory.json
venv/bin/python deploy.py verify --inventory inventory.json
```

For an already deployed installation, use the applied topology snapshot to
select exact `service:ID` targets. The source inventory locates a deployment,
but it does not redefine the active revision.

## Verification expectations

Verification checks the resolved service graph, not merely container existence.
An operational installation should demonstrate block production, the expected
Besu peers, Kubo and Cluster membership, Store connectivity, API health, the
three Minter workers, and dashboard/explorer reachability through the intended
edge route. Preserve plan, manifest, status, verify, source, and resolution
evidence with the release record.
