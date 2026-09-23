# docs/old/ — historical archive

This directory holds dated analyses, implemented proposals, and closed
incident reports. They preserve the reasoning and the evidence behind
decisions, but they are **not current instructions**: where an archive
document and the code disagree, the code wins. Anything still open that an
archive document describes lives in [`../known-issues.md`](../known-issues.md).

Canonical, maintained documentation lives one level up:
[`../README.md`](../README.md) is the index; the runbook is
[`../operations.md`](../operations.md).

## Renames recorded here (2026-09-23)

- `DARK_2.0_ARCHITECTURE.md` → [`../platform-architecture.md`](../platform-architecture.md)
- `DARK_2.0_GUIDE.md` → [`../sdk-guide.md`](../sdk-guide.md)
- `DARK_2.0_API_REFERENCE.md` → [`../api-reference.md`](../api-reference.md)
- `history.md` → superseded by [`../README.md`](../README.md) (this index's
  sibling); the chronological log itself is kept below.

## Archived in 2026-09 (documentation overhaul)

| Document | Archived because |
| --- | --- |
| [`deployment-v3-operator-inventory-proposal.md`](deployment-v3-operator-inventory-proposal.md) | Proposal fully implemented and operationally duplicated by `deployer.md`; its own header says so. Its "immutable catalog per ID" principle did not hold in practice (branches changed within the same ID). |
| [`metrics-panel.md`](metrics-panel.md) | Dated record (2026-09-16); `metrics/prometheus.py` and `metrics-export` landed two days later. Live content moved to `../monitoring.md` and the runbook. |
| [`metrics-prometheus-proposal.md`](metrics-prometheus-proposal.md) | The decision it proposed was taken and implemented (hybrid B+D); recorded in `../monitoring.md`. |
| [`resolver-api-two-active-instances-problem-2026-09-15.md`](resolver-api-two-active-instances-problem-2026-09-15.md) | Problem solved and verified (`resolver.instances`/`storage.readers`); the format is documented in `deployer.md`. |
| [`ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md`](ipfs-private-swarm-bitswap-replication-diagnosis-2026-09.md) | Closed incident (2026-09-14, Bitswap peer-notify bug + canary fix); open items live in `../known-issues.md`. |
| [`ipfs-private-swarm-bitswap-replication-review-2026-09-14.md`](ipfs-private-swarm-bitswap-replication-review-2026-09-14.md) | Companion audit of the above diagnosis; same closed status. |
| [`aws-single-az-five-host-review-2026-09-14.md`](aws-single-az-five-host-review-2026-09-14.md) | Dated review: two findings already superseded by the code; the two live ones are in `../known-issues.md`. |
| [`examples-inventory-review-2026-09-14.md`](examples-inventory-review-2026-09-14.md) | Its example inventory was outdated; the three open findings are in `../known-issues.md`. |
| [`per-machine-bundle-implementation-plan.md`](per-machine-bundle-implementation-plan.md) | Implemented on 2026-09-22 with deviations; the live reference is `../deployment-v3-revisions.md` (deviations listed there). |
| [`documentation-overhaul-proposal-2026-09-23.md`](documentation-overhaul-proposal-2026-09-23.md) | Documentation audit (2026-09-23) that proposed and drove this overhaul; executed the same day. Kept as the evidence record for every verdict in its tables. |
| [`pr-14-review.md`](pr-14-review.md) | Its two pending items (2.1, 2.2) were resolved; the outcome is recorded in `../monitoring.md`. |
| [`history.md`](history.md) | Chronological log, superseded as an index by `../README.md`. |

## Older archive (pre-dating 2026-09-23)

The documents below predate the deployment v3 migration (2026-09-09/12). They
describe a world of `.env` master configuration, `dark-env`/`dark-net`
topology, `examples/deployment-v2/` and standalone component compose files —
none of which exists anymore. They are kept for archaeology only:

- `implementation-history.md`, `dark-technical-reference.md` — consolidated
  history and the v1 technical reference.
- Deployment eras: `decoupled-implementation-plan.md`,
  `decoupled-infrastructure.md`, `modular-installation-redesign-proposal.md`,
  `installer-local-remote-unification-analysis.md`,
  `unified-deployment-v2-implementation-plan.md`,
  `deployment-v2-variable-map.md`,
  `production-single-site-four-server-installation.md`,
  `network-topology-analysis.md`.
- Inventory tooling: `inventory-adaptation-wizard-proposal.md`,
  `inventory-editor-implementation-plan.md`.
- Operational analysis: `deployer-operations.md`,
  `worker-cycle-evidence.md`, `publication-latency-control.md`,
  `worker-workflow-simplification-{proposal,implementation,verification}.md`,
  `worker-queues-and-ipfs-recovery-follow-up.md`,
  `ipfs-metadata-worker-throughput-proposal.md`,
  `ipfs-architecture.md`, `ipfs-concepts-and-dark-store-api.md`,
  `minter-direct-ark-import.md`, `minter-shoulder-policy.md`,
  `minter-signed-client-keys.md`.
- Dated diagnostics: `chain-throughput-analysis.md`,
  `throughput-diagnosis-live-2026-09.md`,
  `diagnostico-full-reconciliacion-2026-09.md`, `timeout-hallazgos.md`.

Archive documents must not be linked from installation steps as if they
described the current state. When a historical decision matters, summarize it
in the live documentation and link it from there.