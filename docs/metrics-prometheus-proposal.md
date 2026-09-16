# A Prometheus probe for dARK: how monitoring works today, and where a probe could live

This document first describes the monitoring that exists in this repository today,
then examines four ways to expose those numbers to Prometheus, for each of them
saying what can be reused from the current work and what cannot. It is a
proposal: nothing described in sections 3 to 7 is implemented.

## Scope of this document

Date: 2026-09-16, on `main`, on top of `ce7c185`. It covers the two legacy monitor
scripts, the read-only metrics panel and its collector, and the design space for a
Prometheus probe. It does not cover application-level metrics inside the dARK
services themselves (minter, resolver, store), which are a separate question.

Method: the description of the current state comes from reading and running the
code in this repository; the Prometheus-side statements come from the documented
behaviour of Prometheus, the Pushgateway, and node_exporter, not from a running
monitoring stack, because no Prometheus exists here to test against. Every claim
is marked **VERIFIED** (evidence reproducible in this repository) or **INFERRED**
(reading or documentation, without direct proof). Where a claim only holds in part,
that is said.

The companion document [`metrics-panel.md`](metrics-panel.md) covers the panel and
the collector in depth: the probe contract, the parser, the failure semantics, the
verification, and what remains unproven. This document does not repeat that detail;
it references it.

## 1. How monitoring works today

### 1.1 The two legacy monitors

`scripts/monitor_dark_runtime.py` and `scripts/monitor_dark_infrastructure.py` are
long-running loops that print one JSON object per sample and optionally append it
to a `.jsonl` file (**VERIFIED**).

| | `monitor_dark_runtime.py` | `monitor_dark_infrastructure.py` |
| --- | --- | --- |
| Observes | Docker containers and worker log health | Docker metadata/stats, service health endpoints, IPFS Cluster peers, RPC block height and txpool |
| Cadence | `--interval` 60 s, `--full-every` 300 s for the expensive worker diagnostic | `--interval` 60 s, `--full-every` 300 s for the full worker diagnostic |
| Output | One JSONL record per sample, stdout and/or `--output` (default `dark-runtime-monitor.jsonl`) | Same, default `dark-infrastructure-monitor.jsonl` |
| Reaches | Only the local Docker CLI and `127.0.0.1` | Only the local Docker CLI and `127.0.0.1` |

Their docstrings are explicit about the intent — *"one JSON record per sample,
making the output suitable for `jq`, spreadsheets, or later incident analysis"* —
and about the constraint they accept: they are *"intentionally independent of the
application"* and *"never start, stop, recreate, or change a container"*
(**VERIFIED**).

Three structural limits matter for what follows. They are **local only**: every
endpoint is hardcoded to `127.0.0.1` and every Docker command runs on the machine
that starts the loop, so a remote deployment cannot be observed at all. They know
**nothing about the deployment**: which containers matter is decided by a name
prefix (`name.startswith("dark-")`), not by the managed inventory, so a renamed
deployment or a second one on the same host is invisible or ambiguous. And they
**duplicate the collection** between themselves, since both call `docker ps` and
`docker stats`.

### 1.2 The metrics panel and its collector

`deploy.py metrics` opens a read-only terminal panel over one managed deployment
(**VERIFIED**, see [`metrics-panel.md`](metrics-panel.md)). Its collector is the
piece that matters here. It samples each machine of a deployment with **one
`sh -lc` program per machine**, executed locally or through the deployment's own
SSH transport, and parses six sections: daemon denominators, docker version,
containers, stats, inspect, and optionally `docker system df`.

Four properties of that collector are the reason this document exists:

* **The targets come from the deployment, not from configuration.** The panel
  lists the deployments this controller knows about and derives the machines from
  the managed plan, which is the topology snapshot that was actually deployed
  (**VERIFIED**: `managed_plan` reads `bundle/shared/deployment-topology.json`).
  Adding a host, renaming a group or moving a service changes the inventory, not
  any monitoring configuration.
* **It carries semantic labels.** Every container sample is joined to its
  `service`, and the plan knows the `machine`, the `group`, the `site` and the
  deployment id, so a series can say *which part of the topology* it belongs to.
* **It is read-only and issues no lifecycle command.** Allowed Docker subcommands
  are declared in `READ_ONLY_DOCKER_SUBCOMMANDS` and enforced by tests.
* **It reports its own failure in detail.** Unreachable machines, failed sections,
  rejected rows and contract drift all become typed states and counted warnings,
  which is what makes a scrape safe to export (see section 5).

### 1.3 What neither of them is

Neither is scrapeable. The only machine-readable output that exists today is the
legacy monitors' JSONL, and it is written on the machine that runs it, one file
per monitor, with no labels beyond the container name and no notion of a
deployment. There is no HTTP endpoint, no exposition format, and no Prometheus
anywhere in the repository (**VERIFIED**: no port-binding code and no Prometheus
text in the tree, outside vendored third-party packages).

## 2. The principle this proposal must preserve

The requirement that shapes every option below: **the probe should happen from or
towards the machine that runs the deployer, and the deployer should be the single
place where the targets are resolved, so that a change in the infrastructure does
not mean reconfiguring the monitoring.**

Today that property already holds for the panel, by accident of its design: the
fan-out over N machines happens *inside* the deployer's process, and the list of N
comes from a deployment. Two consequences follow, and they are worth stating
plainly because they are what the options trade against each other:

* **One ingress, N targets.** Whatever Prometheus talks to, it talks to one
  address: the deployer's host. The fleet is behind it. There is no per-host scrape
  target to declare, and therefore nothing to update when the fleet changes.
* **One address to trust, and one point to lose.** Prometheus's own `up` metric
  will tell you that the deployer's endpoint is alive; it will not tell you that
  the fleet is. Machine health has to be exported as data, not inferred from the
  scrape. And if the deployer's host is down, the whole fleet goes dark in the
  metrics path even if the fleet is fine.

The two readings of "from or towards" are the same requirement seen from either
end: *from* means the deployer's host is where queries originate; *towards* means
it is where queries arrive. Both are satisfied by a single scrape target or a
single discovery source, which is what the four options provide in different ways.

Note also what "depend on a deployment" excludes: a Prometheus configuration that
lists hosts by hand, and equally a monitor that decides what to look at by name
prefix. The deployment is the source of truth for *what* is observed, and this
should survive whichever transport is chosen.

## 3. Four shapes for a Prometheus probe

### 3.1 Option A — the deployer collects and exports

The deployer runs a small HTTP endpoint that serves the Prometheus exposition
format for the whole deployment. A background collector samples the machines on
its own clock; a scrape reads the cached sample and returns immediately.

Prometheus needs to reach one address. Nothing is installed on the fleet, no port
is opened there, and the read-only probe already exists, so this is the option
that reuses the most. The cost is that the deployer's process becomes a
long-running service holding the fleet's SSH keys, and that the SSH fan-out
(N sessions, each of which has to wait for the daemon to produce a `docker stats`
sample rather than answering a lookup) has to be decoupled from the scrape
interval, since a scrape must never trigger a probe.

### 3.2 Option B — the deployer publishes discovery, Prometheus scrapes each host

The deployer writes a Prometheus *file-based service discovery* file — one target
per machine, with the deployment's labels attached — and Prometheus scrapes each
host directly, through a node_exporter or cAdvisor installed on it.

This is the only option where the deployer **does not collect metrics at all**. It
keeps the property that matters most — one place decides what is observed, derived
from a deployment — while removing the SSH fan-out from the metrics path entirely,
and it gives per-host `up`, per-host scrape health and real per-host resolution. It
also closes the gap this project cannot close through Docker, because node_exporter
sees the host filesystem where the chain data and the IPFS repository actually
live, unlike `docker system df`, which cannot see bind mounts at all
(**VERIFIED**: all persistent data is bind-mounted from the host).

The cost is on the fleet: an agent per machine, a port per machine, firewall and
network decisions per site, and something that must be installed and upgraded on
hosts the deployer currently only talks to over SSH. The deployer's role changes
from collector to discovery source, and the labels — which are the deployer's real
value — have to be carried into the discovery file as target labels.

### 3.3 Option C — the deployer pushes

The collector samples on its clock and pushes to a Pushgateway, or straight into
Prometheus through remote write. Nothing has to reach the deployer's host, which
removes the ingress and the exposure question entirely, and it fits the shape the
deployer already has: a tool that reaches outwards over SSH.

The cost is staleness. A Pushgateway keeps the last pushed value until it is
deleted, so a fleet that stopped being sampled keeps reporting the same numbers;
the mitigation is to push a timestamp and a success gauge on every round and to
alert on their age (the Pushgateway adds its own `push_time_seconds` and
`push_failure_time_seconds`, which helps but does not solve it). Remote write asks
for a receiving endpoint and, in most deployments, a Prometheus that is already
running somewhere reachable.

### 3.4 Option D — a textfile for a node_exporter on the controller

A periodic job — cron, launchd, or a one-shot `deploy.py metrics --textfile` —
writes the exposition text into the directory that a node_exporter's textfile
collector reads, and Prometheus scrapes that node_exporter on the controller.

It reuses exactly the same pure renderer as options A and C, needs no long-running
deployer process and no new port in the deployer, and inherits staleness reporting
for free, because node_exporter records the file's modification time as
`node_textfile_mtime_seconds`, so a stalled job is visible as a metric rather than
as an absence. It requires one node_exporter, on the controller only — a much
smaller ask than an agent on every host — and it brings with it the host
filesystem metrics that Docker cannot provide. The cost is that the freshest data
is as old as the job's period, and that a scrape of the textfile is a file read,
so the probe cadence and the scrape cadence are entirely independent, which is the
property option A has to work to achieve.

### 3.5 Comparison

| | A — exporter | B — discovery | C — push | D — textfile |
| --- | --- | --- | --- | --- |
| Scrape targets | 1 | 1 per machine | none | 1 (the controller) |
| Prometheus must reach | the deployer's host | every machine | nothing | the controller's node_exporter |
| Agent needed on hosts | no | yes | no | no |
| New port anywhere | yes, on the deployer's host | yes, on every host | no | yes, on the controller (node_exporter) |
| Reuses the probe | fully | not at all | fully | fully |
| Keeps deployment-derived targets | yes | yes | yes | yes |
| Per-host `up` and resolution | no | yes | no | no (fleet) / yes (controller host) |
| Staleness handling | must be built | native | must be built | native |
| Deployer becomes | a daemon | a file writer | a daemon, or a one-shot | a one-shot |
| Host disk visibility | no | yes | no | yes, for the controller host |

**INFERRED**: everything in the Prometheus column above rests on documented
behaviour rather than on a stack running in this environment.

## 4. What gets reused, and what does not

Reuse is not all-or-nothing, and the split is the useful part of this analysis.

**Reusable in every option, because it is not about Docker at all:**

* The **topology mapping** from a managed deployment to machines, services, groups
  and sites. In option B this becomes target labels in the discovery file; it is
  the piece that makes the numbers answerable questions rather than a soup of
  container names, and it is the piece a generic exporter cannot provide.
* The **failure vocabulary**: reachable, section failed, warnings counted. Any
  exporter needs a way to say "this machine was not measured" that is distinct
  from "this machine is fine".
* The **staleness semantics** the panel already implements (keep the last value,
  show its age, never zero it). Exported to Prometheus this becomes a timestamp
  and an age metric instead of a greyed-out cell, but the principle is the same
  and it is what makes a scrape honest.

**Reusable in A, C and D, and only there:**

* The **probe contract** and its parser: one command per machine, tab-separated
  named fields, marker protocol, the drop-and-report rule, and the tolerance for
  contract drift. This is the largest single asset and the reason options A, C and
  D are cheap.
* The **derived quantities**, except the ones Prometheus should own. Cumulative
  network and block counters should be exported raw, so that `rate()` handles
  container restarts; the manual delta logic in `container_rates` exists for the
  panel, where there is no PromQL to lean on, and stays there.
* The **per-machine concurrency** of `sample_machines`, which is already bounded
  and ordered, and the executor routing that gives local and remote machines the
  same code path.

**Reusable in none of them:**

* Nothing, on closer inspection — but it is honest to note that in option B the
  probe is not merely unused, it is *contradictory*: the point of B is to stop
  fanning out over SSH, so keeping the probe alive next to it would mean paying
  for two collection paths. If B is chosen, the probe should be kept for the panel
  and for the one-shot commands, and treated as a diagnostic tool rather than a
  metrics source.

There is also a piece of the current work that is useful to all four options but
does not exist yet, and is cheap because it needs no network: a **pure renderer**
from `MachineMetrics` to the exposition text, and a **cache** that holds the last
sample per machine together with its age and health. Everything else — which
process serves it, over which protocol — becomes a small decision once those two
exist.

## 5. The metric mapping, and where the traps are

The collector already produces everything below. What follows is what each datum
should become, and the trap attached to it.

| Series | Type | Notes |
| --- | --- | --- |
| `dark_machine_reachable{machine,execution,site}` | gauge | Must be emitted **always**, including when the machine is unreachable. A series that disappears is an ambiguous alert |
| `dark_probe_success` | gauge | 1/0 for the probe as a whole |
| `dark_probe_timestamp_seconds` | gauge | The age of the sample, so staleness is queryable |
| `dark_probe_warnings` | gauge | The count from the sample. This is the contract-change detector: the separator defect that broke `info` and `inspect` would have been an alert here instead of 22 silent warnings |
| `dark_machine_cores`, `_memory_total_bytes` | gauge | The denominators; without them the percentages are not comparable between hosts |
| `dark_machine_memory_used_bytes`, `_cpu_fraction_of_host` | gauge | HELP text must say the fraction is of the daemon's cores. On a controller running Docker Desktop or Lima, that is the virtual machine |
| `dark_docker_info{version,api_version,driver}` | gauge 1 | Info pattern: metadata as labels, so a change can be correlated with a version change |
| `dark_container_network_bytes_total`, `_block_bytes_total` | counter | Cumulative per container; export raw and let `rate()` handle restarts. Do not pre-compute deltas |
| `dark_container_memory_bytes`, `_memory_limit_bytes`, `_memory_percent`, `_pids` | gauge | Straightforward |
| `dark_container_cpu_percent` | gauge | **Percentage of one core**, not of the host. `docker stats` exposes no cumulative CPU seconds, so `rate()` over CPU is not available from the CLI; the Docker API does expose it (`cpu_stats.cpu_usage.total_usage`) if that ever becomes necessary |
| `dark_container_restart_count_total` | counter | Per container instance; it resets when the container is recreated |
| `dark_container_start_time_seconds` | gauge | The clearer way to see restarts and recreations: `time() - start_time < 300` |
| `dark_container_state{state=...}` | gauge 0/1 | Keeps the set of label values closed and small |
| `dark_container_running` | gauge | Convenience for the common query |

Five traps deserve their own paragraph.

**Counters and restarts.** Network and block totals reset when a container is
recreated, which Prometheus treats as a counter reset inside `rate()`, provided
the series identity does not change. That is an argument for labels made of
*names* (`deployment`, `machine`, `service`) and against container identifiers and
timestamps in labels, which would both churn and fragment the series.

**Absence is not health.** `up{job="dark-deployer"}` says the endpoint answering
the scrape is alive; it says nothing about the fleet. Alerts about machines must
be built on `dark_machine_reachable` and on the age metrics, not on the scrape
result, and the exporter must keep emitting a machine's series even when it cannot
be sampled.

**Cardinality.** One series per container per metric is a few hundred series for a
nine-host deployment, which is nothing. Exporting every deployment known to the
controller multiplies that, and exporting per-container identifiers or per-sample
timestamps explodes it. The rule is: stable semantic labels only.

**Cost.** `docker stats --no-stream` makes each daemon produce a sample rather than
answering a lookup — the exact delay is unmeasured here, since no Docker was
available in this environment (**INFERRED**) — and it must run at the probe
cadence, never at the scrape cadence. `docker system df` walks the filesystem and
belongs on a much slower clock, or nowhere.

**Resolution.** A 60-second probe does not become a 15-second metric by lowering
`scrape_interval`; it becomes the same number repeated three times. The honest
configuration is a scrape interval no shorter than the probe period, with the probe
period configurable, and the probe timestamp exported so that queries can see
reality.

## 6. Security and operations

The repository's existing standard for a local HTTP surface is `web-wizard/`, and
it is stricter than what a Prometheus probe can afford. That workbench binds
*exclusively to `127.0.0.1`* and requires a per-run token compared with
`secrets.compare_digest`, "so nothing else on the machine — or another web page —
can reach it", and its requirements file states the rule that its framework
dependencies live in `web-wizard/requirements.txt` and **never** in the deployer's
(**VERIFIED**).

A scraper is a machine that lives elsewhere, so option A breaks the first half of
that posture by definition and has to replace it with something explicit: bind to
a private or VPN interface, or keep the loopback binding and have Prometheus reach
it through an SSH tunnel, and in either case put `basic_auth` or a bearer token in
the scrape configuration. Option C needs none of this, because nothing connects
inwards. Option D needs none either, and inherits whatever protection the
controller's node_exporter already has.

One consequence deserves to be stated plainly rather than buried: today the
fleet's SSH keys are used by an interactive command that someone runs. A daemon
holding those keys keeps them in memory permanently, on whatever host it runs, and
that host becomes a control-plane asset rather than a laptop. This is not a reason
to avoid option A, but it is a reason to decide where it runs deliberately, and it
is a point in favour of C and D.

Nothing in this repository supervises a long-running process today — the legacy
monitors are started by hand, and no service unit exists (**VERIFIED**). Option A
or C means adding that (launchd or systemd on the deployer's host, or cron for D),
with the restart, logging and upgrade consequences that follow.

Finally, a note on the second half of the `web-wizard` rule: the deployer's own
`requirements.txt` is a pinned list in which the only web-capable library
(`aiohttp`) is imported by nothing under `deployment_v3/` or `scripts/`
(**VERIFIED**), and the exposition format is about fifty lines of text. Writing it
with the standard library — the format is `# HELP`, `# TYPE`, `name{labels} value`,
with escaping in label values — keeps the deployer free of a new dependency, and
keeps the renderer testable in isolation, which is where the real risk lies: the
escaping rules, not the transport.

## 7. Where I would start

**Stage 0, no network and no new process.** Write the two pure pieces, because
every option uses them and both are testable the way the collector already is:
`exposition.py`, rendering `MachineMetrics` into the exposition text with tests for
label escaping, metric naming and type declarations; and a cache that holds the
last sample per machine with its age, reachability and warning count. Add
`deploy.py metrics --json` on the same collector, which is the non-interactive path
already identified as the next step in `metrics-panel.md`.

**Stage 1, choose the transport.** My recommendation, in order: **option D** if a
node_exporter on the controller is acceptable, because it needs no long-lived
deployer, no new inbound port and no dependency, and it brings the host filesystem
metrics this project cannot get from Docker; **option A** bound to a private
interface if not, because it is the most direct reuse and the only one that can
answer arbitrary questions on demand; **option C** if the deployer's host must not
be reachable at all; and **option B** as the target to grow into if per-host
resolution, per-host `up` or host disk capacity become requirements — accepting
that it means installing an agent on every host, which is exactly what this design
has avoided so far.

**Stage 2, absorb the legacy monitors.** `monitor_dark_runtime.py` and
`monitor_dark_infrastructure.py` already emit one record per sample and were
written for later analysis; once a metric source exists, their job is a subset of
it, and their local-only, name-prefix-limited collection becomes unnecessary.

The invariant to protect through all of it: **the deployment is what decides what
is observed**, and the deployer is the one place that resolves it.

## 8. Open decisions

These are questions of policy, not of code, and they change the design more than
any implementation detail:

1. Where does Prometheus run relative to the deployer's host, and can it reach
   that host, or must the deployer be the one that reaches out?
2. Is a node_exporter acceptable on the controller? It is the price of option D,
   and it also solves host disk visibility.
3. Is an agent per host ever acceptable? That is the price of option B, and the
   only route to per-host `up` and real per-host resolution.
4. What probe cadence is useful, and does anything need to alert within less than
   a minute of an event? If it does, no option in this document satisfies it, and
   the answer is per-host exporters with a short scrape interval.
5. Should the exporter serve every deployment the controller knows about, or one
   selected deployment? The label `deployment` makes both possible; the cardinality
   and the failure blast radius differ.
6. Who owns the running process: a service unit on the controller, cron, or the
   operator starting it by hand as today?

## Appendix: what the text would look like

A sketch of the exposition for one machine, which is what Stage 0 would render. It
is illustrative, not implementation output:

```
# HELP dark_machine_reachable Whether the machine answered its read-only probe
# TYPE dark_machine_reachable gauge
dark_machine_reachable{deployment="dark-operator-local-ha",machine="local",execution="local",site="main"} 1
# HELP dark_probe_warnings Rows and sections the probe refused, by machine
# TYPE dark_probe_warnings gauge
dark_probe_warnings{deployment="dark-operator-local-ha",machine="local"} 0
# HELP dark_container_cpu_percent Container CPU as a percentage of one core
# TYPE dark_container_cpu_percent gauge
dark_container_cpu_percent{deployment="dark-operator-local-ha",machine="local",service="validator01"} 42.5
# HELP dark_container_network_bytes_total Cumulative container network bytes
# TYPE dark_container_network_bytes_total counter
dark_container_network_bytes_total{deployment="dark-operator-local-ha",machine="local",service="validator01"} 3401200
```

And the two queries this design is aiming at, which are the reason the labels
matter:

```promql
# Fleet memory as a share of each daemon, per machine
dark_machine_memory_used_bytes / dark_machine_memory_total_bytes

# Anything whose probe is failing or drifting, which is the alert a silent
# contract change would have tripped
dark_machine_reachable == 0 or dark_probe_warnings > 0
```
