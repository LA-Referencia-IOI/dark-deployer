# Operator Inventory Design

Status: the compact format, resolver, catalogue, templates, inspection
commands, and editor support are implemented. This document explains the
design boundary and the remaining constraints. For day-to-day use, read the
[operations manual](../../OPERATIONS-MANUAL.md).

## Problem and outcome

The full v3 inventory is the execution contract. It explicitly lists machines,
groups, services, connections, component sources, infrastructure policy,
secrets, and settings. That precision is valuable for the runner but forces an
operator to repeat relationships that are fixed by the dARK application model.

The operator inventory is a compact authoring contract. It lets an operator
state the installation's identity, machines, placement, networks, storage,
proxies, and secret sources once. A versioned catalogue expands those decisions
to a complete v3 document before validation, planning, rendering, or execution.

The current terminology is **deployment inventory**. It has two forms:

1. **Operator inventory**: a compact document of decisions and permitted
   exceptions.
2. **Resolved v3 inventory**: the complete execution contract.

Topology is a derived property of an inventory, not the name of either
contract. The generated `shared/deployment-topology.json` path remains only as
a legacy bundle filename.

## Resolution model

```text
operator inventory + immutable catalogue
                 |
                 v
     resolve, validate, and record provenance
                 |
                 v
        resolved v3 inventory + plan
                 |
                 v
     render -> preflight -> push/apply -> verify
```

Resolution is deliberately pure. It does not contact Docker or SSH, acquire
Git sources, generate a chain, or read secret contents. The same input,
catalogue identifier, and resolver version produce the same resolved document.
`inventory-resolution.json` records the input digest, catalogue digest, and
resolver version alongside each rendered bundle.

## Compact contract

The document uses `format: "dark-operator-inventory"`,
`format_version: 2`, and an exact catalogue ID such as `dark-platform-baseline-v1.0`.
Important sections are:

| Section | Operator owns | Resolver derives |
| --- | --- | --- |
| `deployment` | Stable ID and label | Runtime naming |
| `machines`, `defaults` | Local/SSH execution, addresses, paths | Effective machine objects |
| `placement` | Logical group to machine mapping | Service machine assignment and v3 groups |
| `blockchain` | Chain identity and node groups | Besu services and public-node list |
| `storage` | Logical peers and replica policy | Kubo/Cluster pairs and Store relationships |
| `networks`, `routes`, `routing` | Networks, existing directional routes, and traffic choice | Route checks and private exposures |
| `networking` | Optional NAT addresses and translated P2P ports | Advertised endpoints and Compose mappings |
| `proxies` | One proxy per machine, listener, TLS, origin and routes | Explicit gateway services and typed backend connections |
| `access` | Legacy compatibility only | Rejected when combined with `proxies` |
| `secrets` | Controller-side sources | Consumers, destinations, and modes |
| `overrides` | Supported settings/components exceptions | Validated changes to catalogue defaults |

Placement has one authority. Moving `storage-2` changes one mapping; the
resolver recomputes its endpoints, required private ports, and affected plan.
It does not move persistent data.

## Catalogue boundary

`dark-platform-baseline-v1.0` supports dynamic validator and observer groups, multiple RPC nodes, the Minter
stack, Admin, Resolver, Store, dashboard, explorer, and any positive number of
storage peers. Its recipes encode known connections:
for example, Minter API and its three workers use the same PostgreSQL, Store,
RPC, contracts, and local metadata filesystem.

The catalogue is immutable by ID. A default or recipe change requires a new
catalogue ID, so updating the deployer cannot silently alter an inventory. It
does not pin mutable component branches; source evidence records the actual
commits acquired for an installation.

Use a complete v3 inventory when the requested application or service shape is
outside this catalogue. The compact format must not become a generic JSON patch
mechanism for the resolved document.

## Network and proxy rules

The operator chooses a network for each remote traffic class:
`blockchain_p2p`, `storage_p2p`, `storage_api`, and `application_api`.
For each cross-machine dependency, the resolver verifies that the provider has
the selected network and that the consumer either shares it or declares a
directional `routes` entry. It derives the known protocol and private provider
exposure, and rejects collisions. Same-machine dependencies use Docker DNS.

`networking.services` can override a NAT API address/port or a translated P2P
port. These values describe existing network translation; they do not
configure routers or firewalls.

`proxies` expresses requested operator or user access. A proxy declares its
group, listener, TLS mode, site host, public origin and path rules. It is the
only normal application entry point; backends stay unexposed unless a typed
cross-host dependency derives a private listener. The legacy `access` field is
accepted only for old inventories and cannot be combined with `proxies`.

No route turns a service public implicitly. The renderer's firewall suggestion
is evidence for host firewall automation, not an automatic firewall change.

## Storage and chain safety

Each declared storage peer expands to a Kubo and IPFS Cluster service. The
resolved Store API references the derived endpoints; operators do not duplicate
internal connections. `publish_after_replicas` gates publication and
`target_replicas` governs later durability work.

An inventory can reference an existing blockchain role artifact but does not
authorize genesis or identity regeneration. New-chain creation remains an
explicit `chain-init` operation. Secret resolution records paths and consumers,
not private values; preflight and apply require the material at the correct
time.

## Inspection and change review

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory inventory.json \
  --output resolved-inventory.json
venv/bin/python deploy.py inventory-explain \
  --inventory inventory.json --path /services/store-api
venv/bin/python deploy.py inventory-diff \
  --before previous-inventory.json --after inventory.json
venv/bin/python deploy.py plan --inventory inventory.json --json
```

The editor can create and edit either format. It validates a compact section
through expansion before committing it, saves atomically, and creates a
timestamped backup. It neither starts Docker nor reads secret contents.

## Migration guidance

Adopt the compact format first for new installations. An existing v3 inventory
should move only after comparing its effective plan and preserving IDs, groups,
routes, exposures, ports, secret consumers, component choices, and ordering
that affects blockchain artifacts. A conversion that cannot represent one of
those facts without loss must retain v3 instead of discarding it silently.
