# Deployment v3 — bundles and revisions

Reference for the per-machine bundle flow implemented in the deployer
(landed 2026-09-22, commit `52ce8d5`). The design it superseded —
[`docs/old/per-machine-bundle-implementation-plan.md`](old/per-machine-bundle-implementation-plan.md)
— deviated from the implementation in a few places; this page describes the
implemented reality (verified against `deployment_v3/runner.py` and
`deployment_v3/staging_manifest.py`).

## The flow

```
prepare          render + stage an immutable bundle, record a revision
   │
push [--revision]  validate the bundle, transfer it to every machine
   │
apply [--revision]  distribute secrets, activate bundles, start services
```

`install` runs the whole cycle locally (and re-prepares by default); the
explicit `prepare` → `push` → `apply` sequence is the remote form.

## What `prepare` produces

Everything lives under the run root
`.generated/deployment-v3/<deployment-id>/`:

| Path | Content |
|---|---|
| `bundle/` | The current immutable bundle: `shared/deployment-topology.json` (the resolved topology snapshot), `shared/source-evidence.json`, and `machines/<id>/manifest.json` per machine |
| `revisions/<revision-id>/` | One immutable revision record per prepared topology (`revision.json`) |
| `current-revision.json` | Pointer to the current prepared revision |
| `status.json` | Deployment state machine (see below) |

The **revision id** is the SHA-256 of an index document
(`runner.py:_write_revision`) containing `deployment_id`, a
`topology_sha256` (canonical JSON of the resolved inventory),
`source_evidence_sha256`, and the per-machine `manifest.json` digests — so a
revision identifies the exact topology, component sources, and machine
bundles together.

`prepare` is a first-class operation and compiles the contract artifacts
(pinned solc-js, cached by Solidity source hash); it reports
`state: "prepared"` with the revision id.

## What `push` transfers

`push` validates the prepared bundle and transfers **all machines** — there is
no partial transfer. For each machine it verifies the staging manifest
(`verify_machine_manifest`) and stages the machine bundle:

- **SSH machines:** the bundle is transferred to
  `<workspace-root>/.generated/deployment-v3/<deployment-id>/machines/<machine-id>/bundles/<revision>/`,
  the transferred manifest is re-verified on the remote host, and the
  machine's `current` symlink is switched atomically (temporary symlink +
  `mv -Tf`).
- **Local machines:** the bundle is copied under the run root
  (`local/<machine-id>/machine`).

With an explicit `--revision`, push refuses to run unless the revision exists,
belongs to the deployment, the current `source-evidence.json` hash matches the
revision record, and every machine manifest digest matches — a stale bundle
cannot be pushed against a named revision. Without `--revision`, it uses the
revision recorded in `current-revision.json`. On success it writes
`state: "pushed"` with `target_revision`.

## What `apply` consumes

- `apply --revision <id>` requires that revision to be **the currently
  prepared one** (`current-revision.json` must point at it); it reuses the
  prepared bundle (resume semantics). Otherwise it fails with
  "run prepare for it first".
- A normal `apply` (and `install`) **re-prepares from current source** unless
  `resume` is set; a `resume` reuses the previous immutable bundle — missing
  source evidence forces a re-prepare. `install --resume --refresh-bundle`
  re-renders the generated public bundle before applying.
- Apply then distributes secrets, distributes chain artifacts, and executes
  the plan phases.

## Fingerprints and idempotent re-apply

`status.json` carries `service_fingerprints` (per service, over the
comparable inputs computed by `runner.py:_service_fingerprint_inputs`) and
`service_inputs`. On each apply, previous fingerprints are compared with the
desired ones: services whose fingerprint is unchanged are reused, changed
services are recreated. Status states recorded along the way: `prepared`,
`pushed`, `secrets_distributed`, `chain_artifacts_distributed`,
`applied`/`running`, plus per-step records.

## Deviations from the original plan

Recorded for traceability (the archived plan doc described a different
sketch):

- Naming: the implemented tables are `SERVICE_COMPONENTS`,
  `COMPONENT_DEPENDENCIES`, and `SERVICE_RUNTIME_ASSETS`
  (`deployment_v3/staging_manifest.py`), not the two tables the plan named.
- Identity is a topology-derived `revision-id`, not a per-machine
  `machine-bundle-id`.
- `push` always transfers all machines.
- Contract compilation and the `.sol` source-hash cache live in `prepare`;
  push therefore needs no Docker on the controller.
- The strict-discipline flag `--prepared-only` from the plan was **not**
  implemented; apply's revision matching rules above are the enforced
  behavior.

## Command reference

| Command | Notes |
|---|---|
| `./deploy.sh prepare --inventory <file>` | Render, stage, and record a revision |
| `./deploy.sh push --inventory <file> [--revision <id>]` | Validate and transfer to all machines |
| `./deploy.sh apply --inventory <file> [--revision <id>]` | Activate a prepared bundle (must be current) |
| `./deploy.sh install --inventory <file> --resume --refresh-bundle` | Re-render and resume a local deployment |
| `./deploy.sh status --deployment <id>` | Print `status.json` |
| `./deploy.sh verify --deployment <id>` | Post-apply verification report |