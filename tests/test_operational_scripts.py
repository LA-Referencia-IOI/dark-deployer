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
    def make_compose(self, root: Path, name: str) -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n")
        return path

    def test_stop_propagates_compose_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            compose = self.make_compose(Path(temporary), "compose/apps.yml")
            with mock.patch.object(stop_script.subprocess, "run", return_value=mock.Mock(returncode=1)):
                self.assertFalse(stop_script.stop("dark-apps", compose))

    def test_restart_propagates_compose_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_compose(root, "compose/apps.yml")
            with (
                mock.patch.object(restart_script, "ROOT", root),
                mock.patch.object(restart_script.subprocess, "run", return_value=mock.Mock(returncode=1)),
            ):
                self.assertEqual(restart_script.main(), 1)

    def test_restart_uses_central_storage_compose_for_each_peer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_compose(root, "compose/apps.yml")
            storage = root / "components/dark-ipfs"
            storage.mkdir(parents=True)
            (storage / ".env.node.site-a-storage-1").write_text("NODE_ID=site-a-storage-1\n")
            (storage / ".env.node.site-a-storage-2").write_text("NODE_ID=site-a-storage-2\n")
            with (
                mock.patch.object(restart_script, "ROOT", root),
                mock.patch.object(restart_script.subprocess, "run", return_value=mock.Mock(returncode=0)) as run,
            ):
                self.assertEqual(restart_script.main(), 0)
            commands = [" ".join(call.args[0]) for call in run.call_args_list]
            self.assertTrue(any("compose/storage.yml" in command for command in commands))
            self.assertTrue(any("dark-storage-site-a-storage-1" in command for command in commands))
            self.assertTrue(any("dark-storage-site-a-storage-2" in command for command in commands))

    def test_clean_down_uses_central_compose(self):
        with tempfile.TemporaryDirectory() as temporary:
            compose = self.make_compose(Path(temporary), "compose/apps.yml")
            with mock.patch.object(clean_script.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                self.assertTrue(clean_script.down("dark-apps", compose))
            command = " ".join(run.call_args.args[0])
            self.assertIn("compose/apps.yml", command)
            self.assertIn("--volumes", command)


if __name__ == "__main__":
    unittest.main()
