"""Coverage for explicit-placement deployment inventory v3."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deployment_v3.inventory import InventoryError, load_inventory
from deployment_v3.artifacts import chain_context
from deployment_v3 import readiness
from deployment_v3 import verify as verify_module
from deployment_v3.planner import build_plan
from deployment_v3.render import render_plan
from deployment_v3.inventory_editor.textual_app import SECTIONS, SECTION_HELP
from deployment_v3.inventory_resolver import OperatorInventoryError, resolve_inventory_path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentV3Tests(unittest.TestCase):
    def test_editor_exposes_v3_services_and_help(self):
        identifiers = tuple(identifier for identifier, _ in SECTIONS)
        self.assertIn("services", identifiers)
        self.assertIn("groups", identifiers)
        self.assertIn("Minter API", SECTION_HELP["services"])

    def test_explicit_server_groups_preserve_five_group_topology(self):
        source = ROOT / "examples" / "deployment-v3" / "local-ha.json"
        document = json.loads(source.read_text())
        groups = {
            "apps": {"kind": "apps", "machine": "local", "members": ["apps"], "services": []},
            "blockchain-a": {"kind": "validators", "machine": "local", "members": ["validator01", "validator02"], "services": ["validator01", "validator02"]},
            "blockchain-b": {"kind": "validators", "machine": "local", "members": ["validator03", "validator04"], "services": ["validator03", "validator04"]},
            "storage-1": {"kind": "storage", "machine": "local", "members": ["storage-a"], "services": ["ipfs-storage-a", "cluster-storage-a"]},
            "storage-2": {"kind": "storage", "machine": "local", "members": ["storage-b"], "services": ["ipfs-storage-b", "cluster-storage-b"]},
        }
        groups["apps"]["services"] = [service_id for service_id in document["services"] if service_id not in {"validator01", "validator02", "validator03", "validator04", "ipfs-storage-a", "cluster-storage-a", "ipfs-storage-b", "cluster-storage-b"}]
        document["groups"] = groups
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "grouped.json"; path.write_text(json.dumps(document))
            plan = build_plan(path)
        self.assertEqual([group.id for group in plan.groups], list(groups))
        self.assertEqual(sum(len(group.service_ids) for group in plan.groups), len(plan.services))
        service_steps = [step for step in plan.steps if step.action == "service_apply"]
        self.assertEqual({step.group_id for step in service_steps}, set(groups))

    def test_all_examples_validate_and_render(self):
        for name in ("local-simple", "local-ha", "production-five-host"):
            plan = build_plan(ROOT / "examples" / "deployment-v3" / f"{name}.json")
            self.assertTrue(plan.services)
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "bundle"
                render_plan(plan, output)
                self.assertTrue((output / "machines").is_dir())
                if name == "local-ha":
                    machine = output / "machines" / "local"
                    self.assertEqual(
                        {path.name for path in (machine / "groups").iterdir()},
                        {"apps", "blockchain-a", "blockchain-b", "storage-1", "storage-2"},
                    )
                    self.assertIn("name: dark-local-ha-local-apps", (machine / "groups" / "apps" / "compose.yaml").read_text())

    def test_operator_examples_resolve_to_valid_v3_and_render_resolved_contract(self):
        for name, services in (("local-simple", 22), ("local-ha", 24), ("production-five-host", 25)):
            source = ROOT / "examples" / "operator-inventory" / f"{name}.json"
            resolution = resolve_inventory_path(source)
            self.assertEqual(len(resolution.document["services"]), services)
            self.assertEqual(resolution.metadata["catalog"], "dark-standard-1")
            plan = build_plan(source)
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "bundle"
                render_plan(plan, output)
                bundled = json.loads((output / "shared" / "deployment-topology.json").read_text())
                evidence = json.loads((output / "shared" / "inventory-resolution.json").read_text())
            self.assertEqual(bundled, resolution.document)
            self.assertEqual(evidence["metadata"], resolution.metadata)

    def test_operator_inventory_reports_missing_remote_route(self):
        source = ROOT / "examples" / "operator-inventory" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["routing"].pop("storage_api")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid-operator.json"; path.write_text(json.dumps(document))
            with self.assertRaisesRegex(OperatorInventoryError, "routing.storage_api"):
                resolve_inventory_path(path)

    def test_minter_services_must_share_machine(self):
        source = ROOT / "examples" / "deployment-v3" / "local-ha.json"
        document = json.loads(source.read_text())
        document["machines"]["other"] = {"execution": "local", "management_address": "127.0.0.2", "addresses": {"lab": "10.99.0.11"}}
        document["services"]["chain-worker"]["machine"] = "other"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"; path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "must share one machine"):
                load_inventory(path)

    def test_remote_dependency_requires_private_exposure(self):
        source = ROOT / "examples" / "deployment-v3" / "local-ha.json"
        document = json.loads(source.read_text())
        document["machines"]["other"] = {"execution": "local", "management_address": "127.0.0.2", "addresses": {"lab": "10.99.0.11"}}
        document["services"]["explorer"]["machine"] = "other"
        document["services"]["rpc01"]["exposure"] = {"mode": "loopback", "port": 8545}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"; path.write_text(json.dumps(document))
            with self.assertRaises(InventoryError):
                build_plan(path)

    def test_production_uses_explicit_lan_vpn_routes_and_edge_proxy(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "production-five-host.json")
        self.assertEqual(plan.raw["infrastructure"]["besu"]["network"], "vpn")
        self.assertEqual(plan.service("explorer").connections["rpc"]["network"], "lan")
        self.assertEqual(plan.service("edge-proxy").connections["explorer"]["network"], "lan")
        self.assertEqual(plan.service("edge-proxy").exposure["mode"], "public")
        self.assertEqual(
            [(group.id, group.machine_id) for group in plan.groups],
            [("apps", "apps"), ("blockchain-a", "blockchain-a"), ("blockchain-b", "blockchain-b"), ("storage-1", "storage-1"), ("storage-2", "storage-2")],
        )
        self.assertEqual(plan.raw["infrastructure"]["besu"]["p2p_port_start"], 30303)

    def test_local_chain_context_uses_standard_internal_p2p_port(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        context = chain_context(plan, "0x" + "1" * 40)
        self.assertEqual(
            {node["p2p_port"] for node in context["nodes"].values()},
            {30303},
        )

    def test_remote_chain_context_derives_host_p2p_ports(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "production-five-host.json")
        context = chain_context(plan, "0x" + "1" * 40)
        self.assertEqual(
            [context["nodes"][node]["p2p_port"] for node in ("validator01", "validator02", "validator03", "validator04", "rpc01")],
            [30303, 30304, 30305, 30306, 30307],
        )

    def test_local_firewall_suggestion_uses_internal_p2p_port(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            firewall = json.loads((output / "shared" / "firewall-suggestion.json").read_text())
        besu_ports = {entry["port"] for entry in firewall["local"] if entry.get("purpose") == "besu-p2p"}
        self.assertEqual(besu_ports, {30303})

    def test_data_readiness_probes_store_health_not_root(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        calls = []
        original = readiness._curl
        try:
            readiness._curl = lambda _plan, _machine, url, payload=None: calls.append(url) or type("Result", (), {"returncode": 0})()
            ok, evidence = readiness.check_phase(plan, ROOT, "data")
        finally:
            readiness._curl = original
        self.assertTrue(ok)
        self.assertEqual(evidence, "Store API health is ready")
        self.assertEqual(calls, ["http://127.0.0.1:8003/health"])

    def test_verification_probes_store_health_not_root(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        store = plan.service("store-api")
        self.assertEqual(verify_module._store_health_url(store, plan.machine("local")), "http://127.0.0.1:8003/health?refresh=true")

    def test_dashboard_health_endpoints_are_generated_from_inventory(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            env = (output / "machines" / "local" / "groups" / "apps" / "env" / "dashboard.env").read_text()
        self.assertIn("BLOCK_NUMBER=http://rpc01:8545\n", env)
        self.assertIn("LIVENESS=http://rpc01:8545/liveness\n", env)
        self.assertIn("IPFS_API_BASE_URL=http://ipfs-storage-a:5001\n", env)
        self.assertIn("IPFS_CLUSTER_API_URL=http://cluster-storage-a:9094\n", env)
        self.assertIn("WORKER_STATUS_URL=http://minter-api:8001/api/v1/worker/status\n", env)

    def test_cross_host_connection_requires_explicit_shared_network(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["services"]["explorer"]["connections"]["rpc"].pop("network")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"; path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "network is required"):
                load_inventory(path)


if __name__ == "__main__": unittest.main()
