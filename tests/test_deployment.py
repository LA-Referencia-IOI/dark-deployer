import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dark_deployer.deployment import DeploymentError, load_deployment_topology, render_deployment, validate_host_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentTopologyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.document = json.loads((PROJECT_ROOT / "deployment-topology.example.json").read_text())
        self.path = self.root / "deployment-topology.json"
        self._write()

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self):
        self.path.write_text(json.dumps(self.document))

    def test_validates_embedded_storage_and_host_selectors(self):
        loaded = load_deployment_topology(self.path, PROJECT_ROOT)
        self.assertEqual(loaded["_storage_topology"].node("storage-a").address, "10.20.30.31")
        self.document["hosts"][3]["vpn_address"] = "10.20.30.33"
        self._write()
        with self.assertRaisesRegex(DeploymentError, "must match storage"):
            load_deployment_topology(self.path, PROJECT_ROOT)

    def test_rejects_bad_node_or_access_group_selector(self):
        self.document["hosts"][3]["storage_node_id"] = "missing"
        self._write()
        with self.assertRaisesRegex(DeploymentError, "unknown storage node"):
            load_deployment_topology(self.path, PROJECT_ROOT)
        self.document["hosts"][3]["storage_node_id"] = "storage-a"
        self.document["hosts"][0]["storage_access_group"] = "missing"
        self._write()
        with self.assertRaisesRegex(DeploymentError, "unknown storage access group"):
            load_deployment_topology(self.path, PROJECT_ROOT)

    def test_rendered_bundle_derives_storage_and_runtime_roles(self):
        bundle = render_deployment(self.path, self.root / "rendered", PROJECT_ROOT)
        topology = json.loads((bundle / "shared" / "deployment-topology.json").read_text())
        self.assertEqual(topology["storage"]["replication"]["target_replicas"], 2)
        apps = validate_host_bundle(bundle / "hosts" / "site-a-apps-1" / "host.json", require_secrets=False)["env"]
        chain_a = validate_host_bundle(bundle / "hosts" / "site-a-blockchain-a-1" / "host.json", require_secrets=False)["env"]
        chain_b = validate_host_bundle(bundle / "hosts" / "site-a-blockchain-b-1" / "host.json", require_secrets=False)["env"]
        self.assertEqual(apps["BLOCKCHAIN_RUNTIME_ROLE"], "rpc")
        self.assertEqual(chain_a["BLOCKCHAIN_RUNTIME_ROLE"], "validators-a")
        self.assertEqual(chain_b["BLOCKCHAIN_RUNTIME_ROLE"], "validators-b")
        self.assertEqual(apps["PRODUCTION_DEPLOYMENT_TOPOLOGY_FILE"], ".generated/deployment-topology.json")

    def test_rejects_branch_mismatch_and_nonempty_output(self):
        with mock.patch("dark_deployer.deployment.current_deployer_branch", return_value="other-branch"):
            with self.assertRaisesRegex(DeploymentError, "does not match checkout"):
                load_deployment_topology(self.path, PROJECT_ROOT)
        output = self.root / "rendered"
        output.mkdir()
        (output / "operator-file").write_text("keep")
        with self.assertRaisesRegex(DeploymentError, "not empty"):
            render_deployment(self.path, output, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
