import json
import tempfile
import unittest
from pathlib import Path

from dark_deployer.storage import (
    StorageTopologyError,
    developer_runtime,
    load_storage_topology,
    node_environment,
    parse_json_stream,
    pin_entries,
    production_runtime,
    runtime_document,
)


def topology_document() -> dict:
    return {
        "version": 3,
        "cluster_name": "dark-global",
        "replication": {"publish_after_replicas": 1, "target_replicas": 2},
        "nodes": [
            {"id": "storage-a", "address": "10.200.1.11"},
            {"id": "storage-b", "address": "10.200.1.12"},
        ],
        "access_groups": {"apps-a": ["storage-a", "storage-b"]},
    }


class StorageTopologyTests(unittest.TestCase):
    def load(self, document: dict):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "topology.json"
            path.write_text(json.dumps(document))
            return load_storage_topology(path)

    def test_production_runtime_derives_urls_and_group_pool(self):
        topology = self.load(topology_document())
        runtime = production_runtime(topology)
        self.assertEqual(runtime.policy.target_replicas, 2)
        self.assertEqual(runtime.node("storage-a").ipfs_api_url, "http://10.200.1.11:5001")
        self.assertEqual([node.id for node in topology.access_group_nodes("apps-a")], ["storage-a", "storage-b"])

    def test_groups_are_optional_and_unknown_members_rejected(self):
        document = topology_document()
        document["access_groups"] = {}
        self.assertEqual(len(self.load(document).access_group_nodes("")), 2)
        document = topology_document()
        document["access_groups"]["apps-a"] = ["missing"]
        with self.assertRaisesRegex(StorageTopologyError, "unknown node"):
            self.load(document)

    def test_rejects_legacy_schema_and_invalid_policy(self):
        document = topology_document()
        document["version"] = 2
        with self.assertRaisesRegex(StorageTopologyError, "version 3"):
            self.load(document)
        document = topology_document()
        document["replication"]["target_replicas"] = 3
        with self.assertRaisesRegex(StorageTopologyError, "cannot exceed"):
            self.load(document)
        document = topology_document()
        document["replication"]["publish_after_replicas"] = 3
        with self.assertRaisesRegex(StorageTopologyError, "cannot exceed"):
            self.load(document)

    def test_rejects_duplicate_ids_addresses_and_group_members(self):
        document = topology_document()
        document["nodes"][1]["id"] = "storage-a"
        with self.assertRaisesRegex(StorageTopologyError, "duplicate node id"):
            self.load(document)
        document = topology_document()
        document["nodes"][1]["address"] = "10.200.1.11"
        with self.assertRaisesRegex(StorageTopologyError, "duplicate node address"):
            self.load(document)
        document = topology_document()
        document["access_groups"]["apps-a"] = ["storage-a", "storage-a"]
        with self.assertRaisesRegex(StorageTopologyError, "duplicate nodes"):
            self.load(document)

    def test_developer_presets_are_generated_not_manual_topologies(self):
        simple = developer_runtime("simple")
        ha = developer_runtime("ha")
        self.assertEqual((len(simple.nodes), simple.policy.target_replicas), (1, 1))
        self.assertEqual((len(ha.nodes), ha.policy.target_replicas), (2, 2))
        self.assertFalse(simple.nodes[0].address)
        endpoints = json.loads(runtime_document(ha))
        self.assertEqual(len(endpoints["nodes"]), 2)
        self.assertNotIn("cluster_proxy_url", endpoints["nodes"][0])

    def test_node_environment_bootstraps_every_other_node_and_targets_cluster(self):
        runtime = production_runtime(self.load(topology_document()))
        generated = node_environment(runtime, "storage-b", "/swarm", "/cluster")
        self.assertEqual(generated["IPFS_BOOTSTRAP_HOSTS"], "10.200.1.11")
        self.assertEqual(generated["CLUSTER_REPLICATION_MIN"], "1")
        self.assertEqual(generated["CLUSTER_REPLICATION_MAX"], "2")

    def test_developer_node_environment_announces_its_docker_alias(self):
        runtime = developer_runtime("ha")
        generated = node_environment(runtime, "developer-storage-1", "/swarm", "/cluster")
        self.assertEqual(
            generated["IPFS_ANNOUNCE_MULTIADDRESS"],
            "/dns4/dark-ipfs-developer-storage-1/tcp/4001",
        )
        self.assertEqual(
            generated["CLUSTER_ANNOUNCE_MULTIADDRESS"],
            "/dns4/dark-ipfs-cluster-developer-storage-1/tcp/9096",
        )

    def test_json_stream_and_pin_entries(self):
        self.assertEqual(parse_json_stream('{"a":1}\n{"b":2}'), [{"a": 1}, {"b": 2}])
        self.assertEqual(pin_entries('{"pins":[{"cid":"a"}]}'), [{"cid": "a"}])


if __name__ == "__main__":
    unittest.main()
