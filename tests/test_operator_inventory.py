"""Unit coverage for dynamic compact operator inventories."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deployment_v3.inventory_resolver import OperatorInventoryError, resolve_inventory
from deployment_v3.artifacts import chain_context, export_chain_group, static_nodes, write_artifact_manifest, verify_artifact_manifest
from deployment_v3.planner import build_plan
from deployment_v3.catalogs import get_catalog


ROOT = Path(__file__).resolve().parents[1]


class InventoryEvolutionTests(unittest.TestCase):
    def test_catalog_is_a_topology_free_versioned_recipe(self):
        catalog = get_catalog("dark-standard-1")
        self.assertEqual(catalog.document["machines"], {})
        self.assertEqual(catalog.document["groups"], {})
        self.assertEqual(set(catalog.service_templates), {"besu-validator", "besu-rpc", "besu-observer", "ipfs-kubo", "ipfs-cluster"})

    def test_operator_v1_is_rejected(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["format_version"] = 1
        with self.assertRaisesRegex(Exception, "2 was expected"):
            resolve_inventory(document, source_path=ROOT / "inventory.json")

    def test_operator_v2_generates_dynamic_chain_and_storage(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["blockchain"]["validator_groups"] = {
            "validators-a": {"validator_count": 1},
            "validators-b": {"validator_count": 3},
        }
        document["blockchain"]["observer_groups"] = {"observers": {"observer_count": 2}}
        document["blockchain"]["rpc"]["nodes"]["rpc02"] = {"group": "apps"}
        document["placement"].update({"validators-a": "local", "validators-b": "local", "observers": "local"})
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        services = resolution.document["services"]
        self.assertEqual(sum(item["type"] == "besu-validator" for item in services.values()), 4)
        self.assertEqual(sum(item["type"] == "besu-observer" for item in services.values()), 2)
        self.assertEqual(sum(item["type"] == "besu-rpc" for item in services.values()), 2)
        self.assertEqual(resolution.document["blockchain"]["primary_rpc"], "rpc01")

    def test_dynamic_chain_context_includes_observers_but_does_not_validate_them(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["blockchain"]["validator_groups"] = {"blockchain-a": {"validator_count": 1}}
        document["blockchain"]["observer_groups"] = {"observers": {"observer_count": 2}}
        document["placement"]["observers"] = "local"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"; path.write_text(json.dumps(document))
            plan = build_plan(path)
        context = chain_context(plan, "0x" + "1" * 40)
        self.assertEqual(context["nodes"]["validator01"]["role"], "validator")
        self.assertEqual(context["nodes"]["observer01"]["role"], "observer")
        keys = {node: "a" * 128 for node in context["nodes"]}
        peers = static_nodes(context, keys)
        self.assertIn("observer01", peers)
        self.assertEqual(len(peers["observer01"]), len(context["nodes"]) - 1)

    def test_resolver_can_bind_an_rpc_consumer_to_a_private_observer(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["blockchain"]["observer_groups"] = {"observers": {"observer_count": 1}}
        document["blockchain"]["rpc"]["bindings"] = {"resolver-api": "observer01"}
        document["placement"]["observers"] = "local"
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(resolution.document["services"]["resolver-api"]["connections"]["rpc"]["service"], "observer01")
        self.assertNotIn("exposure", resolution.document["services"]["observer01"])

    def test_production_rejects_an_unmet_unacknowledged_objective(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "production-five-host.json").read_text())
        document["availability"]["objectives"] = ["rpc_redundant"]
        with self.assertRaisesRegex(OperatorInventoryError, "rpc_redundant"):
            resolve_inventory(document, source_path=ROOT / "inventory.json")

    def test_production_accepts_a_typed_objective_acknowledgement(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "production-five-host.json").read_text())
        document["availability"] = {"objectives": ["rpc_redundant"], "acknowledgements": ["rpc_redundant"]}
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertTrue(any("rpc_redundant" in warning for warning in resolution.warnings))

    def test_chain_export_selects_only_the_requested_observer_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            root.mkdir()
            context = {"nodes": {"validator01": {"group": "validators"}, "observer01": {"group": "observers"}}}
            (root / "chain-context.json").write_text(json.dumps(context))
            (root / "genesis.json").write_text("{}")
            for node in context["nodes"]:
                directory = root / "nodes" / node
                directory.mkdir(parents=True)
                (directory / "nodekey").write_text("a" * 64)
                (directory / "key.pub").write_text("b" * 128)
                static = root / "static-nodes"
                static.mkdir(exist_ok=True)
                (static / f"{node}.json").write_text("[]")
            write_artifact_manifest(root)
            destination = export_chain_group(root, "observers", Path(temporary) / "export")
            self.assertTrue((destination / "nodes" / "observer01" / "nodekey").is_file())
            self.assertFalse((destination / "nodes" / "validator01").exists())
            verify_artifact_manifest(destination)


if __name__ == "__main__":
    unittest.main()
