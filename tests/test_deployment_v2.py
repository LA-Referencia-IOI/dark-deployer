import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from deployment_v2.inventory import InventoryError, load_inventory
from deployment_v2.executor import CommandResult, run_preflight
from deployment_v2.model import Machine, SshSettings
from deployment_v2.planner import build_plan
from deployment_v2.render import render_plan


ROOT = Path(__file__).resolve().parents[1]


class DeploymentV2Tests(unittest.TestCase):
    def test_examples_validate_and_plan(self):
        for name, machines, groups in (("local-simple", 1, 4), ("local-ha", 1, 5), ("production-five-host", 5, 5)):
            path = ROOT / "examples" / "deployment-v2" / f"{name}.json"
            _, loaded_machines, loaded_groups, _ = load_inventory(path)
            self.assertEqual(len(loaded_machines), machines)
            self.assertEqual(len(loaded_groups), groups)
            plan = build_plan(path)
            self.assertEqual(plan.deployment_id, json.loads(path.read_text())["deployment"]["id"])
            self.assertEqual(len(plan.docker_subnets), machines)
            self.assertTrue(any(step.id == "verify:deployment" for step in plan.steps))

    def test_rejects_duplicate_private_address_and_bad_replication(self):
        source = ROOT / "examples" / "deployment-v2" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["machines"]["storage-2"]["addresses"]["vpn"] = "10.20.30.31"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "duplicates private address"):
                load_inventory(path)
            document["machines"]["storage-2"]["addresses"]["vpn"] = "10.20.30.32"
            document["storage"]["replication"]["target_replicas"] = 3
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "target_replicas"):
                load_inventory(path)

    def test_render_produces_public_group_configs_without_secret_values(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = render_plan(plan, Path(temporary) / "bundle")
            self.assertTrue((output / "shared" / "plan.json").exists())
            apps = json.loads((output / "groups" / "apps" / "group.json").read_text())
            self.assertEqual(apps["machine"]["execution"], "local")
            self.assertIn("components", apps)
            apps_compose = yaml.safe_load((output / "groups" / "apps" / "compose.yaml").read_text())
            self.assertIn("minter-chain-worker", apps_compose["services"])
            storage_a = yaml.safe_load((output / "groups" / "storage-a" / "compose.yaml").read_text())
            storage_b = yaml.safe_load((output / "groups" / "storage-b" / "compose.yaml").read_text())
            self.assertNotEqual(storage_a["services"]["cluster"]["ports"][0], storage_b["services"]["cluster"]["ports"][0])

    def test_preflight_is_read_only_and_reports_each_check(self):
        machine = Machine(
            "local", "local", "127.0.0.1", "10.98.0.10",
            SshSettings("dark", 22, "/tmp/key", None), "/srv/dark", "/srv/dark/data", "/srv/dark/secrets"
        )
        with mock.patch("deployment_v2.executor.LocalExecutor.run", return_value=CommandResult(("test",), 0, "ok\n", "")) as command:
            results = run_preflight(machine)
        self.assertEqual([item["check"] for item in results], ["docker", "compose", "workspace_parent", "data_parent", "secrets_parent"])
        self.assertEqual(command.call_count, 5)
        self.assertTrue(all(item["ok"] for item in results))


if __name__ == "__main__":
    unittest.main()
