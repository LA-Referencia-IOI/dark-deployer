# Handoff: recreate and verify the simplified minter workflow

> **Estado: procedimiento histórico de verificación.** Úselo como evidencia
> de pruebas anteriores; para la operación actual prevalecen el código de
> `main` y [`worker-cycle-evidence.md`](worker-cycle-evidence.md).

This document is intentionally a handoff procedure. The implementation changes
the initial schema and has **no database compatibility path**. Do not execute
these commands against a database that must be retained.

## Expected architecture

The minter has exactly three normal workers:

1. `minter-metadata-worker` stores Level 1 and Level 2 metadata.
2. `minter-replication-worker` observes Cluster replication, enables chain
   publication after the publication target, and purges payloads at the
   retention target.
3. `minter-chain-worker` creates or updates the on-chain ARK.

There is no recovery worker and no automatic error queue. A normal temporary
condition is `WAITING`, with a `processing_wait_reason` and `next_action_at`.
Only `FAILED` records need an administrator; `POST /api/v1/worker/retry` is the
explicit mTLS-only action to return one to `READY`.

## Recreate a developer installation

Run these steps only after confirming that local project containers and
volumes may be discarded.

1. From the deployer root, stop the project and remove its named volumes:

   ```bash
   docker compose --project-name dark-apps --env-file .env -f compose/apps.yml down -v --remove-orphans
   ```

   If the normal installer created additional project Compose stacks, remove
   those with their own Compose files as well. Do not use global Docker prune
   commands.

2. Regenerate integration configuration and images through the supported
   installer path:

   ```bash
   python3 install.py rebuild minter --no-cache
   ```

   For a complete fresh local stack, use the normal installation sequence
   instead:

   ```bash
   python3 install.py validate
   python3 install.py
   ```

3. Confirm that Alembic ran against the new empty database and that exactly
   these three worker services exist:

   ```bash
   docker compose --project-name dark-apps --env-file .env -f compose/apps.yml ps
   ```

   Expected services: `minter-api`, `minter-metadata-worker`,
   `minter-replication-worker`, and `minter-chain-worker`. The API is not a
   worker; there must be no `minter-recovery-worker`.

## Verify API contracts

The lightweight endpoint must require only PostgreSQL heartbeats:

```bash
curl -fsS http://127.0.0.1:8001/api/v1/worker/status | jq .
```

It must contain only `metadata`, `replication`, and `chain` under `workers`.
Each one reports `process_state` (`RUNNING`, `SLEEPING`, `PAUSED`, `DOWN`, or
`DISABLED`) separately from workload.

Use full status only interactively:

```bash
curl -fsS 'http://127.0.0.1:8001/api/v1/worker/status?detail=full' | jq .
```

For every worker, verify the following interpretation:

- `workload.<worker>.ready`: can be attempted on the next cycle.
- `workload.<worker>.waiting`: a normal scheduled recheck; it is not an error.
- `next_action_at`: earliest scheduled recheck across that worker's waits. For
  Replication, first-pin checks use 15 seconds, 1 minute and then 5 minutes;
  durability checks use 5 minutes, 15 minutes and then hourly.
- `waiting_reasons`: why the records wait (for example `cluster_pinning`,
  `initial_visibility`, `replica_target`, `storage_backoff`, or `rpc_backoff`).
- `workload.<worker>.failed`: records which need operator review.
- `process_state=SLEEPING`: the process completed a cycle and is polling; it
  does not mean that ready work was lost. `wake_in_seconds` makes the delay
  explicit.

Permanent errors are the only entries returned by:

```bash
curl -fsS 'http://127.0.0.1:8001/api/v1/worker/errors?page=1&page_size=50' | jq .
```

## Exercise the lifecycle

1. Reserve and stage one ARK with metadata.
2. Confirm the record first appears as `METADATA/READY` and then advances to
   `AVAILABILITY/WAITING` or `CHAIN/READY`.
3. While Cluster is still converging, confirm the record remains in
   `WAITING`, has a reason and next action, and is absent from `/worker/errors`.
4. Once the publication replica target is reached, confirm the chain worker
   publishes it and the record becomes `REPLICATION/READY` or `WAITING`.
5. Once the retention target is reached, confirm payloads are purged and the
   record becomes `COMPLETE/DONE`.
6. Induce a clearly permanent failure in an isolated environment, confirm it
   alone is listed by `/worker/errors`, and exercise the mTLS administrative
   retry endpoint only after restoring the dependency.

## Automated checks for the next agent

Use the project Python 3.12 virtual environment. Do not rely on the macOS
system Python. The database-lock suite needs an isolated PostgreSQL URL:

```bash
cd components/services/dark-core-minter-api
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../.venv/bin/python -m pytest \
  tests/test_worker_unit.py tests/test_worker_status_api.py

ARK_LOCK_TEST_DATABASE_URL='postgresql://…/dark_test' \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../.venv/bin/python -m pytest \
  tests/test_ark_exclusion.py
```

Adjust the virtual-environment path if the repository uses a root-level venv.
The critical assertion is semantic, not visual: temporary dependency and IPFS
conditions must become `WAITING`, never errors; `FAILED` must be explicit and
operator-retryable.
