import json
import tempfile
import unittest
from pathlib import Path

from dark_deployer.storage import (
    StorageTopologyError,
    load_storage_topology,
    node_environment,
    parse_json_stream,
    pin_entries,
    store_api_environment,
)


def topology_document(site_count: int = 2) -> dict:
    sites = []
    for site_number in range(1, site_count + 1):
        site_id = f"site-{site_number}"
        sites.append({
            "id": site_id,
            "peers": [
                {
                    "id": f"{site_id}-storage-{node}",
                    "vpn_address": f"10.200.{site_number}.{10 + node}",
                }
                for node in (1, 2)
            ],
        })
    return {"version": 1, "cluster_name": "dark-global", "sites": sites}


class StorageTopologyTests(unittest.TestCase):
    def load(self, document: dict):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "topology.json"
            path.write_text(json.dumps(document))
            return load_storage_topology(path)

    def test_multisite_policy_and_site_endpoints(self):
        topology = self.load(topology_document(2))

        self.assertEqual(topology.sites, ("site-1", "site-2"))
        self.assertEqual(topology.policy.replication_min, 3)
        self.assertEqual(topology.policy.replication_max, 4)
        self.assertEqual(topology.policy.write_min_sites, 2)
        generated = store_api_environment(topology, "site-1")
        self.assertEqual(len(json.loads(generated["IPFS_API_URLS_JSON"])), 2)
        self.assertEqual(generated["IPFS_CLUSTER_WRITE_MIN_PEERS"], "3")

    def test_single_site_available_and_strict_policies(self):
        document = topology_document(1)
        self.assertEqual(self.load(document).policy.replication_min, 1)
        document["strict_single_site"] = True
        self.assertEqual(self.load(document).policy.replication_min, 2)

    def test_node_environment_uses_all_other_peers_as_bootstraps(self):
        topology = self.load(topology_document(2))
        generated = node_environment(topology, "site-1-storage-2", "/swarm", "/cluster")

        self.assertEqual(generated["SITE_ID"], "site-1")
        self.assertEqual(len(generated["IPFS_BOOTSTRAP_HOSTS"].split()), 3)
        self.assertEqual(len(generated["CLUSTER_BOOTSTRAP_HOSTS"].split()), 3)
        self.assertEqual(generated["CLUSTER_REPLICATION_MAX"], "4")
        self.assertEqual(generated["CLUSTER_SEED"], "false")

    def test_first_node_is_recovery_capable_seed(self):
        topology = self.load(topology_document(1))
        generated = node_environment(topology, "site-1-storage-1", "/swarm", "/cluster")
        self.assertEqual(generated["CLUSTER_SEED"], "true")
        self.assertEqual(generated["CLUSTER_BOOTSTRAP_HOSTS"], "10.200.1.12")

    def test_rejects_any_site_without_two_peers(self):
        document = topology_document(1)
        document["sites"][0]["peers"].pop()
        with self.assertRaisesRegex(StorageTopologyError, "exactly two peers"):
            self.load(document)

    def test_rejects_duplicate_vpn_address(self):
        document = topology_document(1)
        document["sites"][0]["peers"][1]["vpn_address"] = "10.200.1.11"
        with self.assertRaisesRegex(StorageTopologyError, "duplicate VPN address"):
            self.load(document)

    def test_cluster_json_stream_is_normalized(self):
        raw = '{"cid":"bafy1"}\n{"cid":"bafy2"}\n'
        self.assertEqual(len(parse_json_stream(raw)), 2)
        self.assertEqual([entry["cid"] for entry in pin_entries(raw)], ["bafy1", "bafy2"])


if __name__ == "__main__":
    unittest.main()
