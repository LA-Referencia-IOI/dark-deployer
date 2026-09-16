# Docker metrics panel (`deploy.py metrics`)

A read-only terminal panel that shows, for one managed deployment, a row per
machine with Docker-level CPU, memory, container and restart state, plus the
per-container detail of the selected machine. It never starts, stops, recreates
or removes anything.

## Scope of this document

What is described here is the complete implementation of that panel and of the
collector behind it: the decisions taken, what was rejected and why, how each
layer is built, how it behaves when things fail, how it is tested, and what is
still unproven. Date: 2026-09-16, on `main`, on top of `1f22db8` ("Add
interactive deployment operations console").

Tree state: this work sits on `main` on top of `1f22db8` ("Add interactive
deployment operations console"), in the commit that adds the panel and this
document plus the follow-up that fixes the separator defect found by the first
real run. The working tree also contained unrelated modifications that are
deliberately not part of it: `local-infra/lima-status.sh`,
`local-infra/lima-two-site-down.sh`,
`examples/operator-inventory/aws-active-six-host.md`, the untracked
`infrastructure/` directory and `tests/test_aws_cloudformation_profile.py`.
Since line numbers move, the stable references in this document are the file, the
function and the test names, not line numbers.

Method: the collector and the panel were written, then executed. The parser and
the view helpers are covered by unit tests; the probe shell program is executed
against a fake `docker` placed on `PATH`; the panel itself is driven by the
Textual pilot over a bundle rendered into a temporary directory. Every claim
below is marked **VERIFIED** (reproducible evidence: a test, a command or a file
in the repository) or **INFERRED** (my reading, without direct proof). Where the
evidence only supports part of a claim, that is said explicitly.

The single most important caveat: no real Docker daemon was available in the
environment where this was written, so field names came from the Docker CLI
documentation rather than from a live run. That is no longer the whole story —
the first real execution against a running deployment happened on the same day,
on Docker 29.8.0 / API 1.56, and it immediately found a genuine defect in the
separator between fields, which is described in
[what a real run found](#what-a-real-run-found) and has been fixed. What remains
to be confirmed from a live run is listed in
[What is not verified](#10-what-is-not-verified).

## 1. Why a separate panel

The operations console (`deploy.py tui`) already shows deployments and services,
and it has buttons that build, stop and remove. A metrics view inside it would
inherit that power without needing it. Splitting the observation out yields
something strictly read-only: safe to leave open next to the operations console,
safe to run against production, and safe to hand to someone who must not be able
to press "remove".

Two secondary reasons supported the same choice. The operations console is the
tool used when something is broken, so adding a panel to it carries a regression
risk at the worst possible moment; and the console is not built for periodic
sampling at all, since its only timer re-reads the *logs* of the open service
every five seconds and its snapshot is on demand (**VERIFIED**:
`operations_tui.py`, `set_interval(REFRESH_SECONDS, self._refresh_logs_if_open)`).
A watcher that samples every minute is a different kind of thing.

The cost accepted is a context switch: during an incident, seeing the problem
and acting on it now happen in two applications. It is mitigated by keeping the
same vocabulary of states in both, and by showing the service inventory — with
its authoritative states — inside the metrics panel too, so the first triage
question ("what is down?") is answered without leaving it. Only acting requires
switching.

**The separation is enforced, not just intended.** `test_metrics_view.py::PanelIsReadOnlyTests`
asserts that the view module never mentions `manage_service`, `read_service_logs`
or `run_lifecycle`, and
`MetricsPanelTests::test_panel_renders_machine_samples_and_offers_no_action`
asserts that the running application contains zero `Button` widgets
(**VERIFIED**).

### The shared layer

A separate frontend must not become a second source of truth. The deployment and
service state that both consoles read therefore moved to `deployment_v3/console.py`
(`ConsoleSnapshot`, `snapshot`, `service_detail`, `action_choices`), which imports
no terminal toolkit. `operations_tui` now imports them from there and re-exports
them under `__all__` so existing callers keep working — including
`tests/test_deployment_v3.py`, which imports `action_choices` and `service_detail`
from `operations_tui` and was left untouched on purpose, as proof that the
re-export holds (**VERIFIED**: the suite passes with the original import).

## 2. What was rejected, and why

**An SSH tunnel to the remote Docker socket.** Technically possible without
touching the host (`ssh -N -L /tmp/docker.sock:/var/run/docker.sock`, with
`StreamLocalBindUnlink=yes`), but it buys nothing for this purpose: the output of
`docker stats`, `docker info` and `docker ps` is byte-identical whether the CLI
runs on the remote host through `SshExecutor` or locally against a forwarded
socket, because the same daemon produces it either way. Against that, it adds a
persistent process and a socket per machine to supervise, re-key and tear down,
it makes the local Docker CLI version a variable (Docker Desktop/Lima against a
remote daemon), its availability depends on the remote `sshd` allowing
`AllowStreamLocalForwarding`, and its failure mode is a silently stale
connection instead of the fast, loud failure that already maps to `unreachable`
in this code base. Rejected for this phase; the tunnel remains the right tool if
streaming `docker events` is ever wanted.

**Host-level probes.** Reading `/proc/loadavg`, `/proc/meminfo` or `df` directly
would mean a different code path per operating system, and the controller of this
project is macOS while the remote hosts are Linux. Letting the Docker daemon do
the measuring removes that branch entirely.

**An agent-based metrics stack** (node-exporter, cAdvisor, Prometheus). There is
no precedent for it in the catalogue, and it would add ports, networks and
site/VPN decisions the deployer does not currently govern.

**A fourth pane inside the operations console.** Rejected with the panel itself,
see above.

**Filtering containers by name prefix.** The existing monitors select containers
with `name.startswith("dark-")` (`scripts/monitor_dark_runtime.py`). The panel
uses the managed label instead, which is exact and already the join key for
services (**VERIFIED**: all 24 services in the five rendered compose files of
`dark-operator-local-ha` carry `org.dark.deployment.id` and `org.dark.service.id`).

**The full `docker inspect` document.** It carries the containers' environment,
and environment is where the deployment's secrets live. The probe asks for four
named fields instead.

**`docker --format '{{json .}}'`.** See section 8.

## 3. Architecture and files

| File | Lines | Role |
| --- | --- | --- |
| `deployment_v3/metrics/collector.py` | 646 | Read-only probe: command construction, execution, parsing. Imports no terminal library. |
| `deployment_v3/metrics/textual_app.py` | 599 | The panel, plus the pure helpers it renders from. Imports Textual lazily. |
| `deployment_v3/metrics/__init__.py` | 42 | Re-exports of the collector's public surface. |
| `deployment_v3/console.py` | 55 | Toolkit-free deployment/service state shared by both consoles. |
| `tests/test_metrics.py` | 562 | 47 tests: contract, parser, program, rates. |
| `tests/test_metrics_view.py` | 376 | 35 tests: view helpers, container order, warning summary, pilot runs. |

Modified, in support of the above: `deployment_v3/cli.py` (import, `metrics`
subparser, `_run_metrics_console`, dispatch branch), `deployment_v3/executor.py`
(one robustness fix, section 6), `deployment_v3/operations_tui.py` (the three
functions moved out, re-exported), `tests/test_deployment_v3.py` (one new test),
`README.md` and `OPERATIONS-MANUAL.md` (documentation).

The layering follows the precedent already set by `inventory_editor/`: a
toolkit-free model, and one thin module that imports the terminal library. Textual
is imported inside `_textual()`, called only from `run_metrics_tui` and
`_make_textual_app`, so nothing else in the deployment CLI depends on the optional
package. **VERIFIED** by a subprocess that installs an import hook rejecting any
`textual` import and then imports `deployment_v3.cli` and
`deployment_v3.metrics.textual_app` successfully; the error raised by
`_textual()` in that situation names `requirements-tui.txt`.

`_make_textual_app(project_root)` exists as a separate factory from
`run_metrics_tui(project_root)` so a test can drive the application without a
terminal, mirroring `inventory_editor`'s `_make_textual_app`.

## 4. The probe contract

One machine is sampled with **one** `sh -lc` program, executed either locally or
over SSH through the deployment's own executors. One session per machine per
sample; the same parser for both transports. The program is in
[Appendix A](#appendix-a-the-generated-probe-script).

Each section announces itself with a marker line
(`__dark_metrics__ <section>`), prints tab-separated rows, and reports a failure
with `__dark_error__ <message>` inside its own section. Everything the script
prints is one line per record, which keeps the parser trivial and means a partial
read cannot be mistaken for a complete one.

| Section | Command | Fields | Critical |
| --- | --- | --- | --- |
| `daemon` | `docker info` | `NCPU`, `MemTotal`, `ContainersRunning`, `ServerVersion`, `Driver` | yes |
| `version` | `docker version` | `Server.APIVersion` | no |
| `containers` | `docker ps -a --filter label=org.dark.deployment.id=<id>` | `ID`, `Names`, `State`, `Status`, `Label "org.dark.service.id"` | yes |
| `stats` | `docker stats --no-stream` | `ID`, `Name`, `CPUPerc`, `MemUsage`, `MemPerc`, `NetIO`, `BlockIO`, `PIDs` | no |
| `inspect` | `docker inspect <ids>` | `Id`, `Name`, `RestartCount`, `State.Status`, `State.StartedAt` | no |
| `disk` | `docker system df` | `Type`, `Size`, `Reclaimable`, `TotalCount` | no, and off by default |

Three details in that table are deliberate:

* **The label filter.** `docker ps --filter label=...` returns every container of
  the deployment on that daemon in a single call. `list_managed_services` instead
  runs one `docker ps` per *group* (`runner.py`), so the panel's per-machine call
  replaces five calls with one in `local-ha` and nine with five in the AWS
  topologies. The label is also what makes the join to the service inventory exact.
* **`docker stats` cannot be filtered.** It has no `--filter` flag, so the script
  always asks about every container on the daemon and the parser restricts the
  result to the deployment by joining on the container name. Names are the join
  key and not IDs because `docker inspect` reports `{{.Name}}` with a leading
  slash while `docker ps` and `docker stats` do not; `_normalize_name` strips it,
  and a test covers the join (**VERIFIED**).
* **`inspect` runs in a subshell.** It needs the container IDs from the same SSH
  session, so it is `sh -c 'ids=$(docker ps -aq ...); if [ -n "$ids" ]; then docker inspect ... $ids; fi'`.

The deployment identifier is the only value interpolated into the script, and it
is passed through `shlex.quote`. A test builds the script with a hostile
identifier (`evil; touch /tmp/dark-pwned 'quoted'`), asserts that the quoted form
is present in the script, and that the resulting program still passes `sh -n`
(**VERIFIED**).

## 5. Data model

`MachineMetrics` is one machine's sample: identity (`machine_id`, `execution`),
`reachable` and `error`, the daemon's `cores`, `memory_total_bytes`,
`containers_running`, `server_version`, `api_version` and `driver`, then
`containers`, `disk_footprint` and `warnings`. `ContainerSample` is one container;
`DiskFootprint` is one row of `docker system df`; `ContainerRate` is the activity
between two samples.

Three conventions carry meaning:

* **`None` is "no data", `0` is "measured zero".** A stopped container has no
  CPU or memory figures, so those fields are `None` and the panel prints `—`. A
  running but idle container has `cpu_percent = 0.0` and the panel prints `0.0%`.
  A test asserts that a missing percentage is never invented.
* **Two denominators, both of the daemon.** `CPUPerc` is relative to one core and
  can exceed 100%, so the aggregate divides by `NCPU`; `MemPerc` is relative to
  the daemon's memory. On a controller running Docker Desktop or Lima that is the
  virtual machine, not the physical host. The detail pane says so in words, so the
  number is not read as "how loaded is my Mac".
* **`containers_running` is daemon-wide** and includes containers that are not
  part of the deployment. It is not the comparison for "is my deployment up"; for
  that, the panel counts the deployment's own containers.

`cpu_percent_of_host`, `memory_bytes` and `memory_percent_of_host` return `None`
rather than zero when the denominator or the numerators are missing.

`container_rates(previous, current, elapsed_seconds)` derives per-second network
and block activity from cumulative counters, and it refuses to do so — returning
`None` — when the container is new, when `started_at` changed (a restart resets
the counters), when a counter moved backwards, or when no time elapsed. Without
those guards a `recreate` would draw a spike that looks like a real incident
(**VERIFIED**: five tests in `ContainerRateTests`).

## 6. Failure semantics

| Situation | Outcome |
| --- | --- |
| Non-zero exit from the probe (SSH refused, timeout, daemon down) | `reachable=False`, `error` carries the message, container list empty, other machines unaffected |
| `docker` not present on the host | The program itself reports it: `reachable=False`, `error="docker CLI not found on this machine"` |
| `daemon` or `containers` section fails | Sample discarded, machine `unreachable` |
| `stats`, `inspect`, `version` or `disk` section fails | Sample kept, section named in `warnings` |
| A row with an unexpected field count | Row dropped, warning recorded; the rest of the section still parses |
| A row whose separator was never expanded | Same, and the warning names the cause: `Docker left the tab separator unexpanded` |
| The daemon answers without `NCPU` or `MemTotal` | The row is kept and the missing denominator is named, instead of leaving the CPU and memory columns silently blank |
| `docker stats` returning "No containers found" | Not a warning: it is the normal answer for a deployment with nothing running |
| Empty output | `unreachable`, never an empty success |
| `NetIO`/`BlockIO` impossible to rate | Rate `None`, value still shown as cumulative-derived elsewhere |

Three of these deserve a note. A machine that cannot be sampled **keeps its last
values and its age on screen** instead of being blanked or zeroed, which is the
whole point of an operations panel during an incident; `unreachable` is therefore
a first-class state, not an error dialog. The distinction between critical and
optional sections is what lets a machine remain useful when only a convenience
section fails. And "docker CLI missing" is answered by the probe rather than by
Python, because it is a property of the destination host, not of the controller.

Row lines are deliberately **not** stripped before parsing. Stripping would
remove an empty leading field, which is the difference between five columns and
four, and would shift every value one position left instead of reporting the row
as unparsable — the exact silent corruption this design is built to avoid. Only
blank lines are skipped. This was found while writing a test for the missing
denominator case, and it is pinned by a test that parses a daemon row with an
empty `NCPU` field.

**One bug found here.** `LocalExecutor.run` caught `subprocess.TimeoutExpired` but
not `OSError`, so a host without the `docker` binary raised `FileNotFoundError`
out of `list_managed_services` instead of returning a non-zero result
(**VERIFIED**: reproduced, then fixed). That affected the pre-existing operations
console as well, and it would have crashed the metrics panel on exactly the host
most likely to lack Docker — the controller. `LocalExecutor.run` now returns
`CommandResult(..., 127, "", str(exc))`, matching what
`scripts/monitor_dark_runtime.py` already did for the same condition; a test in
`test_deployment_v3.py` pins it.

## 7. Panel behaviour

Layout: `DEPLOYMENTS` (fixed width), `MACHINES` (flexible, the main table),
`DETAIL` (fixed width) across the top, a full-width `CONTAINERS OF THE SELECTED
MACHINE` table below, and a status line plus the footer. The layout assumes a
terminal of roughly 120 columns to show all eight machine columns.

**Two clocks, deliberately different.** Machines are sampled every 60 seconds and
on demand, because each sample is a full Docker probe per machine. The service
inventory of the selected deployment is read only on mount, on selection and on
demand, because it costs one `docker ps` per group over SSH and it is the
expensive part. Both ages are on screen: each row carries the age of its own
sample, and the status line carries the age of the service read.

**Sampling never blocks the interface.** One worker per machine
(`group=f"metrics:{machine.id}", exclusive=True, thread=True`), so a slow host
delays only its own row, and re-sampling a machine cancels the worker already
running for it instead of queueing a second one. Rows are filled in as samples
arrive. The results are applied on the UI thread through `call_from_thread`,
which is the pattern the operations console already used for log reads.

**Keys.** `tab`/`shift+tab` move between panes, `r` re-reads the inventory and
starts a sampling round, `m` re-samples only the selected machine, `s` cycles the
container order, `?` explains that nothing here can act, `q` quits.

**Container order.** `s` cycles the container table through three orders: by
name, by CPU with the busiest first, and by memory with the largest first. The
current choice is shown in the pane title, and the selection follows the
container by name so re-ordering never loses your place. Containers the daemon
cannot measure — a stopped one-shot job, for instance — have no value to compare,
so they stay last under either metric rather than sorting as zero, and ties fall
back to the name. Because the order is re-applied on every sample, rows move as
load moves; that is what "sorted by CPU" means, but it is worth knowing before
leaving the pane open on a second monitor.

**Detail pane.** Daemon version, API version, driver, cores, memory total, the
daemon-wide container count, an explicit sentence about the denominators, the
first warnings, whether another machine shares this daemon, and the states of the
services placed on this machine.

**No action, anywhere.** No lifecycle buttons, no confirmation dialog, no log
viewer, no mutation of any kind.

Two defects were found by running the panel rather than by reading it. The
machines table initially received nine cells for eight columns, which crashed the
application on mount (`ValueError: More values provided than there are columns`)
(**VERIFIED**: the pilot test failed, then passed after the fix); the row builder
now derives the gap count from the table definition, and a test asserts every row
shape matches. Separately, the row-highlight and row-selection handlers queried
widgets that could already be unmounted during shutdown, raising `NoMatches` out
of a queued event; every lookup now goes through `_widget()`, which returns `None`
when the panel is not mounted, and the render entry points degrade to no-ops.

## 8. Docker version compatibility

This deserves its own section because a changed contract that silently becomes a
wrong number is worse than a visible failure, and because this repository has
already been bitten by JSON shape changes.

**Templates, not JSON keys.** Every section asks for named template fields
(`{{.CPUPerc}}`) rather than the keys of `docker --format '{{json .}}'`. That is
the same surface the deployment runner already uses for `docker ps`, and it has
been stable for far longer than any particular JSON key capitalisation. It also
means an unavailable field fails visibly instead of quietly yielding a default.

**A literal tab, never the `\t` escape.** The second half of the same lesson, and
the one that a live run taught us. Docker's commands do not agree on whether they
expand `\t` inside a `--format` template: `ps` and `stats` do, `info` and
`inspect` do not. The separator is therefore written as a real tab character,
which needs no escape processing by anybody and is copied verbatim by all of them.

### What a real run found

The first execution against a running deployment — 2026-09-16, Docker 29.8.0,
API 1.56, the `dark-operator-one-server-aws-sandbox` deployment on the controller,
21 containers — exposed the problem above in full view.

The panel reported **22 warnings**: one for the daemon row and one per container
for the `inspect` row, all of them reading *"1 field(s), expected 5"*. The machine
showed `active` with 21/21 containers and 13.2 GiB of memory, but its CPU column,
its denominators and the restart total were empty, because the two sections that
produce them had been rejected wholesale. The service list was pushed out of the
detail pane by the wall of identical warnings.

The cause was not the field names — `ps`, `stats` and `version` parsed perfectly,
which validated the label filter, the five container fields, the eight stats
fields, the API-version field and the join by name all at once. It was the
separator: those two sections returned the literal characters `\t` instead of a
tab, so each row was a single field. A cross-check inside this repository settled
it: `runner.py` passes the very same `\t` escape to `docker ps`, and
`deploy.py services` has always worked against these hosts, which is only possible
if `ps` expands it.

Three changes came out of the run. The separator became a literal tab. The
warnings became actionable — they now state the field count, the expected count,
and the cause when it is recognisable (`Docker left the tab separator
unexpanded`), while still carrying the raw row as evidence. And repeated warnings
are grouped by kind, so twenty-one identical lines collapse into one, and a single
noisy section can no longer hide the service list. The status line also names
which machines have warnings, because a sample that is missing whole columns must
not look like a clean one.

One question the run could not settle and the fix will answer on the next
execution: whether `{{.NCPU}}` itself returns a value on that Docker version. If
it does not, the daemon row now survives and the panel says so explicitly
(*"the daemon reported no NCPU value"*) instead of quietly leaving the CPU column
blank.

**The contract lives in exactly one place.** The templates are built by
`_template()` from the field tuples `DAEMON_FIELDS`, `VERSION_FIELDS`,
`CONTAINER_FIELDS`, `STATS_FIELDS`, `INSPECT_FIELDS` and `DISK_FIELDS`, and the
parser validates each row against `len(<tuple>)`. `ContractTests` pins the tuples
themselves and the shape of the resulting templates, so changing a field, its
order, or the number of columns is a deliberate edit that fails a test if done
halfway.

**A mismatch is dropped and reported, never parsed optimistically.** A row whose
field count does not match is discarded with a warning naming the section. A field
added or removed upstream therefore surfaces as a warning instead of shifting
every column by one — the exact failure mode that makes a JSON contract change
dangerous (**VERIFIED**: `ContractTests::test_a_row_with_an_unexpected_field_count_is_reported_not_shifted`).

**The version travels with the sample.** `ServerVersion` comes from `docker info`
and `APIVersion` from an extra `docker version --format '{{.Server.APIVersion}}'`
in the same SSH session. Both are shown in the detail pane, so any surprising
number can be attributed to the Docker that produced it. The section is optional:
if it fails, the sample survives with a warning.

**The read-only allow-list is two-sided.** `READ_ONLY_DOCKER_SUBCOMMANDS` declares
the only Docker subcommands the collector may ever issue. One test asserts the
generated script stays inside that set, a second asserts the allow-list has not
been widened, and a third asserts the script contains none of a list of mutating
tokens (`prune`, `rm`, `rmi`, `stop`, `start`, `restart`, `kill`, `run`, `create`,
`build`, `push`, `exec`, `compose`, `update`, `pause`). The allow-list test is
what caught the addition of `docker version` during development, which is exactly
the intended behaviour: every new subcommand is a declared decision.

**Expected minimum versions, documented but not verified.** The core sections
depend on `--format` with tab templates, which this repository already relies on
and which has been available since roughly Docker 1.13 (2017); the optional disk
section needs `docker system df --format`, which arrived with Docker 20.10 (2020)
and is therefore off by default and on its own slower clock. **INFERRED**: those
version floors come from Docker's documentation and were not exercised against
old daemons here, because there was no Docker at all in the environment where this
was written.

**What a future contract change costs.** If a field disappears, one section fails
and degrades to a warning while the rest of the sample survives. If a field is
renamed, the same. If a field is inserted in the middle of a template, rows are
dropped and reported. In all three cases the operator sees a warning and the
Docker version that produced it, and the fix is a single edit to a field tuple
plus the corresponding tests.

## 9. Verification

```bash
venv/bin/python -m pip install -r requirements-tui.txt   # Textual, optional
python -m unittest discover -s tests -p "test_*.py"      # 150 tests
```

Result at the time of writing: **164 tests, all passing, `ruff` clean** on the
changed and added files. 82 of those tests live in the pre-existing files (one of
them, in `test_deployment_v3.py`, is new and belongs to this work); 82 are new and
split as follows.

| Class | Tests | What it establishes |
| --- | --- | --- |
| `ProbeScriptTests` | 6 | Only read-only subcommands; the label filter; `disk` off by default; a hostile deployment id stays quoted and the program stays valid shell |
| `ParseProbeOutputTests` | 11 | Denominators, per-container metrics, stopped containers keep their row without numbers, aggregates, out-of-deployment rows ignored, malformed rows warn, unit parsing, absent fields |
| `ContractTests` | 8 | The field tuples and template shape are pinned; no template relies on Docker expanding an escape; an unexpected field count is dropped, not shifted; an unexpanded separator is named; a daemon without denominators says so; the API version travels; the version section is never critical |
| `FailureSemanticsTests` | 6 | Unreachable transport, missing Docker, optional vs critical sections, benign "No containers found", empty output |
| `SampleMachineTests` | 3 | Executor timeout becomes an unreachable sample; the plan's deployment id reaches the script; resolution failure is reported, not raised |
| `ProbeProgramTests` | 3 | The real shell program runs: sections parse, a missing Docker CLI is explained, a foreign deployment is not queried |
| `ContainerRateTests` | 5 | Rates from cumulative counters, and their refusal on restart, reset, unknown container and no elapsed time |
| `DaemonKeyTests` | 2 | `local` and `docker-lab` collapse to one daemon; remote machines are keyed by address and port |
| `ExecutorRoutingTests` | 1 | The collector reuses the deployment executors instead of growing its own SSH transport |
| `SizeParsingTests` | 2 | Decimal and binary units; unknown units refused |
| `FormattingTests` | 2 | Byte and age formatting |
| `MachineStateTests` | 6 | `sampling`, `unknown`, `unreachable`, `active`, `degraded` (restart loop, unmeasurable container) and `inactive` |
| `MachineRowTests` | 4 | Row contents, blanked numbers for unreachable machines, explicit `sampling`, every shape matching the table |
| `ContainerRowTests` | 4 | Stopped containers, rates attached by name, no sample means no rows, no invented percentage |
| `ContainerOrderTests` | 7 | Name order by default, CPU busiest first, memory largest first, unmeasurable containers last under both metrics, ties by name, an unknown order falls back to the name, the cycle covers both metrics |
| `MachineDetailTests` | 4 | Unreachable machines explain themselves, denominators are named, a shared daemon is disclosed, a machine without services says so |
| `WarningSummaryTests` | 3 | Repeated warnings collapse to one line, a single warning keeps its raw evidence, many kinds are capped |
| `PanelIsReadOnlyTests` | 1 | The view imports no mutating API |
| `MetricsPanelTests` | 4 | Pilot runs: the panel renders real samples over a rendered bundle, survives an unreachable machine, re-sampling one machine does not disturb the others, and pressing `s` cycles the container order |

Three of those are worth singling out because they test something a unit test
cannot. `ProbeProgramTests` puts a fake `docker` executable on `PATH` and lets the
real `/bin/sh` run the real generated program, which is the only way to know that
the marker protocol, the `capture` helper, the argument quoting and the `inspect`
subshell actually work together; a second case gives the program a `PATH`
containing only a symlink to the shell, proving that the "docker CLI not found"
branch is reached rather than assumed. `MetricsPanelTests` renders a real
deployment bundle into a temporary directory, patches only `sample_machine`, and
drives the application with `app.run_test()`; the assertion that the running
application contains no `Button` is the read-only guarantee expressed as code.

## 10. What is not verified

Stated plainly, because the difference between "tested" and "trusted" matters
here:

* **Live field names, partly confirmed.** The run of 2026-09-16 confirmed against
  a real daemon that `ps` resolves all five container fields including the service
  label, that `stats` resolves all eight metric fields, that `version` resolves
  `Server.APIVersion`, and that the label filter and the join by container name
  work on a real 21-container deployment. Still to confirm, on the next run after
  the separator fix, are the `info` fields (`NCPU`, `MemTotal`, `ContainersRunning`,
  `ServerVersion`, `Driver`) and the `inspect` fields (`Id`, `Name`, `RestartCount`,
  `State.Status`, `State.StartedAt`), which the breakage prevented from ever being
  reached. If a name is wrong, the section that uses it fails with a warning rather
  than printing a wrong number, which is the behaviour the design chose on purpose.
* **`docker system df`.** The disk section is off by default and was not exercised
  at all, because the run used the default configuration.
* **`docker-lab` daemon sharing.** `daemon_key()` assumes that every `local` and
  `docker-lab` machine is sampled through the controller's single daemon, so
  several inventory machines could render as identical rows in a lab plan.
  **INFERRED** from the resolver's `docker-lab` handling, not observed. The panel
  mitigates it by naming the other machines that share a daemon in the detail
  pane, but the collapse itself has not been confirmed against a running lab.
* **Older daemons.** The documented version floors were not exercised.
* **macOS and Lima specifics.** Whether `/proc`-style host figures inside a
  container reflect the Lima virtual machine rather than the Mac is **INFERRED**;
  the panel does not read them anyway, but the same question applies to
  `MemTotal` and it is why the detail pane states which denominator it is using.
* **Other real deployments.** The panel has been opened against one running
  deployment on the controller (21 containers, local machine). It has not been
  exercised against a remote `ssh` machine, against a multi-machine `docker-lab`
  or two-site plan, or against a stopped stack.

## 11. Known limitations

**Disk is not this panel's business, and cannot be.** All persistent data in this
platform is bind-mounted from the host (**VERIFIED**: the generated compose files
mount the `data_root` path into `/data` for Besu, into `/var/lib/postgresql/data`
for Postgres and into `/app/metadata_storage` for the minter). `docker system df`
accounts for images, writable layers, named volumes and build cache only, so it
cannot see the growth of the chain, the IPFS repository or the database. The
optional `disk` section is therefore about the Docker footprint — useful for
deciding whether a `prune` is possible — and host disk capacity needs a different
mechanism, in practice a read-only container running `df` over the data root,
which is not implemented here.

**`docker stats` sees running containers only.** In a stopped stack the container
rows keep their existence and state but carry no numbers, which is honest and
deliberate, though it means the panel is least informative precisely when the
stack is down.

**No history.** Every sample is held in memory and discarded on exit, so there are
no deltas across sessions, no graphs and no "disk grew 3 GB in the last hour". The
rates shown are between two samples of the same session.

**Services are read on demand.** The service inventory is not polled on the
60-second clock, by design (it is the expensive call). Its age is displayed so it
cannot be read as current by accident.

**Duplicate rows in lab plans.** See the `docker-lab` item above.

**Terminal width.** The fixed-width panes assume roughly 120 columns; narrower
terminals clip the trailing machine columns. Within a pane, a long deployment
identifier can clip the state column of the deployments table, which is inherited
from the operations console's layout.

**No non-interactive mode yet.** The collector is ready for one — it imports no
terminal library and `sample_machines()` samples every machine of a plan with
configurable concurrency — but `deploy.py metrics --json` does not exist.

## 12. Usage

```bash
venv/bin/python -m pip install -r requirements-tui.txt
venv/bin/python deploy.py metrics
```

It requires an interactive terminal; without a TTY it fails with a message
pointing at the non-interactive equivalents (`status`, `deployments`,
`services`). It reads the deployments this controller knows about, selects the
first one, and can switch between them in the left pane. It never changes
deployment state, so running it in parallel with `deploy.py tui` is safe, at the
cost of the extra SSH sessions the two together open.

## 13. Next steps

In the order I would tackle them:

1. **`deploy.py metrics --json`**, a non-interactive mode over the same collector,
   which would also let the two `scripts/monitor_*.py` be re-expressed on it
   instead of duplicating collection between themselves.
2. **Confirm the field names against a real host**, which is the cheapest way to
   close the largest gap in this document.
3. **Persist samples** as one JSON record per line per deployment — the format the
   existing monitors already use — to gain deltas, simple sparklines and the
   "disk grew" question, which for Besu and IPFS matters more than an instant
   value.
4. **Host disk capacity** through a read-only container probe over the data root,
   the only approach that stays uniform across macOS and Linux.
5. **Healthchecks in the catalogue.** There are none today (0 of 24 services), so
   `State` plus `RestartCount` is the only normalised health signal available; a
   healthcheck per service would give the panel, and everyone else, a better one.
6. **`docker events` streaming**, if the polling interval ever becomes a
   limitation; that is the case where the rejected SSH tunnel would earn its
   place.

## Appendix A: the generated probe script

Output of `probe_script("dark-operator-local-ha")` as implemented, with the
optional `disk` section omitted. The separator between fields is a **literal tab
character**, so if your viewer expands tabs the listing will look as though the
fields are spaced rather than separated; the five lines that begin with `capture`
are the exact `docker` invocations.

```sh
capture() {
  kind="$1"; shift
  printf '%s %s\n' __dark_metrics__ "$kind"
  if output=$("$@" 2>&1); then
    if [ -n "$output" ]; then printf '%s\n' "$output"; fi
  else
    printf '%s %s\n' __dark_error__ "$(printf '%s' "$output" | tr '\n' ' ')"
  fi
  return 0
}
if ! command -v docker >/dev/null 2>&1; then
  printf '%s %s\n' __dark_metrics__ error
  printf '%s\n' 'docker CLI not found on this machine'
  exit 0
fi
capture daemon docker info --format '{{.NCPU}}	{{.MemTotal}}	{{.ContainersRunning}}	{{.ServerVersion}}	{{.Driver}}'
capture version docker version --format '{{.Server.APIVersion}}'
capture containers docker ps -a --filter label=org.dark.deployment.id=dark-operator-local-ha --format '{{.ID}}	{{.Names}}	{{.State}}	{{.Status}}	{{.Label "org.dark.service.id"}}'
capture stats docker stats --no-stream --format '{{.ID}}	{{.Name}}	{{.CPUPerc}}	{{.MemUsage}}	{{.MemPerc}}	{{.NetIO}}	{{.BlockIO}}	{{.PIDs}}'
capture inspect sh -c 'ids=$(docker ps -aq --filter label=org.dark.deployment.id=dark-operator-local-ha); if [ -n "$ids" ]; then docker inspect --format '"'"'{{.Id}}	{{.Name}}	{{.RestartCount}}	{{.State.Status}}	{{.State.StartedAt}}'"'"' $ids; fi'
```

The `'"'"'` sequences are `shlex.quote` escaping the format string that `inspect`
needs inside its own `sh -c`. The program is POSIX shell: only `printf`,
`command -v`, command substitution, a function definition and `tr`, so it runs
under `bash` 3.2 on macOS and under `dash` on a minimal Linux host alike.

## Appendix B: commands used to verify

```bash
# Full suite, and lint
python -m unittest discover -s tests -p "test_*.py"
python -m ruff check deployment_v3/ tests/

# The probe program against a fake docker on PATH
python -m unittest discover -s tests -p "test_metrics.py"

# The panel itself, driven by the Textual pilot
python -m unittest discover -s tests -p "test_metrics_view.py"

# Imports remain free of the optional dependency
python -c "import deployment_v3.cli, deployment_v3.metrics.textual_app"

# The generated program is valid shell even with a hostile deployment id
python -c "from deployment_v3.metrics import probe_script; print(probe_script('x; touch /tmp/pwned'))" | sh -n
```

To see what a machine's rows actually contain when a section is rejected, ask
Docker directly and show the escapes. This is how the separator defect was
diagnosed from the field, and it is the first thing to run if a warning appears:

```bash
# ^I marks a real tab; the two characters \t mean the escape was not expanded
docker info --format '{{.NCPU}}\t{{.MemTotal}}\t{{.ContainersRunning}}\t{{.ServerVersion}}\t{{.Driver}}' | cat -A
docker inspect --format '{{.Id}}\t{{.Name}}\t{{.RestartCount}}\t{{.State.Status}}\t{{.State.StartedAt}}' \
  $(docker ps -aq --filter label=org.dark.deployment.id=DEPLOYMENT) | cat -A | head -3
```
