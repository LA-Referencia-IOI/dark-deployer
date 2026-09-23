# Problem: two simultaneously active Resolver API instances (multi-site)

**Date:** 2026-09-15
**Status:** implemented in the operator resolver; offline validation complete
**Scope:** `deployment_v3/inventory_resolver.py`, `deployment_v3/operator_schema.json`,
`deployment_v3/render.py`, `components/dark-store-api`, and the operator tests.

## Problem statement

In `examples/operator-inventory/aws-active-two-site-nine-host.json` (asymmetric
two-site production: AWS active, site-b preservation), the operator wants the
Resolver to exist as **two simultaneously running, both functional instances —
one in each site** — and to decide at any moment which one is actually used
(DNS / public proxy choice). Before this change that was impossible with
operator data alone because `resolver-api` was a **singleton**. The current
inventory now declares and resolves both instances.

What the operator wants, concretely:

- The AWS `resolver` machine keeps its current bundle
  (`resolver-observer` group: `observer01` + `store-api-reader` +
  `resolver-api` + public proxy `resolver-public`).
- The site-b machine `site-b-observer` (today only `observer02`) becomes an
  equivalent bundle: `observer02` + a local read-only Store API reader +
  a second resolver-api instance + a public proxy.
- Both instances run simultaneously and resolve independently (each against its
  local Store API reader; cross-site durability is already provided by the
  IPFS/Cluster P2P mesh). The operator picks which one is published.

## Original limitation (verified 2026-09-15)

These were the blocking facts before the implementation:

1. The catalog `catalog_data/dark-platform-baseline-v1.0.json` defines exactly
   one `resolver-api` service instance.
2. `inventory_resolver.py`, the former `storage.api_replicas` handling:
   the deepcopy source is hard-coded to `result["services"]["store-api"]`
   (line ~337). That mechanism could only create Store API copies.
3. `overrides` accepts only `explorer`, `resolver`, `settings`, `components`
   (line ~394). `overrides.resolver.group` *moves* the singleton, it does not
   duplicate it. `overrides.components` merges image/component overrides, not
   services.
4. There is no other operator-side mechanism to declare a new service of type
   `resolver-api`.

## Implemented solution

The implementation separates two concepts: `storage.readers` creates
read-only Store API instances, while `resolver.instances` creates additional
Resolver API instances.

1. `storage.readers` creates `store-api-reader` services with
   `configuration.mode: read_only`, local-site Cluster connections, and the
   `STORE_API_MODE=read_only` runtime setting.
2. `resolver.instances` creates a new Resolver service ID, places it in the
   declared group, binds it to the declared observer/RPC, and requires a
   declared local reader.
3. The existing generic planner, renderer, lifecycle and verification paths
   handle the additional service and proxy by ID. The renderer was explicitly
   extended for Store API mode; Resolver itself remains a generic
   `resolver-api` service.
4. Tests cover the two-site inventory, local readers, observer bindings,
   public proxy routing, and read-only Store API behavior.

## Design notes / semantics

- Two active resolver-apis are semantically coherent: ARK resolution is
  read-only; each instance resolves against its own site-local Store API
  reader and the data converges through the IPFS/Cluster mesh.
- Availability: no objective exists for resolver redundancy
  (`availability.py` `observer_copy` only requires >= 1 Besu observer). At
  minimum document the new shape in the example's `.md` guide and its failure
  table; do not invent new objective enum values as part of this change.
- `verify.py` probes every edge-proxy route, so both public proxies get probed.

## Alternatives considered

- **Move the whole bundle to site-b** (single resolver-api, expressed with
  today's format via `overrides.resolver.group` + moving the proxy + rebinding
  `rpc.bindings` to `observer02`): rejected — only one instance can run.
- **Cold standby** (bundle stays in AWS; failover = edit
  `overrides.resolver.group` and re-apply): rejected by the operator; they want
  both running at once.

## Context an implementing agent needs

- Operator inventory pipeline: `load_inventory()` (`deployment_v3/inventory.py:141`)
  detects `format == "dark-operator-inventory"` and calls `resolve_inventory()`
  (`deployment_v3/inventory_resolver.py:160`, pure/offline) → re-validates with
  `validate_inventory()` → `build_plan` (planner.py) → render → runner/verify.
- The `resolver-observer` bundle pattern = Besu observer + resolver-api + local
  read-only Store API reader (+ optional public proxy) sharing one group on one machine.
- Target example to extend:
  `examples/operator-inventory/aws-active-two-site-nine-host.json` (9 hosts,
  profile production, sites `aws`/`site-b`, 7 validators, quorum 5).
- A previous improvement attempt (third observer on the storage-2 machine) was
  rejected by the operator and fully reverted — do not reintroduce it.

## Verification commands (offline, safe)

```bash
venv/bin/python deploy.py validate --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
venv/bin/python deploy.py inventory-resolve --inventory examples/operator-inventory/aws-active-two-site-nine-host.json --output /tmp/resolved.json
venv/bin/python deploy.py plan --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
venv/bin/python deploy.py inventory-network-matrix --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
python3 -m pytest tests/test_operator_inventory.py -q
```

## Acceptance criteria

1. The example inventory declares (and resolves) two resolver-api instances:
   `resolver-api` in AWS and `resolver-api-site-b` in the
   `site-b-observer` group, each with a local read-only Store API reader and its own
   public proxy.
2. Both instances are bound to their site-local Besu observer via
   `blockchain.rpc.bindings`.
3. `validate`, `plan`, `inventory-resolve` and `inventory-network-matrix` pass
   with no new warnings; full operator-inventory test suite passes.
4. Existing single-instance inventories retain their previous topology and
   behavior. The former `storage.api_replicas` field is intentionally replaced
   by `storage.readers`.
5. The example's `.md` guide and `docs` describe the two-instance shape and the
   operator's "which one is published" decision (DNS / proxy host).
