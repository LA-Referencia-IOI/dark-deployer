import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deployment_v3.metrics.collector import ContainerSample, MachineMetrics
from deployment_v3.metrics.prometheus import collect_to_textfile, render_prometheus


PLAN = SimpleNamespace(
    deployment_id="dark-example",
    machines=(SimpleNamespace(id="apps", site="site-a"),),
)


class PrometheusMetricsTests(unittest.TestCase):
    def test_renderer_preserves_missing_values_and_stable_service_labels(self):
        sample = MachineMetrics(
            machine_id="apps",
            execution="local",
            reachable=True,
            cores=8,
            memory_total_bytes=1024,
            warnings=("partial stats",),
            containers=(ContainerSample(
                name='dark-"api',
                container_id="abc",
                service_id="admin-api",
                state="running",
                status="Up 1 minute",
                cpu_percent=None,
                memory_bytes=128,
                restart_count=2,
            ),),
        )
        text = render_prometheus(PLAN, (sample,), collected_at=123.0)
        self.assertIn('dark_metrics_export_timestamp_seconds{deployment="dark-example"} 123.000000', text)
        self.assertIn('service="admin-api"', text)
        self.assertIn('container="dark-\\"api"', text)
        self.assertIn("dark_machine_probe_warnings", text)
        self.assertNotIn("dark_container_cpu_percent{", text)

    def test_unreachable_machine_is_failure_not_zero_capacity(self):
        sample = MachineMetrics(machine_id="apps", execution="ssh", reachable=False, error="timeout")
        text = render_prometheus(PLAN, (sample,), collected_at=123.0)
        self.assertIn("dark_machine_probe_success", text)
        self.assertNotIn("dark_docker_daemon_cores{", text)

    def test_textfile_replacement_is_atomic_and_complete(self):
        sample = MachineMetrics(machine_id="apps", execution="local", reachable=True)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dark.prom"
            with patch("deployment_v3.metrics.prometheus.sample_machines", return_value=(sample,)):
                result = collect_to_textfile(PLAN, output)
            self.assertEqual(result, (sample,))
            self.assertTrue(output.read_text().endswith("\n"))
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(output.parent.glob(".dark.prom-*")), [])


if __name__ == "__main__":
    unittest.main()
