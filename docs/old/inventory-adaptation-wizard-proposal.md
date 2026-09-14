# Dynamic Inventories and Guided Wizard Implementation Plan

Status: implementation plan. Delivery is split into two gated phases. Phase 2
must not start until Phase 1 passes. The wizard only authors an inventory: it
never deploys, contacts Docker/SSH/Git, reads secret contents, or provisions
infrastructure.

## Fixed decisions

- Introduce `dark-operator-inventory` format version 2. Reject v1 explicitly;
  do not reinterpret it or add implicit compatibility.
- Keep the versioned platform-baseline catalogue, but turn it into a parameterized recipe.
- A blockchain group contains a configurable `validator_count >= 1`. Groups
  are placement/failure-domain units and may have different counts.
- Support one or more validator groups, observer groups, RPC nodes, and storage peers. Each
  storage peer generates one Kubo plus one IPFS Cluster service.
- Placement remains the only group-to-machine mapping.
- Generate deterministic IDs in declaration order: `validator01...`,
  `rpc01...`, and storage services derived from peer IDs.
- Use maintained example paths or `inventory.json` in all documentation.

# Phase 1 — Evolution of inventories

## Operator v2 contract

Add a v2 schema whose blockchain section contains `validator_groups` keyed by
group ID with `validator_count`, plus `rpc_nodes` keyed by RPC ID with a group.
Storage accepts an arbitrary peer map and replication policy. Placement maps
every logical group to a declared machine; machine IDs are not duplicated in
blockchain or storage sections.

Validate: at least one validator group/RPC/storage peer; validator count is a
positive integer; references and IDs are valid and unique; placement is
complete; selected networks exist and are shared or directionally routed;
`1 <= publish_after_replicas <= target_replicas <= peer_count`; proxy listeners
do not collide and routes target recipe services; secret fields are references.

## Catalogue and resolver

- Stop deriving the catalogue from a fixed production fixture. Split it into
  singleton application templates, repeatable validator/RPC/Kubo/Cluster
  templates, components, images, settings, ports, connections, and secrets.
- Resolve defaults, machines, networks, and placement first. Then generate the
  requested validators per group, RPC services, storage pairs, Store edges,
  groups, singleton applications, routing, required private exposures, proxy
  services, and secret consumers.
- Preserve deterministic ordering, catalogue digest, warnings, and provenance
  for every generated group/service/endpoint.
- Remove assumptions about four validators, one RPC, two peers, or fixed IDs.

## Complete v3 and runtime

- Replace fixed cardinality checks with relational checks: every blockchain
  node has exactly one Besu service; every Kubo peer has one Cluster partner;
  Store covers all peers; replication respects actual peer count.
- Make planner dependencies and RPC selection cardinality-independent.
- Generate keys, QBFT genesis, static nodes, bootstrap data, and manifests for
  arbitrary nodes. Allocate deterministic P2P ports and reject host collisions.
- Replace fixed chain export roles with `chain-export --group GROUP_ID`.
- Manifest entries include chain ID, node, validator flag, group, public key,
  P2P endpoint, and checksums; verification rejects missing or extra nodes.
- Generalize renderer, Compose, Kubo/Cluster bootstrap, firewall suggestions,
  readiness, install/resume, and verify to actual generated service sets.

## Availability analysis

Add one pure analyzer shared by validate/plan/wizard. For the total validator
set, calculate QBFT quorum and validator-failure tolerance according to the
deployed Besu rule. Separately simulate loss of each validator group and each
machine, because several validators may share one failure domain. Analyze RPC
and storage failure domains too. Local/lab produce warnings; production goals
must be met or carry an explicit typed acknowledgement override.

## Phase 1 tests and gate

Test groups containing 1, 2, 3, and 5 validators; unequal groups; 1–3 RPCs;
1, 2, 3, and 5 storage peers; shared/separate machines; LAN/VPN/routes/NAT;
replication boundaries; proxies; deterministic IDs/ports; genesis with 1, 2,
4, and 8 validators; arbitrary group export; manifests; rendering and failure
analysis. Add local minimum, local HA, production, unequal-validator,
three-storage, multiple-RPC, and dedicated-Resolver examples.

The gate requires all dynamic examples to pass `validate`, `plan`, `render`,
and `preflight`; a controlled local minimum must pass `install` and `verify`.
Code search must show no operational fixed-node or fixed-role assumptions.

# Phase 2 — Guided wizard

## Public command

Support mutually exclusive entry modes:

```bash
venv/bin/python deploy.py inventory-wizard --new --output inventory.json
venv/bin/python deploy.py inventory-wizard --template operator-production --output inventory.json
venv/bin/python deploy.py inventory-wizard --inventory inventory.json
```

`--output` is required for new/template, forbidden for in-place adaptation, and
must not overwrite. Require TTY/Textual. Reject operator v1 and complete v3
with distinct guidance. Do not add answer files or non-interactive mode yet.

## Question engine

Replace the JSON-block prototype. Build toolkit-neutral `WizardSession`,
`Question`, `Answer`, `DecisionGraph`, `InventoryBuilder`, and
`ConsequenceAnalyzer`. Questions carry title, detailed explanation, operational
consequences, typed input, choices, validation, and visibility condition.
Changing an answer invalidates only dependent answers. Back navigation retains
valid answers. Every completed topic builds/resolves transactionally; failure
keeps the previous valid draft and identifies the responsible answer.

## Conditional question sequence

1. New/template/adapt; local/lab/production; minimum/recommended/custom. In
   adapt mode ask which topics to change and preserve all others.
2. Deployment ID/label, new/existing chain, chain ID, cluster name, and desired
   availability (functional, validator/group/machine loss, storage loss,
   custom).
3. Machine count/names/execution. Ask management, SSH, paths, networks, and
   addresses only for relevant remote machines; allow shared defaults.
4. Blockchain group count, independently configurable validators per group
   (one or more), placement, RPC count/placement/public intent, P2P network,
   NAT/advertised values, and existing artifact reference. After each answer
   show total validators, quorum, and effects of losing each group/machine.
5. Storage peer count/placement, publish and target replication, P2P/API
   networks, NAT/advertised values. Constrain replication to peer count and
   show peer- and machine-loss durability.
6. Apps placement, shared/dedicated Resolver, Explorer, Dashboard, and supported
   advanced placement. Explain inseparable service sets.
7. LAN/VPN definitions and traffic-class networks. Ask directional routes only
   where endpoints lack a shared network; skip cross-host questions on one host.
8. Public services, proxy placement/listener, HTTP/local/external TLS, hosts,
   origins, paths/upstreams/order. Skip all when nothing is public. Detect
   collisions/shadowing and explain Dashboard path effects on assets, redirects,
   sessions, and cookies.
9. Artifact/secret reference roots and supported advanced overrides. Never ask
   for or read secret values.

Other mandatory pruning: local skips SSH; external TLS skips certificates;
existing chain skips genesis; no NAT skips advertise values; shared networks
skip routes; one storage peer fixes replication to one; recommended mode hides
advanced overrides. Explain whenever an earlier change discards answers.

## Textual experience, review, and save

Show one question per screen with explanation, allowed values, consequences,
typed control, inline errors, Back/Continue/Cancel/Review, dynamic progress,
and an optional live architecture summary. Do not expose editable JSON. A
read-only resolved JSON preview is allowed only in advanced final review.

Review must show answers; semantic diff; machines/groups/services; validator
counts, quorum and failure domains; RPC/storage placement and replication;
URLs/backends; cross-host connections; networks; exposures/ports/firewall;
warnings and plan phases. Require explicit confirmation. New files are created
only then; existing files use timestamped backup plus atomic replace. Print
suggested validate/install commands without running them.

## Phase 2 tests and gate

Test question visibility for every profile, unequal validator counts, back and
dependency invalidation, availability explanations, defaults, topic-preserving
adaptation, one-question TUI, inline validation, dynamic progress, mandatory
review, confirmation, cancel-without-write, atomic backup, and guards proving no
infrastructure or secret access.

The gate requires users to create without editing JSON: one-host local,
arbitrary validator-group sizes, multiple RPCs, arbitrary storage peers, routed
production, and dedicated Resolver/proxy layouts. Every result must pass the
Phase 1 validator and planner.

## Required implementation order

1. v2 schema and fixtures; parameterized catalogue; resolver and v3 validation.
2. Dynamic artifacts/chain CLI; planner/renderer/network/Compose/verify.
3. Availability analyzer, migrated examples/docs, then pass Phase 1 gate.
4. Remove JSON wizard prototype; implement question graph and inventory builder.
5. Implement new/template/adapt sessions and Textual conditional flow.
6. Implement review/persistence, full safety/TUI tests, then pass Phase 2 gate.
