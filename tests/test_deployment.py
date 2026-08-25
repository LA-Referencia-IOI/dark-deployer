import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dark_deployer.deployment import (
    COMPONENTS_BY_ROLE,
    DeploymentError,
    load_deployment_inventory,
    render_deployment,
    validate_host_bundle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        components = set().union(*COMPONENTS_BY_ROLE.values())
        lock = {
            "version": 1,
            "components": {
                name: {
                    "repository": f"https://example.invalid/{name}.git",
                    "commit": "a" * 40,
                    "branch": "main",
                }
                for name in components
            },
        }
        (self.root / "components.lock.json").write_text(json.dumps(lock))
        self.inventory = {
            "version": 1,
            "environment": "production",
            "deployment_id": "dark-site-a-1",
            "chain_id": 2025,
            "release": {
                "deployer_commit": self.commit,
                "components_lock": "components.lock.json",
            },
            "network": {
                "trust_boundary": "vpn",
                "vpn_cidr": "10.20.30.0/24",
                "trusted_minter_clients": ["10.20.30.50/32"],
            },
            "signing": {
                "mode": "shared",
                "platform_key_target": "/opt/dark/secrets/platform.key",
            },
            "storage": {
                "cluster_name": "dark-global",
                "swarm_key_target": "/opt/dark/secrets/swarm.key",
                "cluster_secret_target": "/opt/dark/secrets/cluster.secret",
            },
            "sites": [{
                "id": "site-a",
                "hosts": [
                    {
                        "id": "site-a-blockchain-1",
                        "management_address": "chain.internal",
                        "vpn_address": "10.20.30.10",
                        "roles": ["blockchain"],
                    },
                    {
                        "id": "site-a-apps-1",
                        "management_address": "apps.internal",
                        "vpn_address": "10.20.30.20",
                        "roles": ["apps"],
                    },
                    {
                        "id": "site-a-storage-1",
                        "management_address": "ipfs1.internal",
                        "vpn_address": "10.20.30.31",
                        "roles": ["storage-node"],
                    },
                    {
                        "id": "site-a-storage-2",
                        "management_address": "ipfs2.internal",
                        "vpn_address": "10.20.30.32",
                        "roles": ["storage-node"],
                    },
                ],
            }],
        }
        self.inventory_path = self.root / "deployment-inventory.json"
        self.inventory_path.write_text(json.dumps(self.inventory))

    def tearDown(self):
        self.temporary.cleanup()

    def test_validates_exact_four_host_shape_and_complete_lock(self):
        loaded = load_deployment_inventory(self.inventory_path, PROJECT_ROOT)
        self.assertEqual(loaded["deployment_id"], "dark-site-a-1")

        self.inventory["sites"][0]["hosts"].pop()
        self.inventory_path.write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(DeploymentError, "exactly four hosts"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)

    def test_rejects_incomplete_component_lock(self):
        lock_path = self.root / "components.lock.json"
        lock = json.loads(lock_path.read_text())
        del lock["components"]["dark-ipfs"]
        lock_path.write_text(json.dumps(lock))
        with self.assertRaisesRegex(DeploymentError, "missing: dark-ipfs"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)

    def test_accepts_legacy_available_policy_but_rejects_strict_policy(self):
        self.inventory["storage"]["strict_single_site"] = False
        self.inventory_path.write_text(json.dumps(self.inventory))
        loaded = load_deployment_inventory(self.inventory_path, PROJECT_ROOT)
        self.assertEqual(loaded["storage"]["strict_single_site"], False)

        self.inventory["storage"]["strict_single_site"] = True
        self.inventory_path.write_text(json.dumps(self.inventory))
        with self.assertRaisesRegex(DeploymentError, "true is no longer supported"):
            load_deployment_inventory(self.inventory_path, PROJECT_ROOT)

    def test_render_is_public_and_detects_topology_drift(self):
        output = self.root / "rendered"
        bundle = render_deployment(self.inventory_path, output, PROJECT_ROOT)
        host_config = bundle / "hosts" / "site-a-storage-1" / "host.json"
        validated = validate_host_bundle(host_config, require_secrets=False)

        self.assertEqual(validated["config"]["host"]["role"], "storage-node")
        rendered_text = "\n".join(
            path.read_text()
            for path in bundle.rglob("*")
            if path.is_file()
        )
        self.assertNotIn("PRIVATE_KEY=", rendered_text)
        self.assertNotIn("a real secret", rendered_text)

        topology = bundle / "shared" / "storage-topology.json"
        topology.write_text(topology.read_text() + "\n")
        with self.assertRaisesRegex(DeploymentError, "topology hash"):
            validate_host_bundle(host_config, require_secrets=False)

    def test_render_refuses_nonempty_output(self):
        output = self.root / "rendered"
        output.mkdir()
        (output / "keep.txt").write_text("operator data")
        with self.assertRaisesRegex(DeploymentError, "not empty"):
            render_deployment(self.inventory_path, output, PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
