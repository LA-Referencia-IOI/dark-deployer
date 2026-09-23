# Monitoring

Decision record and operating guide for dARK deployment monitoring. The design
discussion lives in the archive
([`docs/old/metrics-prometheus-proposal.md`](old/metrics-prometheus-proposal.md));
this page states what was actually decided and implemented.

## The decision (2026-09): hybrid B+D

Two of the four options proposed were implemented, and together they cover the
monitoring story:

- **D — deployer-side textfile export.** `./deploy.sh metrics-export
  --deployment <id> --output <file> [--include-disk]` samples every machine
  through its Docker daemon and writes a Prometheus
  [textfile-collector](https://github.com/prometheus/node_exporter#textfile-collector)
  `.prom` file (`deployment_v3/metrics/prometheus.py`, `collect_to_textfile`).
  `--include-disk` additionally runs `docker system df` and emits
  `dark_docker_disk_*` metrics (host filesystems still require node-exporter).
- **B — the `dark-monitoring` stack.** A self-contained Prometheus stack in
  `components/dark-monitoring/` (verified versions in its `docker-compose.yml`):
  Prometheus `v3.5.0`, blackbox-exporter `v0.27.0`, node-exporter `v1.9.1`,
  cAdvisor `v0.52.1`, Grafana `12.1.1`, with dashboards and an anonymous viewer.

Options A (a cached HTTP exporter embedded in the deployer) and C (Prometheus
remote-write into the deployer) were **not** implemented; the textfile contract
is the bridge between the deployer and the stack.

## How the integration works

Targets are derived from the **applied deployment snapshot**, not a
hand-written host list:

```bash
python3 components/dark-monitoring/install.py \
  --deployment-snapshot .generated/deployment-v3/<deployment-id>/bundle/shared/deployment-topology.json
```

What `install.py` does with a snapshot (all verified in
`components/dark-monitoring/install.py`):

1. Runs `scripts/generate_v3_targets.py --snapshot <file> --output-dir
   generated/` to produce the Prometheus target files from the applied
   topology.
2. If it finds the deployer two levels up (`deploy.py`), it also runs
   `metrics-export --deployment <id> --include-disk --output
   generated/dark.prom` so the textfile collector has data from the first
   scrape.
3. Generates `generated/compose.networks.json` and validates/starts the stack
   (`--no-start` generates and validates only).

The legacy path (no `--deployment-snapshot`) falls back to `.env` discovery
via `scripts/generate_targets.py`.

## Operating sequence

1. Apply a deployment (`./deploy.sh install` or the remote
   `prepare`/`push`/`apply` cycle).
2. Run `install.py --deployment-snapshot <applied bundle>/shared/deployment-topology.json`.
3. Open Grafana and check the dashboards; disk metrics appear when the
   snapshot install was run with disk support and the
   `dark-resources.json` dashboard (`dark_docker_disk_*` panels) is loaded.

The read-only `metrics` TUI (`./deploy.sh metrics`) and `metrics-export` cover
ad-hoc inspection without the stack.

See also: `components/dark-monitoring/README.md` (component reference), the
[README metrics section](../README.md), and the archived decision record.