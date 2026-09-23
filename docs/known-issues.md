# Known issues and open items

This is the single living list of unresolved issues and pending acceptance
work. Everything here was extracted from dated incident reports and review
documents that now live in [`docs/old/`](old/README-index.md) — the archive
keeps the history, this page keeps the *open* items. When an item is fixed,
remove it from here and mention the fix in the commit that fixes it.

Rule of thumb (same as the documentation index): when text and code disagree,
the code wins — defects that belong to the code are tracked here instead of
being documented as behavior.

| # | Issue | Evidence | Origin |
|---|---|---|---|
| 1 | Kubo canary image is still pinned instead of a stable release with the Bitswap fix | `deployment_v3/catalog_data/dark-platform-baseline-v1.0.json:349` pins `ipfs/kubo:master-2026-08-17-a73e8c0@sha256:c11759f5…` (0.44.0-dev). Promote once an upstream release carries the fix (boxo PR #1201, merged 2026-08-14). | [ipfs replication diagnosis](old/ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md) |
| 2 | `pin_error: context canceled` — no owner assigned | `components/dark-store-api/app/backends/ipfs_cluster.py:64` hardcodes `timeout: float = 30.0` with no env override; the streaming response of Cluster's `POST /pins/{cid}` can exceed it. | ipfs replication diagnosis |
| 3 | `verify` does not check `target_replicas` or chain progress | `verify` reports reachability/state only; replication targets and chain height advance are not asserted. | [aws single-az five-host review](old/aws-single-az-five-host-review-2026-09-14.md) |
| 4 | No prolonged acceptance of Cluster/Store over a real application flow | Acceptance criteria 4/5 of the replication incident were only exercised through the manual probe (`scripts/ipfs-bitswap-probe.sh`); the acceptance notebook has no bitswap coverage. | ipfs replication diagnosis |
| 5 | `smoke-test` in dark-ipfs cannot detect a replication failure by construction | `components/dark-ipfs/Makefile` `smoke-test` adds, pins and retrieves through one node pair only — it never performs a remote read, so missing replicas go unnoticed. | ipfs replication diagnosis |
| 6 | Operator-specific absolute paths baked into an example, and example tests with hardcoded data | `examples/deployment-v3/production-six-host.json:290-344` contains `/Users/lmatas/...` secret source paths (with `REPLACE` placeholders); `tests/test_deployment_v3.py:460-462` hardcodes the example-name tuple and `:116-119` fixes `192.168.105.x` multiaddresses. | [examples inventory review](old/examples-inventory-review-2026-09-14.md) |
| 7 | AAAA records: proposed but not implemented | The cost-optimized proposal mentions A/AAAA records; `dark2-prod-aws.yaml` only creates `Type: A` alias records (~lines 584, 591). | aws cloudformation proposal |
| 8 | Physical two-site VPN acceptance pending | `format_version: 3` multi-site is implemented and validated offline; the physical two-site acceptance described in `docs/multi-site-lan-vpn-proposal.md` has not been run. | multi-site proposal |
| 9 | `dashboard-redis` survives as a legacy service type | The type is still allowed and planned (`deployment_v3/inventory.py:33`, `deployment_v3/planner.py:20`) and described as "Redis heredado; retirar durante la migración" (`deployment_v3/runner.py:912`), but nothing rejects it. Remove the type during the migration. | docs audit 2026-09 |