import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


stop_script = load_script("stop")
restart_script = load_script("restart")
clean_script = load_script("clean")


class OperationalScriptTests(unittest.TestCase):
    def make_stack(self, root: Path, relative_path: str) -> Path:
        stack = root / relative_path
        stack.mkdir(parents=True)
        (stack / "docker-compose.yml").write_text("services: {}\n")
        return stack

    def test_stop_propagates_compose_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_stack(root, stop_script.COMPONENTS_TO_STOP[0])
            result = mock.Mock(returncode=1)
            with (
                mock.patch.object(stop_script, "PROJECT_ROOT", root),
                mock.patch.object(stop_script.subprocess, "run", return_value=result),
            ):
                self.assertEqual(stop_script.main(), 1)

    def test_restart_propagates_compose_failure_without_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_stack(root, restart_script.STACKS[0][0])
            result = mock.Mock(returncode=1)
            with (
                mock.patch.object(restart_script, "PROJECT_ROOT", root),
                mock.patch.object(restart_script.subprocess, "run", return_value=result),
            ):
                self.assertEqual(restart_script.main(), 1)

    def test_restart_starts_and_checks_both_ipfs_ha_peers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = "components/storage/dark-ipfs"
            stack = self.make_stack(root, relative)
            (stack / ".env.node.site-a-storage-1").write_text(
                "HOST_BIND_ADDRESS=127.0.0.1\n"
                "IPFS_API_HOST_PORT=5001\n"
                "CLUSTER_REST_HOST_PORT=9094\n"
            )
            (stack / ".env.node.site-a-storage-2").write_text(
                "HOST_BIND_ADDRESS=127.0.0.1\n"
                "IPFS_API_HOST_PORT=5101\n"
                "CLUSTER_REST_HOST_PORT=9194\n"
            )
            result = mock.Mock(returncode=0)
            with (
                mock.patch.object(restart_script, "PROJECT_ROOT", root),
                mock.patch.object(
                    restart_script.subprocess, "run", return_value=result
                ) as run,
                mock.patch.object(
                    restart_script, "wait_for_health", return_value=True
                ) as wait,
            ):
                self.assertEqual(restart_script.main(), 0)

            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(
                ["make", "up", "ENV_FILE=.env.node.site-a-storage-1"],
                commands,
            )
            self.assertIn(
                ["make", "up", "ENV_FILE=.env.node.site-a-storage-2"],
                commands,
            )
            self.assertEqual(wait.call_count, 4)

    def test_stop_stops_both_ipfs_ha_peers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = "components/storage/dark-ipfs"
            stack = self.make_stack(root, relative)
            (stack / ".env.node.site-a-storage-1").write_text("")
            (stack / ".env.node.site-a-storage-2").write_text("")
            result = mock.Mock(returncode=0)
            with (
                mock.patch.object(stop_script, "PROJECT_ROOT", root),
                mock.patch.object(
                    stop_script.subprocess, "run", return_value=result
                ) as run,
            ):
                self.assertEqual(stop_script.main(), 0)

            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(
                ["make", "down", "ENV_FILE=.env.node.site-a-storage-1"],
                commands,
            )
            self.assertIn(
                ["make", "down", "ENV_FILE=.env.node.site-a-storage-2"],
                commands,
            )

    def test_clean_removes_only_project_generated_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "components").mkdir()
            (root / "venv").mkdir()
            outside = root / "keep"
            outside.mkdir()
            with mock.patch.object(clean_script, "PROJECT_ROOT", root):
                self.assertEqual(clean_script.remove_generated_directories(False), [])
            self.assertFalse((root / "components").exists())
            self.assertFalse((root / "venv").exists())
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
