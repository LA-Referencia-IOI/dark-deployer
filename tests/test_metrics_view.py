"""Coverage for the read-only metrics panel.

The formatting and state helpers are pure, so they are covered directly.  The
pilot test drives the real application when Textual is installed, which is an
optional dependency, and is skipped otherwise.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment_v3.metrics import textual_app as view
from deployment_v3.metrics.collector import ContainerRate, ContainerSample, MachineMetrics
from deployment_v3.planner import build_plan
from deployment_v3.render import render_plan


ROOT = Path(__file__).resolve().parents[1]

try:
    import textual  # noqa: F401

    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - depends on the optional extra
    TEXTUAL_AVAILABLE = False


def sample_metrics(**overrides) -> MachineMetrics:
    containers = overrides.pop("containers", (
        ContainerSample(
            name="validator01", container_id="abc123", service_id="validator01", state="running",
            status="Up 2 hours", restart_count=0, started_at="2026-09-16T08:11:00Z",
            cpu_percent=42.5, memory_bytes=int(1.5 * 1024 ** 3), memory_percent=7.91, pids=64,
        ),
        ContainerSample(
            name="minter-migrate", container_id="def456", service_id="minter-migrate", state="exited",
            status="Exited (0) 3 hours ago", restart_count=0, started_at="2026-09-16T05:00:00Z",
        ),
    ))
    values = {
        "machine_id": "local",
        "execution": "local",
        "reachable": True,
        "cores": 16,
        "memory_total_bytes": 16750372454,
        "containers_running": 7,
        "server_version": "27.3.1",
        "api_version": "1.51",
        "driver": "overlay2",
        "containers": containers,
    }
    values.update(overrides)
    return MachineMetrics(**values)


class FormattingTests(unittest.TestCase):
    def test_bytes_are_reported_in_binary_units(self):
        self.assertEqual(view.format_bytes(None), view.EMPTY)
        self.assertEqual(view.format_bytes(0), "0 B")
        self.assertEqual(view.format_bytes(1024), "1.0 KiB")
        self.assertEqual(view.format_bytes(int(1.5 * 1024 ** 3)), "1.5 GiB")

    def test_age_stays_coarse(self):
        self.assertEqual(view.format_age(None), view.EMPTY)
        self.assertEqual(view.format_age(12), "12s")
        self.assertEqual(view.format_age(300), "5m")
        self.assertEqual(view.format_age(7200), "2h")


class MachineStateTests(unittest.TestCase):
    def test_sampling_and_unknown_are_distinct_from_failure(self):
        self.assertEqual(view.machine_state(None, sampling=True), "sampling")
        self.assertEqual(view.machine_state(None), "unknown")

    def test_unreachable_is_not_reported_as_empty(self):
        metrics = sample_metrics(reachable=False, error="ssh: connect failed", containers=())
        self.assertEqual(view.machine_state(metrics), "unreachable")
        self.assertEqual(metrics.error, "ssh: connect failed")

    def test_a_deliberately_stopped_job_does_not_degrade_the_machine(self):
        """minter-migrate exits by design; that is not an incident."""
        self.assertEqual(view.machine_state(sample_metrics()), "active")

    def test_running_container_without_metrics_degrades(self):
        metrics = sample_metrics(containers=(
            ContainerSample(name="validator01", container_id="abc", service_id="validator01", state="running", status="Up"),
        ))
        self.assertEqual(view.machine_state(metrics), "degraded")

    def test_restart_loop_degrades(self):
        metrics = sample_metrics(containers=(
            ContainerSample(name="validator01", container_id="abc", service_id="validator01", state="restarting", status="Restarting"),
        ))
        self.assertEqual(view.machine_state(metrics), "degraded")

    def test_nothing_running_is_inactive(self):
        metrics = sample_metrics(containers=(
            ContainerSample(name="minter-migrate", container_id="def", service_id="minter-migrate", state="exited", status="Exited"),
        ))
        self.assertEqual(view.machine_state(metrics), "inactive")


class MachineRowTests(unittest.TestCase):
    def test_row_carries_the_age_and_the_daemon_share(self):
        row = view.machine_row(sample_metrics(), machine_id="local", execution="local", age_seconds=12)
        self.assertEqual(len(row), len(view.MACHINE_COLUMNS))
        self.assertEqual(row[0], "local")
        self.assertEqual(row[1], "local")
        self.assertEqual(row[2], "active")
        self.assertEqual(row[3], "12s")
        # CPU is one core's worth of percent per container, divided by the daemon's cores.
        self.assertEqual(row[4], "2.7%")
        self.assertEqual(row[5], "1.5 GiB")
        self.assertEqual(row[6], "1/2")
        self.assertEqual(row[7], "0")

    def test_unreachable_row_keeps_the_state_and_blanks_the_numbers(self):
        metrics = sample_metrics(reachable=False, error="timeout", containers=())
        row = view.machine_row(metrics, machine_id="local", execution="local", age_seconds=5)
        self.assertEqual(len(row), len(view.MACHINE_COLUMNS))
        self.assertEqual(row[2], "unreachable")
        self.assertEqual(row[3], "5s")
        self.assertEqual(row[4:], (view.EMPTY,) * 4)

    def test_sampling_row_is_explicit(self):
        row = view.machine_row(None, machine_id="local", execution="local", sampling=True)
        self.assertEqual(len(row), len(view.MACHINE_COLUMNS))
        self.assertEqual(row[2], "sampling")
        self.assertEqual(row[4:], (view.EMPTY,) * 4)

    def test_every_row_shape_matches_the_table(self):
        shapes = (
            view.machine_row(None, machine_id="local", execution="local"),
            view.machine_row(None, machine_id="local", execution="local", sampling=True),
            view.machine_row(sample_metrics(), machine_id="local", execution="local"),
            view.machine_row(sample_metrics(reachable=False, containers=()), machine_id="local", execution="local"),
        )
        for row in shapes:
            self.assertEqual(len(row), len(view.MACHINE_COLUMNS), row)


class ContainerRowTests(unittest.TestCase):
    def test_stopped_container_has_a_row_without_numbers(self):
        rows = view.container_rows(sample_metrics())
        self.assertEqual(len(rows), 2)
        stopped = next(row for row in rows if row[0] == "minter-migrate")
        self.assertEqual(stopped[2], "exited")
        self.assertEqual(stopped[3], view.EMPTY)
        self.assertEqual(stopped[5], view.EMPTY)

    def test_rates_are_attached_by_container_name(self):
        rates = (
            ContainerRate("validator01", 10_240.0, 2_048_000.0),
            ContainerRate("minter-migrate", None, None),
        )
        rows = view.container_rows(sample_metrics(), rates)
        running = next(row for row in rows if row[0] == "validator01")
        self.assertEqual(running[5], "10.0 KiB/s")
        self.assertEqual(running[6], "2.0 MiB/s")
        stopped = next(row for row in rows if row[0] == "minter-migrate")
        self.assertEqual(stopped[5], view.EMPTY)

    def test_no_sample_means_no_rows(self):
        self.assertEqual(view.container_rows(None), ())

    def test_memory_without_a_percentage_does_not_invent_one(self):
        metrics = sample_metrics(containers=(
            ContainerSample(
                name="storage-1-kubo", container_id="x", service_id="storage-1-kubo",
                state="running", status="Up 1 hour", memory_bytes=1024 ** 2,
            ),
        ))
        rows = view.container_rows(metrics)
        self.assertEqual(rows[0][4], "1.0 MiB")


def busy_metrics() -> MachineMetrics:
    """Three containers with distinct load, plus one the daemon cannot measure."""
    return sample_metrics(containers=(
        ContainerSample(name="quiet", container_id="1", service_id="quiet", state="running",
                        status="Up", cpu_percent=0.4, memory_bytes=100 * 1024 ** 2),
        ContainerSample(name="hammer", container_id="2", service_id="hammer", state="running",
                        status="Up", cpu_percent=88.0, memory_bytes=50 * 1024 ** 2),
        ContainerSample(name="fat", container_id="3", service_id="fat", state="running",
                        status="Up", cpu_percent=1.0, memory_bytes=4 * 1024 ** 3),
        ContainerSample(name="migrate", container_id="4", service_id="migrate", state="exited",
                        status="Exited (0)"),
    ))


class ContainerOrderTests(unittest.TestCase):
    def test_name_order_is_the_default(self):
        rows = view.container_rows(busy_metrics())
        self.assertEqual([row[0] for row in rows], ["fat", "hammer", "migrate", "quiet"])

    def test_cpu_order_is_busiest_first(self):
        rows = view.container_rows(busy_metrics(), order="cpu")
        self.assertEqual([row[0] for row in rows], ["hammer", "fat", "quiet", "migrate"])

    def test_memory_order_is_largest_first(self):
        rows = view.container_rows(busy_metrics(), order="memory")
        self.assertEqual([row[0] for row in rows], ["fat", "quiet", "hammer", "migrate"])

    def test_unmeasurable_containers_go_last_under_either_metric(self):
        """A stopped container has no value to compare, so it is not sorted as zero."""
        for order in ("cpu", "memory"):
            rows = view.container_rows(busy_metrics(), order=order)
            self.assertEqual(rows[-1][0], "migrate")
            self.assertEqual(rows[-1][3], view.EMPTY)

    def test_ties_fall_back_to_the_name(self):
        metrics = sample_metrics(containers=(
            ContainerSample(name="beta", container_id="a", service_id="beta", state="running", status="Up", cpu_percent=5.0),
            ContainerSample(name="alpha", container_id="b", service_id="alpha", state="running", status="Up", cpu_percent=5.0),
        ))
        rows = view.container_rows(metrics, order="cpu")
        self.assertEqual([row[0] for row in rows], ["alpha", "beta"])

    def test_an_unknown_order_falls_back_to_the_name(self):
        rows = view.container_rows(busy_metrics(), order="nonsense")
        self.assertEqual([row[0] for row in rows], ["fat", "hammer", "migrate", "quiet"])

    def test_the_cycle_covers_both_metrics(self):
        self.assertEqual(view.CONTAINER_ORDERS, ("name", "cpu", "memory"))
        for order in view.CONTAINER_ORDERS:
            self.assertIn(order, view.CONTAINER_SORT_LABELS)


class MachineDetailTests(unittest.TestCase):
    def test_unreachable_machine_explains_itself_and_keeps_services(self):
        metrics = sample_metrics(reachable=False, error="probe timed out after 8s", containers=())
        text = view.machine_detail(
            metrics, machine_id="blockchain-a", execution="ssh",
            services=({"service": "validator01", "state": "unreachable"},),
        )
        self.assertIn("Cannot sample", text)
        self.assertIn("probe timed out after 8s", text)
        self.assertIn("validator01: unreachable", text)

    def test_detail_names_the_daemon_and_its_denominators(self):
        text = view.machine_detail(sample_metrics(), machine_id="local", execution="local")
        self.assertIn("27.3.1", text)
        # The API version is the contract behind the probe, so it stays visible.
        self.assertIn("API: 1.51", text)
        self.assertIn("Cores: 16", text)
        self.assertIn("shares of this daemon", text)

    def test_shared_daemon_is_disclosed(self):
        text = view.machine_detail(sample_metrics(), machine_id="local", execution="local", shared_with=("lab-1",))
        self.assertIn("Shares this Docker daemon with: lab-1", text)

    def test_machine_without_services_says_so(self):
        text = view.machine_detail(sample_metrics(), machine_id="local", execution="local")
        self.assertIn("No managed service is placed on this machine.", text)


class WarningSummaryTests(unittest.TestCase):
    """One noisy section must not push the service list out of the pane."""

    def test_repeated_warnings_collapse_to_one_line(self):
        warnings = tuple(
            f"inspect row: 1 field(s), expected 5 — Docker left the tab separator unexpanded | {index}"
            for index in range(21)
        ) + ("daemon row: 1 field(s), expected 5 — Docker left the tab separator unexpanded | row",)
        summary = view.summarise_warnings(warnings)
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0], "inspect row: 1 field(s), expected 5 — Docker left the tab separator unexpanded (x21)")
        self.assertIn("daemon row", summary[1])

    def test_a_single_warning_keeps_its_raw_evidence(self):
        warning = "container row: 4 field(s), expected 5 | 1a2b\tname\trunning"
        self.assertEqual(view.summarise_warnings((warning,)), (warning,))

    def test_many_kinds_are_capped(self):
        warnings = tuple(f"kind{index} row: 1 field(s), expected 5 | line" for index in range(9))
        summary = view.summarise_warnings(warnings, limit=5)
        self.assertEqual(len(summary), 6)
        self.assertIn("more kind(s) of warning", summary[-1])


class PanelIsReadOnlyTests(unittest.TestCase):
    def test_the_view_never_imports_a_mutating_api(self):
        """The panel must stay observation-only, enforced on the module itself."""
        source = Path(view.__file__).read_text(encoding="utf-8")
        for forbidden in ("manage_service", "read_service_logs", "run_lifecycle"):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is optional (requirements-tui.txt)")
class MetricsPanelTests(unittest.IsolatedAsyncioTestCase):
    def prepared_project_root(self, plan) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        render_plan(plan, root / ".generated" / "deployment-v3" / plan.deployment_id / "bundle")
        return root

    async def wait_for_samples(self, app, pilot, attempts: int = 200):
        for _ in range(attempts):
            if app.stamps:
                return
            await pilot.pause(0.05)
        self.fail("the panel never applied a sample")

    async def test_panel_renders_machine_samples_and_offers_no_action(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        app = view._make_textual_app(self.prepared_project_root(plan))
        with patch.object(view, "sample_machine", lambda plan, machine, **kwargs: sample_metrics(machine_id=machine.id)):
            async with app.run_test() as pilot:
                await self.wait_for_samples(app, pilot)
                self.assertEqual(app.deployment_id, plan.deployment_id)
                machines = app.query_one("#machine-table")
                self.assertEqual(machines.row_count, len(plan.machines))
                row = machines.get_row_at(0)
                self.assertEqual(row[2], "active")
                self.assertEqual(row[3], "0s")
                self.assertEqual(app.query_one("#container-table").row_count, 2)
                # The whole point of a separate panel: nothing here can act.
                self.assertEqual(len(app.query("Button")), 0)

    async def test_panel_survives_a_machine_that_cannot_be_sampled(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        app = view._make_textual_app(self.prepared_project_root(plan))
        unreachable = sample_metrics(reachable=False, error="docker CLI not found on this machine", containers=())

        def fake_sample(plan, machine, **kwargs):
            return unreachable

        with patch.object(view, "sample_machine", fake_sample):
            async with app.run_test() as pilot:
                await self.wait_for_samples(app, pilot)
                machines = app.query_one("#machine-table")
                self.assertEqual(machines.get_row_at(0)[2], "unreachable")
                self.assertEqual(machines.get_row_at(0)[4], view.EMPTY)
                self.assertEqual(app.query_one("#container-table").row_count, 0)

    async def test_pressing_s_cycles_the_container_order(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        app = view._make_textual_app(self.prepared_project_root(plan))
        with patch.object(view, "sample_machine", lambda plan, machine, **kwargs: busy_metrics()):
            async with app.run_test() as pilot:
                await self.wait_for_samples(app, pilot)
                table = app.query_one("#container-table")
                first = lambda: table.get_row_at(0)[0]
                self.assertEqual(first(), "fat")
                await pilot.press("s")
                self.assertEqual(app.container_order, "cpu")
                self.assertEqual(first(), "hammer")
                await pilot.press("s")
                self.assertEqual(app.container_order, "memory")
                self.assertEqual(first(), "fat")
                await pilot.press("s")
                self.assertEqual(app.container_order, "name")
                self.assertEqual(first(), "fat")

    async def test_resampling_one_machine_does_not_touch_the_others(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        app = view._make_textual_app(self.prepared_project_root(plan))
        with patch.object(view, "sample_machine", lambda plan, machine, **kwargs: sample_metrics(machine_id=machine.id)):
            async with app.run_test() as pilot:
                await self.wait_for_samples(app, pilot)
                before = dict(app.stamps)
                app.action_resample()
                for _ in range(200):
                    if app.sampling:
                        break
                    await pilot.pause(0.05)
                await self.wait_for_samples(app, pilot)
                self.assertEqual(before.keys(), app.stamps.keys())


if __name__ == "__main__":
    unittest.main()
