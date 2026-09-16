# Prometheus integration for dARK: options for discussion

Status: discussion proposal, revised 2026-09-16. No architecture has been selected.

This document offers ideas to evaluate before extending metrics polling. Its
purpose is to help the person implementing monitoring compare alternatives,
identify missing requirements and propose a suitable design. The options and
possible next steps are suggestions, not an approved implementation plan.
Alternative or combined approaches are welcome if their tradeoffs are documented.

## 1. Starting point and evidence

| Component | Current observations | Limitations |
| --- | --- | --- |
| `scripts/monitor_dark_runtime.py` | Local containers and worker log health; JSONL output | Local execution and name-based selection |
| `scripts/monitor_dark_infrastructure.py` | Docker state/stats, service health, Cluster peers, RPC block height and txpool; JSONL output | Local endpoints and container-name conventions |
| `deploy.py metrics` and `deployment_v3/metrics/collector.py` | Docker samples per deployment machine, locally or through SSH | Interactive panel; no Prometheus export or discovery integration in this component |

The collector executes a non-login `sh -c` program per machine. Its sections cover
daemon information, Docker version, containers, stats, inspect and optional
`docker system df`. It supports bounded parallel sampling and reports failures
and malformed output. The panel derives targets from the managed deployment
snapshot, including `bundle/shared/deployment-topology.json`.

These are observations from repository code. The companion
[metrics-panel.md](metrics-panel.md) records the collector contract, tests and
previous validation. This revision does not establish end-to-end Prometheus
behavior or measure production polling costs. External behavior below is based
on upstream documentation; the chosen approach still needs a pilot.

Application instrumentation in Minter, Resolver and Store is a separate scope.
However, the legacy scripts include health and application observations that
the Docker collector does not replace. Their retirement requires a coverage
comparison, not just a working metrics endpoint.

## 2. Requirements to agree

The proposed common principle is that **observed targets come from the deployed
topology**, without maintaining a second manual host list. Editing an inventory
alone should not imply that the running deployment changed: discovery should
follow the snapshot actually deployed.

Two decisions can be made separately:

- **Discovery:** who determines machines, services, sites and deployment labels?
- **Collection:** who reads each metric, through which connection, and how often?

Keeping discovery in the deployer is compatible with either centralized SSH
collection or direct scraping of exporters. A single controller endpoint is an
additional constraint to discuss, not a requirement common to every option.

Questions for the implementer and operators:

1. Is the objective occasional diagnosis, capacity history, continuous alerts,
   or a combination? Which operational questions should be answered first?
2. Where will Prometheus and the collector run? Is the controller an always-on
   server or an operator's laptop?
3. Which connections are available between controller, sites and Prometheus?
   Is incoming access allowed, or only outbound traffic?
4. Are host exporters acceptable? Who installs, upgrades and operates them?
5. Are Docker figures sufficient, or are remote filesystem capacity, inode usage
   and host resource pressure required for Besu/IPFS machines?
6. What detection delay is useful? Include collection duration, sampling interval,
   scrape interval, rule evaluation and alert waiting periods.
7. Should monitoring cover one deployment or several? How should it handle shared
   hosts, removed machines and changes in service placement?
8. Who owns retention, alert delivery, monitoring availability and credentials?

## 3. Candidate architectures

### A. Controller collector with a cached HTTP exporter

A background process samples machines using the existing collector. An HTTP
endpoint exposes completed samples from a cache; requests do not trigger SSH
collection. Prometheus scrapes the endpoint.

This could suit installations that already permit controller-to-host SSH and
want to reuse the collector without installing host exporters. It needs a
supervised process, bounded sampling, freshness rules and endpoint access
configuration. A slow machine should not block the endpoint or other machines.

Prometheus `up` describes this endpoint, not each remote machine. Collection
success needs separate metrics. Losing the controller interrupts visibility even
if the deployment remains healthy.

Questions to resolve: process location, deployment scope, sampling budget, cache
expiry and topology refresh.

### B. Deployment-derived discovery with host exporters

The deployer generates discovery metadata; Prometheus scrapes exporters directly.
For file-based discovery, the generated file must be delivered to a location
readable by Prometheus and refreshed after deployed topology changes.
See [Prometheus file-based discovery](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#file_sd_config).

This could suit continuous monitoring where independent target health and remote
host visibility matter. `node_exporter` supplies host metrics and cAdvisor can
supply container metrics. They cover different needs; both and multiple endpoints
per machine may be necessary.

The main reuse is topology and identity mapping. Target labels can carry machine
and site information, but mapping container series to dARK services still needs
an explicit Compose-label or relabeling strategy. Shared hosts need a policy
against duplicate collection or misleading per-deployment host totals.

This adds exporter installation, network access and upgrade responsibilities.
The SSH collector can remain useful for interactive diagnosis. If the deployer
stops, scraping can continue with the last discovery file, although topology
updates stop. Exporter reachability alone does not prove application health.

### C. Outbound delivery from the collection side

If inbound access is impractical, consider a supported agent or collector that
forwards samples using remote write. This requires a compatible receiver and
decisions about buffering, retries, delivery failures and credentials. Remote
write is a separate protocol; a text renderer is not a remote-write client.
Reusing the SSH collector requires an adapter or supported bridge.

Pushgateway is another mechanism but should be evaluated separately. Prometheus
recommends it for limited cases, primarily service-level batch outcomes, rather
than general machine monitoring. It retains pushed series until deletion, so a
design needs freshness alerts and explicit cleanup for removed targets.
See [when to use Pushgateway](https://prometheus.io/docs/practices/pushing/).

Outbound delivery avoids incoming access to the controller but still needs an
accessible receiver. With Pushgateway, Prometheus scrapes the gateway, so there
is still a scrape target. A push design does not remove operational dependencies.

### D. Periodic collection with a controller textfile collector

A scheduled job runs the SSH collector and writes a `.prom` file. A
`node_exporter` on the controller exposes that file to Prometheus. A command such
as `deploy.py metrics --textfile` is a possible interface, not an existing command.

This could suit modest collection frequencies and installations already operating
a controller exporter. Write a temporary file and rename atomically to avoid
partial output. File modification time helps detect a stopped writer, but
per-machine last-success timestamps are still needed when a job continues writing
while individual probes fail. The textfile collector does not support explicit
sample timestamps; expose observation time as an ordinary gauge value.
See the [textfile collector documentation](https://github.com/prometheus/node_exporter#textfile-collector).

This avoids a custom HTTP server but needs a scheduler, exporter and scrape
connectivity. Its host filesystem metrics cover the controller only, not remote
machines storing blockchain and IPFS data. Containerized exporters also require
appropriate host access to observe host resources.

### Comparison

| Question | A: cached exporter | B: direct exporters | C: outbound delivery | D: textfile |
| --- | --- | --- | --- | --- |
| Reuse SSH collector? | Yes | Optional diagnosis | Possible with adapter | Yes |
| Network requirement | Prometheus to controller; controller to hosts | Prometheus to each exporter | Collector to receiver; SSH if retained | Prometheus to controller exporter; controller to hosts |
| Additional operation | Collector and HTTP service | Host exporters and discovery delivery | Sender/agent and receiver | Scheduled job and controller exporter |
| Remote filesystem capacity? | Not from current collector | With host exporter | Depends on source | Not from current collector |
| Remote collection health | Explicit probe metrics | Exporter scrape and collector health | Collection and delivery health | Explicit probe metrics |
| Freshness | Per-machine cache age | Scrape timing and source semantics | Collection and delivery age | File age and per-machine age |
| Controller outage | Collection interrupted | Existing targets can still be scraped | Interrupted if controller collects | File remains but becomes outdated |

No option is ranked as the default here. A hybrid is possible: host exporters
for capacity and a small dARK-specific collector for deployment checks, for
example. Its scope should justify any duplicated collection.

## 4. Reuse and implementation boundaries

Topology mapping and a stable identity policy are useful in every option.
Failure and freshness semantics are shared concerns, although their code and
metric names need not be identical across architectures.

The parser, SSH transport and `sample_machines` are directly reusable in A and D,
and potentially C. A renderer from `MachineMetrics` to exposition text can support
A, D and a Pushgateway variant. It is not a prerequisite for B or remote write.
Likewise, a persistent cache is an architecture choice, not mandatory groundwork.

A non-interactive JSON mode could help diagnostics and contract inspection
independently. The implementer could propose it as a small first step if there is
a consumer; it should not delay a discovery-based design.

## 5. Candidate metric contract

Names below are examples for a custom collector, not a committed public API.
Standard exporters should normally retain their native contracts.

| Candidate metric | Meaning and decisions needed |
| --- | --- |
| `dark_machine_probe_success` | Last attempt met a defined success criterion; distinguish connectivity from complete collection |
| `dark_probe_section_success{section}` | Optional bounded set of section outcomes |
| `dark_probe_last_attempt_timestamp_seconds` | Unix time of latest attempt |
| `dark_probe_last_success_timestamp_seconds` | Unix time of latest successful collection; age is `time() - value` |
| `dark_probe_duration_seconds` | Collection duration; requires timing instrumentation |
| `dark_probe_warnings` | Warning count; details belong in logs rather than unbounded labels |
| `dark_docker_daemon_cores`, `dark_docker_daemon_memory_total_bytes` | Capacity seen by Docker, which may describe a VM |
| `dark_deployment_container_memory_bytes` | Sum for measured deployment containers, not total host memory use |
| `dark_deployment_container_cpu_fraction_of_daemon` | Container CPU normalized by daemon core capacity; convert percent to fraction |
| `dark_container_cpu_percent`, `dark_container_memory_bytes` | Sampled values with documented units and source semantics |
| `dark_container_network_bytes_total`, `dark_container_block_bytes_total` | Current collector combines directions; directional series require an extension |
| `dark_container_restart_count_total`, `dark_container_start_time_seconds` | Validate reset and recreation behavior |
| `dark_container_running`, `dark_container_state{state}` | Bounded states; distinguish stopped containers, completed jobs and missing expected services |

Not every row is available directly from the collector. Timestamps, duration and
per-section success may need additional fields. Each proposed metric should be
mapped to its source before its contract is finalized.

Contract questions worth resolving:

- **Partial data:** current aggregates sum available measurements. A smaller sum
  may mean missing data rather than lower usage. Expose completeness or suppress
  incomplete aggregates. Unavailable values should not silently become zero.
- **Freshness:** keep last-attempt health separate from last successful values.
  Decide when old values are omitted. Re-exporting a cached value gives it a fresh
  scrape time, not a fresh observation time.
- **Identity:** use bounded labels such as deployment, machine, site and service.
  Add a stable discriminator if several containers implement one service. Avoid
  per-sample timestamps, raw errors and unnecessary container-ID churn in labels.
- **Counters:** export cumulative values rather than panel-computed rates.
  Validate restarts, recreations and missing samples. Human-readable CLI units
  introduce rounding; use a more precise source if the use case requires it.
- **Lifecycle:** distinguish a failed probe from an intentionally removed target.
  Define cleanup after deployment removal or placement changes.
- **Cadence:** faster scrapes may reveal exporter failure sooner but cannot
  improve slower collection resolution. Sub-minute SSH collection is a
  benchmarking question, not inherently exclusive to host exporters. Expensive
  diagnostics may need a separate slower schedule.

## 6. Operational choices

For the selected option, document the process owner, restart behavior, logs,
deployment selection and topology refresh policy. Scheduled jobs should avoid
overlap; background collectors should bound concurrency and timeouts.

The probe uses a non-login shell. A supervised environment needs explicit paths,
access to the deployment store and suitable credentials. Read-only commands do
not imply that the underlying SSH account or Docker access is read-only.
Scheduled and outbound collectors also need credential access; their process
shape alone does not remove that responsibility.

Endpoint exposure depends on actual placement: Prometheus may be local, on a
private network or across sites. Decide binding, firewall rules, authentication
and transport protection accordingly. Existing exporters and receivers still
need appropriate access controls. Loopback limits network reachability but does
not authenticate local processes.

For a custom exporter, compare a maintained client library with a small renderer
rather than assuming one is required. Consider escaping, duplicate series,
missing values, format validation and dependency cost. Remote write additionally
needs a compatible protocol implementation.

## 7. Suggested way to advance

The implementer could use this sequence, adapting it to the agreed requirements:

1. **Define the first operational questions.** For example: which machine cannot
   be sampled, which service repeatedly restarts, and which data filesystem is
   approaching capacity? Identify which sources answer each.
2. **Propose an architecture.** Record the selected option or combination, why it
   fits connectivity and ownership, and alternatives considered. Include a small
   deployment diagram, collection scope and metric contract.
3. **Build a bounded pilot.** Use one deployment with local and remote machines
   where applicable. Measure collection duration, resource cost and recovery
   before choosing production intervals. Build only the adapters needed.
4. **Review with operators.** Check that missing, partial and old data are
   understandable and that alert timing meets the intended use.
5. **Plan adoption.** Document installation, upgrades, removal and responsibility.
   Compare legacy-monitor coverage before replacing their checks.

The proposal can be accepted, revised or rejected during architecture review.
The discussion issue should record decisions before expanding the work into a
production monitoring service.

## 8. Suggested pilot evidence

Refine these criteria once the design is selected:

- Targets and labels match deployed topology, including shared hosts and placement
  changes, without manual host-list edits.
- Endpoint availability is distinguishable from failed collection, partial data,
  old samples and intentionally removed targets.
- One disconnected machine does not block others; recovery restores current
  measurements without manufacturing zeros.
- Controller/exporter/scheduler restart and receiver failure have documented
  effects on freshness, buffering and visibility.
- Prometheus ingests real samples and representative queries/alerts work.
  Unit tests alone do not establish end-to-end behavior.
- Measured cost and detection latency fit an agreed budget.
- Remote filesystem visibility is demonstrated if required.
- Service, worker, Cluster and RPC checks remain covered or explicitly deferred
  when discussing legacy-monitor retirement.

## Appendix: illustrative freshness query

For a custom collector, expose observation time as an ordinary gauge:

```text
# HELP dark_probe_last_success_timestamp_seconds Unix time of last successful collection
# TYPE dark_probe_last_success_timestamp_seconds gauge
dark_probe_last_success_timestamp_seconds{deployment="example",machine="apps",site="site-a"} 1789552800
```

```promql
# Illustrative threshold; select it from the agreed detection budget.
time() - dark_probe_last_success_timestamp_seconds > 180
```

This detects an aging existing sample. Separate checks are needed for exporter
failure, targets that never produced a successful sample and series that
disappear entirely. Include these cases in the chosen alert contract.
