"""Read-only Docker metrics for every machine of a managed deployment.

The collector reuses the deployment executors, so a ``local`` machine is sampled
with the local Docker CLI and a remote machine through the same SSH transport
the lifecycle commands already use (private key, port, ``BatchMode``,
``known_hosts`` and ``auto`` resolution included).  It opens no tunnels and
changes nothing on the destination: every section of the probe is a read
(``info``, ``ps``, ``stats``, ``inspect`` and, optionally, ``system df``).

One machine is sampled with a single ``sh -lc`` program that prints tab
separated sections behind a marker line.  That keeps one SSH session per machine
per sample, gives local and remote hosts the same parser, and avoids depending
on the JSON key names of ``docker --format '{{json .}}'``.

Two properties of the raw data are worth remembering when reading a sample:

* ``.CPUPerc`` is relative to one core and can exceed 100%, so it is only
  comparable between hosts after dividing by the daemon's ``NCPU``.
* ``.MemPerc`` and ``.MemTotal`` are relative to the daemon, which on a
  controller running Docker Desktop or Lima is the virtual machine, not the
  physical host.

Compatibility
-------------

Every section asks Docker for *named template fields* rather than the JSON keys
of ``docker --format '{{json .}}'``.  That is the same surface the deployment
runner already depends on (``docker ps --format`` with tab separators) and it
has been stable since Docker 1.13; the optional disk section additionally needs
``docker system df --format``, which arrived with Docker 20.10.  The templates
are built from the field tuples below, so the contract lives in exactly one
place.

A changing contract must never turn into a wrong number, so three rules apply:

* a section whose command fails degrades to a warning, except ``daemon`` and
  ``containers``, whose failure means the machine cannot be described at all;
* a row whose field count does not match the template is dropped and reported,
  never parsed optimistically, so a field added or removed upstream shows up as
  a warning instead of shifting every column by one;
* the daemon's version and API version travel with every sample, so an
  unexpected number can always be traced to the Docker that produced it.

Availability of the older-daemon fields has not been verified from here: there
was no Docker CLI in the environment this was written in, so the first runs
against real hosts should confirm the field names before the numbers are
trusted.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable

from ..executor import ExecutionError, resolve_executor
from ..model import Machine

DEPLOYMENT_LABEL = "org.dark.deployment.id"
SERVICE_LABEL = "org.dark.service.id"
MARKER = "__dark_metrics__"
ERROR_TAG = "__dark_error__"
PROBE_TIMEOUT_SECONDS = 8.0
DEFAULT_CONCURRENCY = 4

# Sections that make a sample worthless when they fail, as opposed to sections
# that merely lack a few columns.
CRITICAL_SECTIONS = ("daemon", "containers")
# The only Docker subcommands this collector is allowed to issue, ever.  The
# probe is read-only by construction and the tests check both that the script
# stays inside this set and that the set itself is not widened casually.
READ_ONLY_DOCKER_SUBCOMMANDS = frozenset({"info", "version", "ps", "stats", "inspect", "system"})
# Failures that are expected and carry no information for the operator.
BENIGN_SECTION_FAILURES = ("No containers found",)

DAEMON_FIELDS = ("NCPU", "MemTotal", "ContainersRunning", "ServerVersion", "Driver")
VERSION_FIELDS = ("Server.APIVersion",)
CONTAINER_FIELDS = ("ID", "Names", "State", "Status", f'Label "{SERVICE_LABEL}"')
STATS_FIELDS = ("ID", "Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs")
INSPECT_FIELDS = ("Id", "Name", "RestartCount", "State.Status", "State.StartedAt")
DISK_FIELDS = ("Type", "Size", "Reclaimable", "TotalCount")


def _template(fields: Iterable[str]) -> str:
    """Build a tab separated ``--format`` template from named fields."""
    return r"\t".join("{{." + field + "}}" for field in fields)


DAEMON_FORMAT = _template(DAEMON_FIELDS)
VERSION_FORMAT = _template(VERSION_FIELDS)
CONTAINER_FORMAT = _template(CONTAINER_FIELDS)
STATS_FORMAT = _template(STATS_FIELDS)
INSPECT_FORMAT = _template(INSPECT_FIELDS)
DISK_FORMAT = _template(DISK_FIELDS)

_SIZE_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*([A-Za-z]*)$")
_UNITS = {
    "b": 1,
    "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
}


@dataclass(frozen=True)
class ContainerSample:
    """One container of a managed deployment, as seen on its own machine."""

    name: str
    container_id: str
    service_id: str | None
    state: str
    status: str
    restart_count: int | None = None
    started_at: str | None = None
    cpu_percent: float | None = None
    memory_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None
    network_bytes: int | None = None
    block_bytes: int | None = None
    pids: int | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"

    @property
    def has_metrics(self) -> bool:
        """False for containers the daemon cannot sample, e.g. stopped ones."""
        return self.cpu_percent is not None or self.memory_bytes is not None


@dataclass(frozen=True)
class DiskFootprint:
    """One ``docker system df`` row, i.e. the Docker footprint, not host disks."""

    type: str
    size_bytes: int | None
    reclaimable_bytes: int | None
    total_count: int | None


@dataclass(frozen=True)
class MachineMetrics:
    machine_id: str
    execution: str
    reachable: bool
    error: str | None = None
    cores: int | None = None
    memory_total_bytes: int | None = None
    # Daemon wide, so it includes containers that are not part of the
    # deployment; compare `running_containers` against the plan instead.
    containers_running: int | None = None
    server_version: str | None = None
    # The API version is the contract number behind the probe templates; it is
    # kept next to the numbers so a surprising sample can be traced back.
    api_version: str | None = None
    driver: str | None = None
    containers: tuple[ContainerSample, ...] = ()
    disk_footprint: tuple[DiskFootprint, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def by_name(self) -> dict[str, ContainerSample]:
        return {container.name: container for container in self.containers}

    @property
    def running_containers(self) -> tuple[ContainerSample, ...]:
        return tuple(container for container in self.containers if container.running)

    @property
    def unmeasured_containers(self) -> tuple[ContainerSample, ...]:
        """Deployment containers the daemon could not sample."""
        return tuple(container for container in self.containers if not container.has_metrics)

    @property
    def cpu_percent_of_host(self) -> float | None:
        """Sum of per-core container CPU as a share of the daemon's cores."""
        if not self.cores:
            return None
        values = [item.cpu_percent for item in self.containers if item.cpu_percent is not None]
        if not values:
            return None
        return round(sum(values) / self.cores, 2)

    @property
    def memory_bytes(self) -> int | None:
        values = [item.memory_bytes for item in self.containers if item.memory_bytes is not None]
        if not values:
            return None
        return sum(values)

    @property
    def memory_percent_of_host(self) -> float | None:
        if not self.memory_total_bytes or self.memory_bytes is None:
            return None
        return round(100 * self.memory_bytes / self.memory_total_bytes, 2)


@dataclass(frozen=True)
class ContainerRate:
    """Per-container activity derived from two consecutive samples."""

    name: str
    network_bytes_per_second: float | None
    block_bytes_per_second: float | None


def _parse_size(text: str) -> int | None:
    """Parse Docker's human sizes, both decimal (kB) and binary (KiB)."""
    value = text.strip()
    if not value:
        return None
    match = _SIZE_RE.match(value)
    if not match:
        return None
    number, unit = match.group(1), match.group(2).lower()
    if unit and unit not in _UNITS:
        return None
    try:
        return int(float(number) * _UNITS.get(unit, 1))
    except ValueError:
        return None


def _parse_pair(text: str) -> int | None:
    """Sum both halves of Docker's ``received / transmitted`` style pair."""
    parts = [item for item in text.split("/") if item.strip()]
    if not parts:
        return None
    values = [_parse_size(item) for item in parts]
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _parse_percent(text: str) -> float | None:
    value = text.strip().rstrip("%")
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(text: str) -> int | None:
    value = text.strip()
    match = re.match(r"^-?[0-9]+$", value)
    if not match:
        return None
    return int(value)


def _first_token(text: str) -> str:
    parts = text.split()
    return parts[0] if parts else ""


def _normalize_name(text: str) -> str:
    """Join key across sections: ``docker inspect`` prefixes names with ``/``."""
    return text.strip().lstrip("/")


def _optional_text(text: str) -> str | None:
    """Treat an absent field as absent, including Go's ``<no value>`` marker."""
    value = text.strip()
    if not value or value == "<no value>":
        return None
    return value


def _summary(text: str, *, limit: int = 400) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _unreachable(machine: Machine, error: str) -> MachineMetrics:
    return MachineMetrics(machine_id=str(machine.id), execution=str(machine.execution), reachable=False, error=error)


def _section_commands(deployment_id: str, *, include_disk: bool) -> list[tuple[str, tuple[str, ...]]]:
    label_filter = f"label={DEPLOYMENT_LABEL}={deployment_id}"
    inspect_script = (
        f"ids=$(docker ps -aq --filter {shlex.quote(label_filter)}); "
        f"if [ -n \"$ids\" ]; then docker inspect --format {shlex.quote(INSPECT_FORMAT)} $ids; fi"
    )
    commands: list[tuple[str, tuple[str, ...]]] = [
        ("daemon", ("docker", "info", "--format", DAEMON_FORMAT)),
        ("version", ("docker", "version", "--format", VERSION_FORMAT)),
        ("containers", ("docker", "ps", "-a", "--filter", label_filter, "--format", CONTAINER_FORMAT)),
        ("stats", ("docker", "stats", "--no-stream", "--format", STATS_FORMAT)),
        ("inspect", ("sh", "-c", inspect_script)),
    ]
    if include_disk:
        commands.append(("disk", ("docker", "system", "df", "--format", DISK_FORMAT)))
    return commands


def probe_script(deployment_id: str, *, include_disk: bool = False) -> str:
    """Return the read-only POSIX shell program that samples one machine.

    ``docker stats`` accepts no label filter, so the program always asks for
    every container on the daemon and the parser restricts the result to the
    deployment's containers by joining on the container name.
    """
    lines = [
        "capture() {",
        '  kind="$1"; shift',
        "  printf '%s %s\\n' " + shlex.quote(MARKER) + ' "$kind"',
        '  if output=$("$@" 2>&1); then',
        '    if [ -n "$output" ]; then printf \'%s\\n\' "$output"; fi',
        "  else",
        "    printf '%s %s\\n' " + shlex.quote(ERROR_TAG) + ' "$(printf \'%s\' "$output" | tr \'\\n\' \' \')"',
        "  fi",
        "  return 0",
        "}",
        "if ! command -v docker >/dev/null 2>&1; then",
        "  printf '%s %s\\n' " + shlex.quote(MARKER) + " error",
        "  printf '%s\\n' 'docker CLI not found on this machine'",
        "  exit 0",
        "fi",
    ]
    for kind, argv in _section_commands(deployment_id, include_disk=include_disk):
        lines.append("capture " + " ".join([shlex.quote(kind), *(shlex.quote(part) for part in argv)]))
    return "\n".join(lines) + "\n"


def _collect_sections(stdout: str) -> tuple[dict[str, list[str]], dict[str, str], list[str]]:
    sections: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    warnings: list[str] = []
    current: str | None = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(MARKER + " "):
            current = line[len(MARKER) + 1:].strip()
            sections.setdefault(current, [])
            continue
        if line.startswith(ERROR_TAG + " "):
            if current is not None:
                errors[current] = line[len(ERROR_TAG) + 1:].strip()
            continue
        if current is None:
            warnings.append(f"output before any section marker: {line[:60]}")
            continue
        sections[current].append(line)
    return sections, errors, warnings


def parse_probe_output(
    machine: Machine,
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> MachineMetrics:
    """Turn one probe result into a sample; never raises on bad output.

    ``machine`` only needs ``id`` and ``execution``, which keeps this function
    usable from tests without building a full inventory.
    """
    if returncode != 0:
        detail = _summary(stderr) or _summary(stdout) or f"probe exited with {returncode}"
        return _unreachable(machine, detail)

    sections, errors, warnings = _collect_sections(stdout)
    for name in CRITICAL_SECTIONS:
        if name in errors:
            return _unreachable(machine, errors[name])
    for name, message in errors.items():
        if any(benign in message for benign in BENIGN_SECTION_FAILURES):
            continue
        warnings.append(f"{name} section failed: {message}")

    if "error" in sections:
        return _unreachable(machine, _summary(" ".join(sections["error"])) or "probe reported an error")
    if not sections:
        return _unreachable(machine, "probe returned no recognizable output")

    cores = memory_total = containers_running = None
    server_version = driver = None
    for line in sections.get("daemon", ()):
        parts = line.split("\t")
        if len(parts) != len(DAEMON_FIELDS):
            warnings.append(f"unexpected daemon row: {line[:60]}")
            continue
        cores = _parse_int(parts[0])
        memory_total = _parse_size(parts[1])
        containers_running = _parse_int(parts[2])
        server_version = _optional_text(parts[3])
        driver = _optional_text(parts[4])

    api_version = None
    for line in sections.get("version", ()):
        api_version = _optional_text(line.split("\t")[0])

    observed: dict[str, dict[str, Any]] = {}
    for line in sections.get("containers", ()):
        parts = line.split("\t")
        if len(parts) != len(CONTAINER_FIELDS):
            warnings.append(f"unexpected container row: {line[:60]}")
            continue
        container_id, name, state, status, service_id = parts
        observed[_normalize_name(name)] = {
            "container_id": container_id,
            "state": state,
            "status": status,
            "service_id": _optional_text(service_id),
        }

    for line in sections.get("stats", ()):
        parts = line.split("\t")
        if len(parts) != len(STATS_FIELDS):
            warnings.append(f"unexpected stats row: {line[:60]}")
            continue
        container_id, name, cpu, memory, memory_percent, network, block, pids = parts
        key = _normalize_name(name)
        entry = observed.get(key)
        if entry is None:
            warnings.append(f"stats row outside the deployment: {key}")
            continue
        usage, _, limit = memory.partition("/")
        entry.update({
            "container_id": entry["container_id"] or container_id,
            "cpu_percent": _parse_percent(cpu),
            "memory_bytes": _parse_size(usage),
            "memory_limit_bytes": _parse_size(limit),
            "memory_percent": _parse_percent(memory_percent),
            "network_bytes": _parse_pair(network),
            "block_bytes": _parse_pair(block),
            "pids": _parse_int(pids),
        })

    for line in sections.get("inspect", ()):
        parts = line.split("\t")
        if len(parts) != len(INSPECT_FIELDS):
            warnings.append(f"unexpected inspect row: {line[:60]}")
            continue
        _, name, restarts, _, started_at = parts
        key = _normalize_name(name)
        entry = observed.get(key)
        if entry is None:
            warnings.append(f"inspect row outside the deployment: {key}")
            continue
        entry.update({
            "restart_count": _parse_int(restarts),
            "started_at": _optional_text(started_at),
        })

    disk: list[DiskFootprint] = []
    for line in sections.get("disk", ()):
        parts = line.split("\t")
        if len(parts) != len(DISK_FIELDS):
            warnings.append(f"unexpected disk row: {line[:60]}")
            continue
        kind, size, reclaimable, count = parts
        disk.append(DiskFootprint(kind, _parse_size(_first_token(size)), _parse_size(_first_token(reclaimable)), _parse_int(count)))

    containers = tuple(
        ContainerSample(
            name=name,
            container_id=str(entry["container_id"]),
            service_id=entry["service_id"],
            state=str(entry["state"]),
            status=str(entry["status"]),
            **{key: entry[key] for key in (
                "restart_count", "started_at", "cpu_percent", "memory_bytes", "memory_limit_bytes",
                "memory_percent", "network_bytes", "block_bytes", "pids",
            ) if key in entry},
        )
        for name, entry in sorted(observed.items())
    )

    return MachineMetrics(
        machine_id=str(machine.id),
        execution=str(machine.execution),
        reachable=True,
        cores=cores,
        memory_total_bytes=memory_total,
        containers_running=containers_running,
        server_version=server_version,
        api_version=api_version,
        driver=driver,
        containers=containers,
        disk_footprint=tuple(disk),
        warnings=tuple(warnings),
    )


def daemon_key(machine: Machine) -> str:
    """Identify the Docker daemon a machine is sampled through.

    ``local`` and ``docker-lab`` machines run on the controller daemon, so they
    collapse into one key.  Whether a ``docker-lab`` plan really maps several
    inventory machines onto one daemon is still an inference from the v3
    resolver and has not been verified against a running lab.

    ``auto`` needs no branch here: ``build_plan`` already rewrites it to
    ``local`` or ``ssh``, so a resolved plan never carries it.
    """
    if str(machine.execution) in {"local", "docker-lab"}:
        return "local"
    ssh = getattr(machine, "ssh", None)
    port = getattr(ssh, "port", None) if ssh is not None else None
    return f"ssh:{machine.management_address}:{port}"


def sample_machine(
    plan: Any,
    machine: Machine,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    include_disk: bool = False,
) -> MachineMetrics:
    """Sample one machine without changing anything on it."""
    script = probe_script(plan.deployment_id, include_disk=include_disk)
    try:
        executor = resolve_executor(machine)
        result = executor.run(("sh", "-lc", script), timeout=timeout)
    except subprocess.TimeoutExpired:
        return _unreachable(machine, f"probe timed out after {timeout:.0f}s")
    except ExecutionError as exc:
        return _unreachable(machine, _summary(str(exc)))
    except OSError as exc:
        return _unreachable(machine, _summary(f"cannot execute probe: {exc}"))
    return parse_probe_output(machine, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def sample_machines(
    plan: Any,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    include_disk: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> tuple[MachineMetrics, ...]:
    """Sample every machine of the plan, one SSH session per machine.

    Results keep the plan's machine order.  Remote machines are sampled in
    parallel because a single unreachable host would otherwise delay the whole
    panel by its timeout.
    """
    machines = tuple(plan.machines)
    if not machines:
        return ()
    if concurrency <= 1 or len(machines) == 1:
        return tuple(sample_machine(plan, machine, timeout=timeout, include_disk=include_disk) for machine in machines)
    workers = max(1, min(concurrency, len(machines)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return tuple(pool.map(lambda machine: sample_machine(plan, machine, timeout=timeout, include_disk=include_disk), machines))


def container_rates(
    previous: MachineMetrics | None,
    current: MachineMetrics,
    *,
    elapsed_seconds: float,
) -> tuple[ContainerRate, ...]:
    """Per-container activity between two samples of the same machine.

    ``NetIO`` and ``BlockIO`` are cumulative counters, so a rate needs the
    previous sample.  A rate is refused (``None``) when the container is new,
    when it was recreated or restarted, when the counter went backwards, or
    when the samples are not separated in time — all of which would otherwise
    produce a spike that looks like a real incident.
    """
    if previous is None or elapsed_seconds <= 0:
        return tuple(ContainerRate(item.name, None, None) for item in current.containers)
    before = previous.by_name
    rates: list[ContainerRate] = []
    for item in current.containers:
        earlier = before.get(item.name)
        if earlier is None or earlier.started_at != item.started_at:
            rates.append(ContainerRate(item.name, None, None))
            continue
        rates.append(ContainerRate(
            item.name,
            _rate(earlier.network_bytes, item.network_bytes, elapsed_seconds),
            _rate(earlier.block_bytes, item.block_bytes, elapsed_seconds),
        ))
    return tuple(rates)


def _rate(earlier: int | None, current: int | None, elapsed_seconds: float) -> float | None:
    if earlier is None or current is None or current < earlier:
        return None
    return round((current - earlier) / elapsed_seconds, 2)
