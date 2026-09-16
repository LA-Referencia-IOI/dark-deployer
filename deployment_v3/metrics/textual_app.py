"""Read-only Textual panel for the Docker metrics of managed deployments.

Only this module imports Textual, so the collector and its parser stay usable by
a non-interactive exporter or a test runner without the terminal library.

The panel observes and never acts.  It has no lifecycle buttons, no confirmation
dialog and no log viewer: everything it issues is the read-only probe of
``collector.sample_machine``, which means it is safe to leave open next to the
operations console or to hand to someone who must not be able to press "remove".

Two clocks, deliberately different:

* machines are sampled once a minute, or on demand, because one sample costs a
  full Docker probe per machine, and
* the service inventory of the selected deployment is read only on mount, on
  selection and on demand, because it costs one ``docker ps`` per group over
  SSH.

The age of every sample is always on screen, and a machine that cannot be
sampled keeps its last values marked as stale instead of showing a zero.

The layout assumes a reasonably wide terminal — the deployments and detail
panes are fixed width, so the machines table needs roughly 120 columns to show
all of its columns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..console import ConsoleSnapshot, snapshot
from ..runner import ApplyError, managed_plan
from .collector import (
    PROBE_TIMEOUT_SECONDS,
    ContainerRate,
    MachineMetrics,
    container_rates,
    daemon_key,
    sample_machine,
)


REFRESH_MACHINES_SECONDS = 60
MACHINE_COLUMNS = ("MACHINE", "VIA", "STATE", "AGE", "CPU", "MEM", "CONT", "RST")
CONTAINER_COLUMNS = ("CONTAINER", "SERVICE", "STATE", "CPU", "MEMORY", "NET/s", "BLOCK/s", "PIDS", "RESTARTS")
EMPTY = "—"


def _textual():
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import DataTable, Footer, Header, Label, Static
    except ModuleNotFoundError as exc:
        raise ApplyError(
            "the deployment metrics panel requires Textual; install it with "
            "venv/bin/python -m pip install -r requirements-tui.txt"
        ) from exc
    return App, ComposeResult, Horizontal, Vertical, DataTable, Footer, Header, Label, Static


def format_bytes(value: int | float | None) -> str:
    """Human size for a byte count, with an explicit gap for missing data."""
    if value is None:
        return EMPTY
    number = float(value)
    for unit, scale in (("TiB", 1024 ** 4), ("GiB", 1024 ** 3), ("MiB", 1024 ** 2), ("KiB", 1024)):
        if abs(number) >= scale:
            return f"{number / scale:.1f} {unit}"
    return f"{number:.0f} B"


def format_age(seconds: float | None) -> str:
    """Age of a sample, kept coarse so the column stays narrow."""
    if seconds is None:
        return EMPTY
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.0f}h"


def machine_state(metrics: MachineMetrics | None, *, sampling: bool = False) -> str:
    """One word for the machine, reusing the vocabulary of the other console.

    A container that is deliberately stopped — a one-shot migration job, for
    instance — does not degrade the machine; only a container the daemon cannot
    measure, or one stuck in a restart loop, does.
    """
    if sampling:
        return "sampling"
    if metrics is None:
        return "unknown"
    if not metrics.reachable:
        return "unreachable"
    if any(item.state == "restarting" for item in metrics.containers):
        return "degraded"
    if any(item.running and not item.has_metrics for item in metrics.containers):
        return "degraded"
    if not metrics.running_containers:
        return "inactive"
    return "active"


def container_counts(metrics: MachineMetrics) -> str:
    """Deployment containers running over deployment containers found."""
    running = len(metrics.running_containers)
    return f"{running}/{len(metrics.containers)}"


def restart_total(metrics: MachineMetrics) -> str:
    values = [item.restart_count for item in metrics.containers if item.restart_count is not None]
    if not values:
        return EMPTY
    return str(sum(values))


def machine_row(
    metrics: MachineMetrics | None,
    *,
    machine_id: str,
    execution: str,
    sampling: bool = False,
    age_seconds: float | None = None,
) -> tuple[str, ...]:
    """Build the machines table row, with the age of the sample in plain sight."""
    state = machine_state(metrics, sampling=sampling)
    if metrics is None or not metrics.reachable:
        # Four leading cells and one gap per remaining column, so the row always
        # matches the table definition.
        return (machine_id, execution, state, format_age(age_seconds), *(EMPTY,) * (len(MACHINE_COLUMNS) - 4))
    return (
        machine_id,
        execution,
        state,
        format_age(age_seconds),
        EMPTY if metrics.cpu_percent_of_host is None else f"{metrics.cpu_percent_of_host:.1f}%",
        # Absolute memory only: the daemon total belongs to the detail pane, and
        # keeping it out of the row leaves room for every column.
        format_bytes(metrics.memory_bytes),
        container_counts(metrics),
        restart_total(metrics),
    )


def container_rows(
    metrics: MachineMetrics | None,
    rates: tuple[ContainerRate, ...] = (),
) -> tuple[tuple[str, ...], ...]:
    """Build the per-container rows of one machine."""
    if metrics is None:
        return ()
    per_second = {item.name: item for item in rates}
    rows: list[tuple[str, ...]] = []
    for item in metrics.containers:
        rate = per_second.get(item.name)
        if item.memory_bytes is None:
            memory = EMPTY
        elif item.memory_percent is None:
            memory = format_bytes(item.memory_bytes)
        else:
            memory = f"{format_bytes(item.memory_bytes)} {item.memory_percent:.1f}%"
        rows.append((
            item.name,
            item.service_id or EMPTY,
            item.state,
            EMPTY if item.cpu_percent is None else f"{item.cpu_percent:.1f}%",
            memory,
            EMPTY if rate is None or rate.network_bytes_per_second is None else f"{format_bytes(rate.network_bytes_per_second)}/s",
            EMPTY if rate is None or rate.block_bytes_per_second is None else f"{format_bytes(rate.block_bytes_per_second)}/s",
            EMPTY if item.pids is None else str(item.pids),
            EMPTY if item.restart_count is None else str(item.restart_count),
        ))
    return tuple(rows)


def machine_detail(
    metrics: MachineMetrics | None,
    *,
    machine_id: str,
    execution: str,
    services: tuple[dict[str, object], ...] = (),
    shared_with: tuple[str, ...] = (),
    sampling: bool = False,
) -> str:
    """Explain one machine without repeating the numbers of the table."""
    header = f"[b]{machine_id}[/b]  ({execution})"
    state = machine_state(metrics, sampling=sampling)
    lines = [header, f"State: {state}"]
    if shared_with:
        lines.append(f"Shares this Docker daemon with: {', '.join(shared_with)}")
    if metrics is None:
        lines.append("No sample yet.")
    elif not metrics.reachable:
        lines.append(f"[red]Cannot sample:[/red] {metrics.error or 'unknown error'}")
    else:
        lines.append(f"Docker: {metrics.server_version or '?'}  API: {metrics.api_version or '?'}  driver: {metrics.driver or '?'}")
        lines.append(f"Cores: {metrics.cores or '?'}   Memory: {format_bytes(metrics.memory_total_bytes)}")
        lines.append(f"Containers on this daemon: {metrics.containers_running if metrics.containers_running is not None else '?'}")
        lines.append("CPU and memory are shares of this daemon, not of the physical host.")
        if metrics.warnings:
            lines.append(f"[yellow]Warnings:[/yellow] {len(metrics.warnings)}")
            lines.extend(f"  {warning}" for warning in metrics.warnings[:3])
    lines.append("")
    lines.append("SERVICES")
    if not services:
        lines.append("  No managed service is placed on this machine.")
    else:
        lines.extend(f"  {row['service']}: {row['state']}" for row in services)
    return "\n".join(lines)


def _selected_row_key(table: Any) -> str | None:
    if table.row_count == 0 or table.cursor_row is None:
        return None
    try:
        return str(table.get_row_at(table.cursor_row)[0])
    except (IndexError, TypeError):
        return None


@dataclass(frozen=True)
class SampleStamp:
    metrics: MachineMetrics
    observed_at: float


def _make_textual_app(project_root: Path):
    """Build the panel so a test can drive it without a terminal."""
    App, ComposeResult, Horizontal, Vertical, DataTable, Footer, Header, Label, Static = _textual()

    class MetricsApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; }
        #deployments { width: 38; border: round $primary; }
        #machines { width: 1fr; border: round $primary; }
        #detail { width: 42; border: round $primary; padding: 1; }
        #containers-pane { height: 13; border: round $primary; padding: 0 1; }
        #status { height: 2; padding: 0 1; }
        DataTable { height: 1fr; }
        .pane-title { height: 1; padding: 0 1; text-style: bold; }
        """
        BINDINGS = [
            ("tab", "focus_next", "Next pane"),
            ("shift+tab", "focus_previous", "Previous pane"),
            ("r", "refresh", "Refresh"),
            ("m", "resample", "Sample machine"),
            ("question_mark", "help", "Help"),
            ("q", "quit", "Quit"),
        ]
        TITLE = "read-only Docker metrics"

        def __init__(self) -> None:
            super().__init__()
            self.deployment_id: str | None = None
            self.data = ConsoleSnapshot((), ())
            self.plan: Any = None
            self.stamps: dict[str, SampleStamp] = {}
            self.rates: dict[str, tuple[ContainerRate, ...]] = {}
            self.sampling: set[str] = set()
            self.snapshot_age: float | None = None

        def compose(self) -> ComposeResult:
            yield Header(name="dARK deployment metrics", show_clock=False)
            with Horizontal(id="content"):
                with Vertical(id="deployments"):
                    yield Label("DEPLOYMENTS", classes="pane-title")
                    yield DataTable(id="deployment-table", cursor_type="row")
                with Vertical(id="machines"):
                    yield Label("MACHINES  (read-only, sampled each minute)", classes="pane-title")
                    yield DataTable(id="machine-table", cursor_type="row")
                with Vertical(id="detail"):
                    yield Label("DETAIL", classes="pane-title")
                    yield Static("Loading…", id="machine-detail")
            with Vertical(id="containers-pane"):
                yield Label("CONTAINERS OF THE SELECTED MACHINE", classes="pane-title")
                yield DataTable(id="container-table", cursor_type="row")
            yield Static("Loading managed deployments…", id="status")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#deployment-table", DataTable).add_columns("Deployment", "State", "Run")
            self.query_one("#machine-table", DataTable).add_columns(*MACHINE_COLUMNS)
            self.query_one("#container-table", DataTable).add_columns(*CONTAINER_COLUMNS)
            self.action_refresh()
            self.set_interval(REFRESH_MACHINES_SECONDS, self._tick)

        # -- reading -----------------------------------------------------

        def _status(self, value: str) -> None:
            self.query_one("#status", Static).update(value)

        def _selected_machine_id(self) -> str | None:
            return _selected_row_key(self.query_one("#machine-table", DataTable))

        def _services_of(self, machine_id: str | None) -> tuple[dict[str, object], ...]:
            if not machine_id:
                return ()
            return tuple(row for row in self.data.services if str(row.get("machine")) == machine_id)

        def _shared_daemon_with(self, machine_id: str | None) -> tuple[str, ...]:
            if self.plan is None or not machine_id:
                return ()
            current = {machine.id: machine for machine in self.plan.machines}.get(machine_id)
            if current is None:
                return ()
            key = daemon_key(current)
            return tuple(sorted(
                machine.id for machine in self.plan.machines
                if machine.id != machine_id and daemon_key(machine) == key
            ))

        def action_refresh(self) -> None:
            """Read the deployment inventory and start a metrics round."""
            previous = self.deployment_id
            try:
                self.data = snapshot(project_root, self.deployment_id)
                if self.data.deployments and not self.deployment_id:
                    self.deployment_id = str(self.data.deployments[0]["deployment_id"])
                    self.data = snapshot(project_root, self.deployment_id)
                self.snapshot_age = time.monotonic()
                self.plan = managed_plan(project_root, self.deployment_id) if self.deployment_id else None
            except ApplyError as exc:
                self._status(f"[red]Refresh failed:[/red] {exc}")
                return
            if self.deployment_id != previous:
                # Samples of another deployment would be attributed to the wrong machines.
                self.stamps = {}
                self.rates = {}
            self._render_deployments()
            self._sample_machines()

        def _tick(self) -> None:
            self._sample_machines()

        def _sample_machines(self, only: str | None = None) -> None:
            if self.plan is None:
                self._render()
                return
            for machine in self.plan.machines:
                if only is not None and machine.id != only:
                    continue
                if machine.id in self.sampling:
                    continue
                self.sampling.add(machine.id)
                self.run_worker(
                    lambda machine=machine: self._collect(machine),
                    name=f"metrics-{machine.id}", group=f"metrics:{machine.id}",
                    exclusive=True, thread=True,
                )
            self._render()

        def _collect(self, machine: Any) -> None:
            """Sample one machine off the UI loop; the probe may open SSH."""
            sample = sample_machine(self.plan, machine, timeout=PROBE_TIMEOUT_SECONDS)
            self.call_from_thread(self._apply, machine.id, sample)

        def _apply(self, machine_id: str, sample: MachineMetrics) -> None:
            previous = self.stamps.get(machine_id)
            observed_at = time.monotonic()
            self.rates[machine_id] = container_rates(
                previous.metrics if previous else None,
                sample,
                elapsed_seconds=(observed_at - previous.observed_at) if previous else 0.0,
            )
            self.stamps[machine_id] = SampleStamp(sample, observed_at)
            self.sampling.discard(machine_id)
            self._render()

        # -- rendering ---------------------------------------------------

        def _widget(self, selector: str) -> Any | None:
            """Look a widget up without assuming the app is still mounted.

            Highlight and selection events can be delivered while the panel is
            being torn down, and a query that raises there would take the whole
            application with it.
            """
            found = self.query(selector)
            return found.first() if found else None

        def _render(self) -> None:
            if self._widget("#machine-table") is None:
                return
            self._render_machines()
            self._render_selection()

        def _render_selection(self) -> None:
            """Panes that depend on the current selection only."""
            if self._widget("#machine-detail") is None:
                return
            self._render_detail()
            self._render_containers()
            self._render_status()

        def _render_deployments(self) -> None:
            table = self._widget("#deployment-table")
            if table is None:
                return
            table.clear(columns=False)
            for item in self.data.deployments:
                table.add_row(
                    str(item["deployment_id"]), str(item["state"]), f"{item['running']}/{item['services']}",
                    key=str(item["deployment_id"]),
                )

        def _render_machines(self) -> None:
            table = self._widget("#machine-table")
            if table is None:
                return
            selected = self._selected_machine_id()
            table.clear(columns=False)
            if self.plan is None:
                return
            now = time.monotonic()
            for machine in self.plan.machines:
                stamp = self.stamps.get(machine.id)
                table.add_row(
                    *machine_row(
                        stamp.metrics if stamp else None,
                        machine_id=machine.id,
                        execution=str(machine.execution),
                        sampling=machine.id in self.sampling,
                        age_seconds=None if stamp is None else now - stamp.observed_at,
                    ),
                    key=machine.id,
                )
            self._restore_cursor(table, selected)

        def _render_containers(self) -> None:
            table = self._widget("#container-table")
            if table is None:
                return
            selected = _selected_row_key(table)
            table.clear(columns=False)
            machine_id = self._selected_machine_id()
            stamp = self.stamps.get(machine_id) if machine_id else None
            for row in container_rows(stamp.metrics if stamp else None, self.rates.get(machine_id or "", ())):
                table.add_row(*row, key=row[0])
            self._restore_cursor(table, selected)

        def _restore_cursor(self, table: Any, key: str | None) -> None:
            """Keep the selection steady across refreshes."""
            if key is None or table.row_count == 0:
                return
            for index, row_key in enumerate(table.rows):
                if str(row_key.value) == key:
                    table.move_cursor(row=index)
                    return

        def _render_detail(self) -> None:
            pane = self._widget("#machine-detail")
            if pane is None:
                return
            machine_id = self._selected_machine_id()
            machine = None
            if self.plan is not None and machine_id:
                machine = next((item for item in self.plan.machines if item.id == machine_id), None)
            stamp = self.stamps.get(machine_id) if machine_id else None
            pane.update(machine_detail(
                stamp.metrics if stamp else None,
                machine_id=machine_id or "-",
                execution=str(machine.execution) if machine else "?",
                services=self._services_of(machine_id),
                shared_with=self._shared_daemon_with(machine_id),
                sampling=bool(machine_id and machine_id in self.sampling),
            ))

        def _render_status(self) -> None:
            if self.plan is None:
                self._status("No managed deployments found.  q quits.")
                return
            unhealthy = [m.id for m in self.plan.machines if machine_state(self.stamps[m.id].metrics if m.id in self.stamps else None, sampling=m.id in self.sampling) == "unreachable"]
            services_age = "not read yet" if self.snapshot_age is None else f"read {format_age(time.monotonic() - self.snapshot_age)} ago"
            summary = [
                str(self.deployment_id or "-"),
                f"{len(self.plan.machines)} machine(s)",
                f"services {services_age}",
                f"machines sampled each {REFRESH_MACHINES_SECONDS}s",
            ]
            if self.sampling:
                summary.append(f"sampling {len(self.sampling)}")
            if unhealthy:
                summary.append(f"[red]{len(unhealthy)} unreachable: {', '.join(unhealthy)}[/red]")
            summary.append("r refresh · m sample machine · q quit  (read-only)")
            self._status("  ·  ".join(summary))

        # -- events ------------------------------------------------------

        def on_data_table_row_selected(self, event: Any) -> None:
            if event.data_table.id == "deployment-table":
                self.deployment_id = str(event.row_key.value)
                self.action_refresh()
            else:
                self._render_selection()

        def on_data_table_row_highlighted(self, event: Any) -> None:
            if event.data_table.id == "machine-table":
                self._render_selection()

        def action_resample(self) -> None:
            machine_id = self._selected_machine_id()
            if machine_id:
                self._sample_machines(only=machine_id)

        def action_help(self) -> None:
            self._status(
                "Read-only: this panel never starts, stops or removes anything.  "
                "Tab moves between panes · r re-reads services and samples machines · "
                "m samples the selected machine · q quits"
            )

    return MetricsApp()


def run_metrics_tui(project_root: Path) -> None:
    """Run the read-only metrics panel; Textual is imported only for this command."""
    _make_textual_app(project_root).run()
