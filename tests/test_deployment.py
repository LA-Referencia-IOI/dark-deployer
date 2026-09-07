import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dark_deployer.deployment import DeploymentError, load_deployment_inventory, render_deployment, validate_host_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.topology = self.root / "storage-topology.json"
        # Deliberately non-canonical whitespace proves byte-for-byte bundle copy.
        self.topology.write_text('{\n "version": 3,\n "cluster_name": "dark-global",\n "replication": {"target_replicas": 2, "publish_after_replicas": 1},\n "nodes": [{"id":"storage-a","address":"10.20.30.31"},{"id":"storage-b","address":"10.20.30.32"}],\n "access_groups": {"apps-a": ["storage-a", "storage-b"]}\n}\n')
        self.inventory = {
            "version": 1, "environment": "production", "deployment_id": "dark-site-a-1", "chain_id": 2025,
            "network": {"trust_boundary": "vpn", "vpn_cidr": "10.20.30.0/24", "trusted_minter_clients": ["10.20.30.50/32"]},
            "signing": {"mode": "shared", "platform_key_target": "/opt/dark/secrets/platform.key"},
            "storage": {"topology_file": "storage-topology.json", "swarm_key_target": "/opt/dark/secrets/swarm.key", "cluster_secret_target": "/opt/dark/secrets/cluster.secret"},
            "sites": [{"id": "site-a", "hosts": [
                {"id": "site-a-blockchain-1", "management_address": "chain.internal", "vpn_address": "10.20.30.10", "roles": ["blockchain"]},
                {"id": "site-a-apps-1", "management_address": "apps.internal", "vpn_address": "10.20.30.20", "roles": ["apps"], "storage_access_group": "apps-a"},
                {"id": "site-a-storage-1", "management_address": "ipfs1.internal", "storage_node_id": "storage-a", "roles": ["storage-node"]},
                {"id": "site-a-storage-2", "management_address": "ipfs2.internal", "storage_node_id": "storage-b", "roles": ["storage-node"]},
            ]}],
        }
        self.inventory_path = self.root / "deployment-inventory.json"
        self.inventory_path.write_text(json.dumps(self.inventory))

    def tearDown(self):
        self.temporary.cleanup()

    def test_validates_inventory_selectors_and_does_not_duplicate_storage_addresses(self):
        loaded = load_deployment_inventory(self.inventory_path, PROJECT_ROOT)
        self.assertEqual(loaded["_storage_topology"].node("storage-a").address, "10.20.30.31")
        self.inventory["sites"][0]["hosts"][2]["vpn_address"] = "10.20.30.31"
        self.inventory_path.write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(DeploymentError, "derived from storage"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)

    def test_rejects_bad_node_or_access_group_selector(self):
        self.inventory["sites"][0]["hosts"][2]["storage_node_id"] = "missing"
        self.inventory_path.write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(DeploymentError, "unknown storage node"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)
        self.inventory["sites"][0]["hosts"][2]["storage_node_id"] = "storage-a"
        self.inventory["sites"][0]["hosts"][1]["storage_access_group"] = "missing"
        self.inventory_path.write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(DeploymentError, "unknown storage access group"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)

    def test_render_copies_topology_bytes_and_hashes_it(self):
        output = self.root / "rendered"
        bundle = render_deployment(self.inventory_path, output, PROJECT_ROOT)
        copied = bundle / "shared" / "storage-topology.json"
        self.assertEqual(copied.read_bytes(), self.topology.read_bytes())
        host_config = bundle / "hosts" / "site-a-storage-1" / "host.json"
        validated = validate_host_bundle(host_config, require_secrets=False)
        self.assertEqual(validated["env"]["PRODUCTION_STORAGE_NODE_ID"], "storage-a")
        self.assertEqual(validated["env"]["PRODUCTION_STORAGE_ACCESS_GROUP"], "")
        copied.write_text(copied.read_text() + "\n")
        with self.assertRaisesRegex(DeploymentError, "topology hash"):
            validate_host_bundle(host_config, require_secrets=False)

    def test_apps_bundle_carries_only_access_group(self):
        bundle = render_deployment(self.inventory_path, self.root / "rendered", PROJECT_ROOT)
        values = validate_host_bundle(bundle / "hosts" / "site-a-apps-1" / "host.json", require_secrets=False)["env"]
        self.assertEqual(values["PRODUCTION_STORAGE_ACCESS_GROUP"], "apps-a")
        self.assertEqual(values["PRODUCTION_STORAGE_NODE_ID"], "")
        self.assertNotIn("PRODUCTION_STORAGE_SITE_ID", values)

    def test_rejects_deployer_branch_mismatch_and_nonempty_output(self):
        with mock.patch("dark_deployer.deployment.current_deployer_branch", return_value="other-branch"):
            with self.assertRaisesRegex(DeploymentError, "does not match checkout"):
                load_deployment_inventory(self.inventory_path, PROJECT_ROOT)
        output = self.root / "rendered"
        output.mkdir()
        (output / "operator-file").write_text("keep")
        with self.assertRaisesRegex(DeploymentError, "not empty"):
            render_deployment(self.inventory_path, output, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
