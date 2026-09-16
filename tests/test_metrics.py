"""Coverage for the read-only Docker metrics collector.

The fixtures reproduce the tab separated output of the probe templates declared
in ``deployment_v3.metrics.collector``.  They were written from those templates
and not captured from a live daemon, so the first run against a real host should
confirm the field names before the numbers are trusted.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deployment_v3.executor import CommandResult, LocalExecutor, SshExecutor
from deployment_v3.metrics import collector as collector_module
from deployment_v3.metrics.collector import (
    MARKER,
    MachineMetrics,
    container_rates,
    daemon_key,
    parse_probe_output,
    probe_script,
    sample_machine,
)


MACHINE = SimpleNamespace(id="local", execution="local")
SSH_MACHINE = SimpleNamespace(
    id="blockchain-a",
    execution="ssh",
    management_address="10.0.0.11",
    ssh=SimpleNamespace(port=22),
)

DAEMON_ROW = "16\t16750372454\t7\t27.3.1\toverlay2"
CONTAINER_ROWS = (
    "3f2a1b0c9d8e\tvalidator01\trunning\tUp 2 hours\tvalidator01",
    "9c8b7a6d5e4f\tminter-api\trunning\tUp 2 hours\tminter-api",
    "1a2b3c4d5e6f\tminter-migrate\texited\tExited (0) 3 hours ago\tminter-migrate",
)
STATS_ROWS = (
    "3f2a1b0c9d8e\tvalidator01\t42.50%\t1.5GiB / 15.6GiB\t7.91%\t1.2kB / 3.4MB\t12MB / 34MB\t64",
    "9c8b7a6d5e4f\tminter-api\t3.10%\t512MiB / 15.6GiB\t1.35%\t10MB / 2MB\t1MB / 0B\t12",
    "deadbeefdead\tunrelated-container\t0.10%\t10MiB / 15.6GiB\t0.06%\t0B / 0B\t0B / 0B\t1",
)
INSPECT_ROWS = (
    "3f2a1b0c9d8eaabbccddeeff00112233445566778899aabbccddeeff00112233\t/validator01\t2\trunning\t2026-09-16T08:11:00.123456789Z",
    "9c8b7a6d5e4faabbccddeeff00112233445566778899aabbccddeeff00112233\t/minter-api\t0\trunning\t2026-09-16T08:11:02.123456789Z",
    "1a2b3c4d5e6faabbccddeeff00112233445566778899aabbccddeeff00112233\t/minter-migrate\t0\texited\t2026-09-16T05:00:00.123456789Z",
)
DISK_ROWS = (
    "Images\t6.1GB\t4.2GB (68%)\t14",
    "Containers\t120MB\t0B (0%)\t3",
)


def emit(sections: dict[str, tuple[str, ...]]) -> str:
    """Render probe output the way the shell program prints it."""
    lines: list[str] = []
    for kind, rows in sections.items():
        lines.append(f"{MARKER} {kind}")
        lines.extend(rows)
    return "\n".join(lines) + "\n"


FULL_OUTPUT = emit({
    "daemon": (DAEMON_ROW,),
    "containers": CONTAINER_ROWS,
    "stats": STATS_ROWS,
    "inspect": INSPECT_ROWS,
})


def sample(**sections: tuple[str, ...]) -> MachineMetrics:
    return parse_probe_output(
        MACHINE,
        returncode=0,
        stdout=emit(sections),
        stderr="",
    )


class ProbeScriptTests(unittest.TestCase):
    def test_probe_only_issues_read_only_docker_subcommands(self):
        import re

        for include_disk in (False, True):
            script = probe_script("dark-operator-local-ha", include_disk=include_disk)
            verbs = set(re.findall(r"\bdocker\s+([a-z-]+)", script))
            self.assertLessEqual(verbs, collector_module.READ_ONLY_DOCKER_SUBCOMMANDS, verbs)
            self.assertTrue(verbs, "the probe must actually call docker")

    def test_the_read_only_allow_list_is_not_widened(self):
        self.assertEqual(
            collector_module.READ_ONLY_DOCKER_SUBCOMMANDS,
            frozenset({"info", "version", "ps", "stats", "inspect", "system"}),
        )

    def test_probe_contains_no_mutating_command(self):
        for include_disk in (False, True):
            script = probe_script("dark-operator-local-ha", include_disk=include_disk)
            for token in (" prune", " rm", " rmi", " stop", " start", " restart", " kill",
                          " run", " create", " build", " push", " exec", " compose", " update", " pause"):
                self.assertNotIn(token, script, token)

    def test_probe_targets_one_deployment_by_label(self):
        script = probe_script("dark-operator-local-ha")
        self.assertIn("--filter", script)
        self.assertIn("label=org.dark.deployment.id=dark-operator-local-ha", script)
        self.assertIn("docker stats --no-stream", script)
        self.assertIn("docker ps -aq", script)

    def test_disk_section_is_off_by_default(self):
        self.assertNotIn("system df", probe_script("dark-operator-local-ha"))
        self.assertIn("system df", probe_script("dark-operator-local-ha", include_disk=True))

    def test_hostile_deployment_id_stays_quoted_and_parses_as_shell(self):
        hostile = "evil; touch /tmp/dark-pwned 'quoted'"
        script = probe_script(hostile)
        self.assertIn(shlex.quote(f"label=org.dark.deployment.id={hostile}"), script)
        syntax = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


class ParseProbeOutputTests(unittest.TestCase):
    def setUp(self):
        self.metrics = parse_probe_output(MACHINE, returncode=0, stdout=FULL_OUTPUT, stderr="")

    def test_daemon_denominators(self):
        self.assertTrue(self.metrics.reachable)
        self.assertEqual(self.metrics.cores, 16)
        self.assertEqual(self.metrics.memory_total_bytes, 16750372454)
        self.assertEqual(self.metrics.containers_running, 7)
        self.assertEqual(self.metrics.server_version, "27.3.1")
        self.assertEqual(self.metrics.driver, "overlay2")

    def test_running_container_metrics(self):
        container = self.metrics.by_name["validator01"]
        self.assertEqual(container.service_id, "validator01")
        self.assertEqual(container.container_id, "3f2a1b0c9d8e")
        self.assertEqual(container.state, "running")
        self.assertTrue(container.running)
        self.assertTrue(container.has_metrics)
        self.assertAlmostEqual(container.cpu_percent, 42.5)
        self.assertEqual(container.memory_bytes, int(1.5 * 1024 ** 3))
        self.assertEqual(container.memory_limit_bytes, int(15.6 * 1024 ** 3))
        self.assertAlmostEqual(container.memory_percent, 7.91)
        self.assertEqual(container.network_bytes, 1_200 + 3_400_000)
        self.assertEqual(container.block_bytes, 12_000_000 + 34_000_000)
        self.assertEqual(container.pids, 64)
        self.assertEqual(container.restart_count, 2)
        self.assertEqual(container.started_at, "2026-09-16T08:11:00.123456789Z")

    def test_stopped_container_keeps_its_row_without_metrics(self):
        container = self.metrics.by_name["minter-migrate"]
        self.assertEqual(container.state, "exited")
        self.assertFalse(container.running)
        self.assertFalse(container.has_metrics)
        self.assertIsNone(container.cpu_percent)
        self.assertEqual(container.restart_count, 0)
        self.assertIn(container, self.metrics.unmeasured_containers)
        self.assertNotIn(container, self.metrics.running_containers)

    def test_installed_limit_and_daemon_total_agree(self):
        container = self.metrics.by_name["minter-api"]
        self.assertEqual(container.memory_limit_bytes, self.metrics.memory_total_bytes)

    def test_aggregates_use_the_daemon_as_denominator(self):
        self.assertAlmostEqual(self.metrics.cpu_percent_of_host, round((42.5 + 3.1) / 16, 2))
        self.assertEqual(self.metrics.memory_bytes, int(1.5 * 1024 ** 3) + 512 * 1024 ** 2)
        self.assertAlmostEqual(self.metrics.memory_percent_of_host, 12.82)

    def test_daemon_counter_is_wider_than_the_deployment(self):
        self.assertEqual(len(self.metrics.containers), 3)
        self.assertEqual(len(self.metrics.running_containers), 2)
        self.assertEqual(self.metrics.containers_running, 7)

    def test_stats_rows_outside_the_deployment_are_ignored(self):
        self.assertNotIn("unrelated-container", self.metrics.by_name)
        self.assertTrue(any("outside the deployment" in warning for warning in self.metrics.warnings))

    def test_inspect_names_join_despite_the_leading_slash(self):
        self.assertEqual(self.metrics.by_name["minter-api"].restart_count, 0)
        self.assertEqual(self.metrics.by_name["minter-api"].started_at, "2026-09-16T08:11:02.123456789Z")

    def test_malformed_row_warns_without_losing_the_rest(self):
        metrics = sample(
            daemon=(DAEMON_ROW,),
            containers=(CONTAINER_ROWS[0], "truncated\trow"),
        )
        self.assertEqual(len(metrics.containers), 1)
        self.assertTrue(any("container row" in warning for warning in metrics.warnings))

    def test_absent_fields_are_absent_and_not_the_literal_no_value(self):
        metrics = sample(
            daemon=("8\t16750372454\t0\t<no value>\t<no value>",),
            containers=("5a5a5a5a5a5a\torphan-container\trunning\tUp 1 minute\t<no value>",),
        )
        self.assertIsNone(metrics.server_version)
        self.assertIsNone(metrics.driver)
        self.assertIsNone(metrics.by_name["orphan-container"].service_id)
        self.assertEqual(metrics.cores, 8)

    def test_unparsable_size_leaves_the_column_empty(self):
        metrics = sample(
            daemon=(DAEMON_ROW,),
            containers=(CONTAINER_ROWS[0],),
            stats=("3f2a1b0c9d8e\tvalidator01\tnot-a-percent\t?\t?\t?\t?\t?",),
        )
        container = metrics.by_name["validator01"]
        self.assertIsNone(container.cpu_percent)
        self.assertIsNone(container.memory_bytes)
        self.assertIsNone(container.network_bytes)
        self.assertIsNone(container.pids)
        self.assertTrue(container.running)


class ContractTests(unittest.TestCase):
    """The probe contract is one deliberate edit, not an accident.

    Docker has changed its output before; these tests fail loudly if a template
    field or its order changes, instead of letting a shifted column reach the
    panel.
    """

    def test_templates_use_exactly_the_documented_fields(self):
        self.assertEqual(collector_module.DAEMON_FIELDS, ("NCPU", "MemTotal", "ContainersRunning", "ServerVersion", "Driver"))
        self.assertEqual(collector_module.VERSION_FIELDS, ("Server.APIVersion",))
        self.assertEqual(collector_module.CONTAINER_FIELDS, ("ID", "Names", "State", "Status", f'Label "{collector_module.SERVICE_LABEL}"'))
        self.assertEqual(collector_module.STATS_FIELDS, ("ID", "Name", "CPUPerc", "MemUsage", "MemPerc", "NetIO", "BlockIO", "PIDs"))
        self.assertEqual(collector_module.INSPECT_FIELDS, ("Id", "Name", "RestartCount", "State.Status", "State.StartedAt"))
        self.assertEqual(collector_module.DISK_FIELDS, ("Type", "Size", "Reclaimable", "TotalCount"))

    def test_formats_are_built_from_the_field_tuples(self):
        self.assertEqual(
            collector_module.DAEMON_FORMAT,
            "{{.NCPU}}\t{{.MemTotal}}\t{{.ContainersRunning}}\t{{.ServerVersion}}\t{{.Driver}}",
        )
        for fields, template in (
            (collector_module.STATS_FIELDS, collector_module.STATS_FORMAT),
            (collector_module.INSPECT_FIELDS, collector_module.INSPECT_FORMAT),
            (collector_module.CONTAINER_FIELDS, collector_module.CONTAINER_FORMAT),
        ):
            self.assertEqual(template.count("\t"), len(fields) - 1)
            self.assertEqual(template.count("{{."), len(fields))

    def test_no_template_relies_on_docker_expanding_an_escape(self):
        """docker info and docker inspect do not expand ``\\t``; ps and stats do.

        The first real run of the panel lost the whole daemon row and every
        inspect row to this, so the separator must be a real tab.
        """
        for template in (
            collector_module.DAEMON_FORMAT, collector_module.VERSION_FORMAT,
            collector_module.CONTAINER_FORMAT, collector_module.STATS_FORMAT,
            collector_module.INSPECT_FORMAT, collector_module.DISK_FORMAT,
        ):
            self.assertNotIn(r"\t", template)
        for template in (
            collector_module.DAEMON_FORMAT, collector_module.CONTAINER_FORMAT,
            collector_module.STATS_FORMAT, collector_module.INSPECT_FORMAT, collector_module.DISK_FORMAT,
        ):
            self.assertIn("\t", template)

    def test_an_unexpanded_separator_is_named_in_the_warning(self):
        """The shape the real run produced must explain itself."""
        row = r"{{.NCPU}}\t{{.MemTotal}}\t{{.ContainersRunning}}\t{{.ServerVersion}}\t{{.Driver}}"
        metrics = sample(daemon=(row,), containers=(CONTAINER_ROWS[0],))
        self.assertEqual(metrics.warnings, (collector_module.row_warning("daemon", row, 5),))
        self.assertIn("tab separator unexpanded", metrics.warnings[0])
        self.assertIn("1 field(s), expected 5", metrics.warnings[0])
        self.assertIsNone(metrics.cores)
        self.assertIsNone(metrics.memory_total_bytes)

    def test_a_daemon_without_denominators_says_so(self):
        metrics = sample(daemon=("\t16750372454\t7\t29.8.0\toverlayfs",), containers=(CONTAINER_ROWS[0],))
        self.assertIsNone(metrics.cores)
        self.assertTrue(any("no NCPU" in warning for warning in metrics.warnings))
        self.assertFalse(any("no MemTotal" in warning for warning in metrics.warnings))

    def test_a_row_with_an_unexpected_field_count_is_reported_not_shifted(self):
        metrics = sample(
            daemon=(DAEMON_ROW,),
            containers=(CONTAINER_ROWS[0] + "\textra-field",),
            stats=("3f2a1b0c9d8e\tvalidator01\t42.50%\t1.5GiB / 15.6GiB\t7.91%\t1.2kB / 3.4MB\t12MB / 34MB\t64\textra-field",),
        )
        self.assertEqual(metrics.containers, ())
        self.assertTrue(any("container row" in warning for warning in metrics.warnings))
        self.assertTrue(any("stats row" in warning for warning in metrics.warnings))

    def test_api_version_travels_with_every_sample(self):
        metrics = sample(daemon=(DAEMON_ROW,), version=("1.51",), containers=CONTAINER_ROWS)
        self.assertEqual(metrics.server_version, "27.3.1")
        self.assertEqual(metrics.api_version, "1.51")

    def test_version_section_is_never_critical(self):
        stdout = (
            f"{MARKER} daemon\n{DAEMON_ROW}\n"
            f"{MARKER} containers\n" + "\n".join(CONTAINER_ROWS) + "\n"
            f"{MARKER} version\n{collector_module.ERROR_TAG} unknown flag: --format\n"
        )
        metrics = parse_probe_output(MACHINE, returncode=0, stdout=stdout, stderr="")
        self.assertTrue(metrics.reachable)
        self.assertIsNone(metrics.api_version)
        self.assertTrue(any("version section failed" in warning for warning in metrics.warnings))


class FailureSemanticsTests(unittest.TestCase):
    def test_remote_command_failure_marks_the_machine_unreachable(self):
        metrics = parse_probe_output(
            SSH_MACHINE,
            returncode=255,
            stdout="",
            stderr="ssh: connect to host 10.0.0.11 port 22: Connection refused\n",
        )
        self.assertFalse(metrics.reachable)
        self.assertEqual(metrics.machine_id, "blockchain-a")
        self.assertEqual(metrics.execution, "ssh")
        self.assertIn("Connection refused", metrics.error or "")
        self.assertEqual(metrics.containers, ())

    def test_missing_docker_is_reported_as_an_explanation(self):
        metrics = parse_probe_output(
            MACHINE,
            returncode=0,
            stdout=emit({"error": ("docker CLI not found on this machine",)}),
            stderr="",
        )
        self.assertFalse(metrics.reachable)
        self.assertIn("docker CLI not found", metrics.error or "")

    def test_optional_section_failure_keeps_the_sample(self):
        stdout = (
            f"{MARKER} daemon\n{DAEMON_ROW}\n"
            f"{MARKER} containers\n" + "\n".join(CONTAINER_ROWS) + "\n"
            f"{MARKER} stats\n{collector_module.ERROR_TAG} stats boom\n"
        )
        metrics = parse_probe_output(MACHINE, returncode=0, stdout=stdout, stderr="")
        self.assertTrue(metrics.reachable)
        self.assertEqual(len(metrics.containers), 3)
        self.assertTrue(any("stats section failed" in warning for warning in metrics.warnings))

    def test_critical_section_failure_discards_the_sample(self):
        metrics = parse_probe_output(
            MACHINE,
            returncode=0,
            stdout=f"{MARKER} containers\n{collector_module.ERROR_TAG} Cannot connect to the Docker daemon\n",
            stderr="",
        )
        self.assertFalse(metrics.reachable)
        self.assertIn("Cannot connect to the Docker daemon", metrics.error or "")

    def test_no_containers_found_is_not_a_warning(self):
        stdout = (
            f"{MARKER} daemon\n{DAEMON_ROW}\n"
            f"{MARKER} containers\n"
            f"{MARKER} stats\n{collector_module.ERROR_TAG} Error response from daemon: No containers found\n"
        )
        metrics = parse_probe_output(MACHINE, returncode=0, stdout=stdout, stderr="")
        self.assertTrue(metrics.reachable)
        self.assertEqual(metrics.containers, ())
        self.assertEqual(metrics.warnings, ())

    def test_empty_output_is_unreachable_rather_than_empty_success(self):
        metrics = parse_probe_output(MACHINE, returncode=0, stdout="", stderr="")
        self.assertFalse(metrics.reachable)
        self.assertIn("no recognizable output", metrics.error or "")


class SampleMachineTests(unittest.TestCase):
    def test_executor_timeout_becomes_an_unreachable_sample(self):
        class TimingOutExecutor:
            def run(self, argv, *, timeout=30.0):
                raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout)

        plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
        with patch.object(collector_module, "resolve_executor", return_value=TimingOutExecutor()):
            metrics = sample_machine(plan, SSH_MACHINE, timeout=5.0)
        self.assertFalse(metrics.reachable)
        self.assertIn("timed out after 5s", metrics.error or "")

    def test_sample_uses_the_plan_deployment_id(self):
        seen: list[tuple[str, ...]] = []

        class RecordingExecutor:
            def run(self, argv, *, timeout=30.0):
                seen.append(tuple(argv))
                return CommandResult(tuple(argv), 0, emit({"daemon": (DAEMON_ROW,)}), "")

        plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
        with patch.object(collector_module, "resolve_executor", return_value=RecordingExecutor()):
            metrics = sample_machine(plan, MACHINE)
        self.assertTrue(metrics.reachable)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][:2], ("sh", "-c"))
        self.assertIn("label=org.dark.deployment.id=dark-operator-local-ha", seen[0][2])

    def test_resolution_failure_is_reported_not_raised(self):
        from deployment_v3.executor import ExecutionError

        plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
        with patch.object(collector_module, "resolve_executor", side_effect=ExecutionError("cannot resolve auto machine")):
            metrics = sample_machine(plan, MACHINE)
        self.assertFalse(metrics.reachable)
        self.assertIn("cannot resolve auto machine", metrics.error or "")


FAKE_DOCKER = """#!/bin/sh
case "$1" in
  info) printf '16\\t16750372454\\t7\\t27.3.1\\toverlay2\\n' ;;
  ps)
    if [ "$2" = "-aq" ]; then
      printf '3f2a1b0c9d8e\\n9c8b7a6d5e4f\\n'
    else
      printf '3f2a1b0c9d8e\\tvalidator01\\trunning\\tUp 2 hours\\tvalidator01\\n'
    fi
    ;;
  stats) printf '3f2a1b0c9d8e\\tvalidator01\\t42.50%%\\t1.5GiB / 15.6GiB\\t7.91%%\\t1.2kB / 3.4MB\\t12MB / 34MB\\t64\\n' ;;
  version) printf '1.51\\n' ;;
  inspect) printf 'full-id\\t/validator01\\t2\\trunning\\t2026-09-16T08:11:00Z\\n' ;;
  *) printf 'unexpected docker invocation: %s\\n' "$*" >&2; exit 1 ;;
esac
"""


class ProbeProgramTests(unittest.TestCase):
    """Run the real shell program; the parser tests would not catch a broken one."""

    def test_probe_program_parses_the_sections_it_prints(self):
        with tempfile.TemporaryDirectory() as bindir:
            executable = Path(bindir) / "docker"
            executable.write_text(FAKE_DOCKER, encoding="utf-8")
            executable.chmod(0o755)
            plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
            with patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ.get('PATH', '')}"}):
                metrics = sample_machine(plan, MACHINE)
        self.assertTrue(metrics.reachable, metrics.error)
        self.assertEqual(metrics.cores, 16)
        container = metrics.by_name["validator01"]
        self.assertEqual(container.service_id, "validator01")
        self.assertAlmostEqual(container.cpu_percent, 42.5)
        self.assertEqual(container.block_bytes, 46_000_000)
        self.assertEqual(container.restart_count, 2)

    def test_probe_program_explains_a_missing_docker_cli(self):
        with tempfile.TemporaryDirectory() as bindir:
            # A PATH that can still find the shell but nothing else.
            os.symlink(shutil.which("sh") or "/bin/sh", Path(bindir) / "sh")
            plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
            with patch.dict(os.environ, {"PATH": bindir}):
                metrics = sample_machine(plan, MACHINE)
        self.assertFalse(metrics.reachable)
        self.assertIn("docker CLI not found", metrics.error or "")

    def test_probe_program_does_not_run_docker_for_a_foreign_deployment(self):
        """The label filter is what keeps one deployment out of another's panel."""
        with tempfile.TemporaryDirectory() as bindir:
            executable = Path(bindir) / "docker"
            executable.write_text(FAKE_DOCKER, encoding="utf-8")
            executable.chmod(0o755)
            plan = SimpleNamespace(deployment_id="dark-operator-local-ha")
            with patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ.get('PATH', '')}"}):
                script = probe_script(plan.deployment_id)
                result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("label=org.dark.deployment.id=dark-operator-local-ha", script)
        self.assertEqual(result.stderr, "")


class ContainerRateTests(unittest.TestCase):
    def _metrics(self, *, network: int, block: int, started_at: str = "t0") -> MachineMetrics:
        return sample(
            daemon=(DAEMON_ROW,),
            containers=(CONTAINER_ROWS[0],),
            stats=(f"3f2a1b0c9d8e\tvalidator01\t1.00%\t1GiB / 15.6GiB\t1.00%\t{network}\t{block}\t8",),
            inspect=(f"full-id\t/validator01\t0\trunning\t{started_at}",),
        )

    def test_rates_are_derived_from_cumulative_counters(self):
        previous = self._metrics(network=1_000_000, block=2_000_000)
        current = self._metrics(network=1_600_000, block=2_500_000)
        rates = container_rates(previous, current, elapsed_seconds=60.0)
        self.assertEqual(len(rates), 1)
        self.assertAlmostEqual(rates[0].network_bytes_per_second, 10_000.0)
        self.assertAlmostEqual(rates[0].block_bytes_per_second, 8333.33)

    def test_restart_resets_instead_of_spiking(self):
        previous = self._metrics(network=5_000_000, block=5_000_000, started_at="t0")
        current = self._metrics(network=10_000, block=20_000, started_at="t1")
        rates = container_rates(previous, current, elapsed_seconds=60.0)
        self.assertIsNone(rates[0].network_bytes_per_second)
        self.assertIsNone(rates[0].block_bytes_per_second)

    def test_counter_going_backwards_is_refused(self):
        previous = self._metrics(network=9_000_000, block=9_000_000)
        current = self._metrics(network=10_000, block=1_000)
        rates = container_rates(previous, current, elapsed_seconds=60.0)
        self.assertIsNone(rates[0].network_bytes_per_second)
        self.assertIsNone(rates[0].block_bytes_per_second)

    def test_new_container_and_missing_previous_have_no_rate(self):
        current = self._metrics(network=1_000, block=1_000)
        self.assertIsNone(container_rates(None, current, elapsed_seconds=60.0)[0].network_bytes_per_second)
        other = sample(daemon=(DAEMON_ROW,), containers=(CONTAINER_ROWS[1],))
        self.assertIsNone(container_rates(other, current, elapsed_seconds=60.0)[0].network_bytes_per_second)

    def test_no_elapsed_time_means_no_rate(self):
        previous = self._metrics(network=1_000, block=1_000)
        current = self._metrics(network=2_000, block=2_000)
        self.assertIsNone(container_rates(previous, current, elapsed_seconds=0)[0].network_bytes_per_second)


class DaemonKeyTests(unittest.TestCase):
    def test_local_and_lab_machines_share_the_controller_daemon(self):
        self.assertEqual(daemon_key(SimpleNamespace(id="a", execution="local")), "local")
        self.assertEqual(daemon_key(SimpleNamespace(id="b", execution="docker-lab")), "local")

    def test_remote_machines_are_keyed_by_address_and_port(self):
        self.assertEqual(daemon_key(SSH_MACHINE), "ssh:10.0.0.11:22")


class ExecutorRoutingTests(unittest.TestCase):
    def test_collector_reuses_the_deployment_executors(self):
        """The collector must not grow its own SSH transport."""
        local = SimpleNamespace(id="local", execution="local")
        remote = SimpleNamespace(
            id="remote",
            execution="ssh",
            management_address="10.0.0.11",
            ssh=SimpleNamespace(user="dark", port=22, private_key_file="/key", known_hosts_file=None),
        )
        self.assertIsInstance(collector_module.resolve_executor(local), LocalExecutor)
        self.assertIsInstance(collector_module.resolve_executor(remote), SshExecutor)


class SizeParsingTests(unittest.TestCase):
    def test_decimal_and_binary_units(self):
        parse = collector_module._parse_size
        self.assertEqual(parse("0B"), 0)
        self.assertEqual(parse("1.2kB"), 1200)
        self.assertEqual(parse("3.4MB"), 3_400_000)
        self.assertEqual(parse("1.5GiB"), int(1.5 * 1024 ** 3))
        self.assertEqual(parse("512MiB"), 512 * 1024 ** 2)
        self.assertEqual(parse("16750372454"), 16750372454)
        self.assertEqual(parse("  2 TiB  "), 2 * 1024 ** 4)

    def test_unknown_units_and_junk_are_refused(self):
        parse = collector_module._parse_size
        self.assertIsNone(parse(""))
        self.assertIsNone(parse("abc"))
        self.assertIsNone(parse("12ZB"))


if __name__ == "__main__":
    unittest.main()
