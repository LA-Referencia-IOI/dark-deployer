"""Unit coverage for dynamic compact operator inventories."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deployment_v3.inventory_resolver import OperatorInventoryError, resolve_inventory
from deployment_v3.artifacts import chain_context, export_chain_group, static_nodes, write_artifact_manifest, verify_artifact_manifest
from deployment_v3.availability import analyze
from deployment_v3.planner import build_plan
from deployment_v3.render import render_plan
from deployment_v3.catalogs import get_catalog
from deployment_v3.executor import run_network_preflight
from deployment_v3.sources import COMPONENT_PATHS


ROOT = Path(__file__).resolve().parents[1]


class InventoryEvolutionTests(unittest.TestCase):
    def test_monitoring_component_is_a_stable_catalog_source(self):
        catalog = get_catalog("dark-platform-baseline-v1.0")
        component = catalog.document["components"]["dark-monitoring"]
        self.assertEqual(component["branch"], "main")
        self.assertEqual(component["repository_url"], "git@github.com:LA-Referencia-IOI/dark-monitoring.git")
        self.assertEqual(COMPONENT_PATHS["dark-monitoring"], "components/dark-monitoring")

    def test_monitoring_inventory_overrides_only_experimental_branches(self):
        source = ROOT / "examples" / "operator-inventory" / "local-ha-monitoring.json"
        resolved = resolve_inventory(json.loads(source.read_text()), source_path=source).document
        components = resolved["components"]
        self.assertEqual(components["dark-monitoring"]["branch"], "monitoriing")
        self.assertEqual(components["dashboard-web"]["branch"], "monitoriing")
        self.assertEqual(components["dark-explorador"]["branch"], "monitoriing")

    def test_catalog_is_a_topology_free_versioned_recipe(self):
        catalog = get_catalog("dark-platform-baseline-v1.0")
        self.assertEqual(catalog.document["machines"], {})
        self.assertEqual(catalog.document["groups"], {})
        self.assertEqual(set(catalog.service_templates), {"besu-validator", "besu-rpc", "besu-observer", "ipfs-kubo", "ipfs-cluster"})
        self.assertEqual(
            set(catalog.document["images"]),
            {"besu", "postgres", "mysql", "dashboard", "kubo", "ipfs_cluster", "edge_proxy"},
        )

    def test_runtime_images_are_inherited_from_the_catalog(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(resolution.document["images"], get_catalog("dark-platform-baseline-v1.0").document["images"])
        self.assertNotIn("besu_image", resolution.document["blockchain"])

    def test_sandbox_minter_shoulder_can_use_lowercase_alphanumeric_characters(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "one-server-aws-sandbox.json").read_text())
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(resolution.document["settings"]["minter"]["shoulder"], "2s0")

    def test_minter_shoulder_rejects_non_lowercase_alphanumeric_2xx_values(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        for shoulder in ("20", "2000", "2S0", "2-0", "001"):
            with self.subTest(shoulder=shoulder):
                document["overrides"]["settings"]["minter"]["shoulder"] = shoulder
                with self.assertRaisesRegex(OperatorInventoryError, "2xx"):
                    resolve_inventory(document, source_path=ROOT / "inventory.json")

    def test_minter_log_level_defaults_to_warning_and_is_overridable(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-simple.json").read_text())
        default_resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(default_resolution.document["settings"]["minter"]["logging"], {"level": "WARNING"})

        document["overrides"]["settings"]["minter"]["logging"] = {"level": "warning"}
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(resolution.document["settings"]["minter"]["logging"], {"level": "warning"})

    def test_minter_log_level_rejects_unknown_value(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-simple.json").read_text())
        document["overrides"]["settings"]["minter"]["logging"] = {"level": "verbose"}

        with self.assertRaisesRegex(OperatorInventoryError, "logging.level"):
            resolve_inventory(document, source_path=ROOT / "inventory.json")

    def test_store_log_level_defaults_to_warning_and_is_overridable(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-simple.json").read_text())
        default_resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(default_resolution.document["settings"]["store"]["logging"], {"level": "WARNING"})

        document["overrides"]["settings"]["store"] = {"logging": {"level": "debug"}}
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        self.assertEqual(resolution.document["settings"]["store"]["logging"], {"level": "debug"})

    def test_store_log_level_rejects_unknown_value(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-simple.json").read_text())
        document["overrides"]["settings"]["store"] = {"logging": {"level": "verbose"}}

        with self.assertRaisesRegex(OperatorInventoryError, "logging.level"):
            resolve_inventory(document, source_path=ROOT / "inventory.json")

    def test_aws_active_two_site_inventory_keeps_aws_quorum_after_site_b_validator_loss(self):
        inventory_path = ROOT / "examples" / "operator-inventory" / "aws-active-two-site-nine-host.json"
        plan = build_plan(inventory_path)
        report = analyze(plan)

        self.assertEqual((report.validators, report.quorum), (7, 5))
        site_b_loss = next(
            scenario for scenario in report.scenarios
            if scenario.kind == "validator_group" and scenario.target == "blockchain-c"
        )
        self.assertEqual((site_b_loss.consensus, site_b_loss.validators_remaining), ("continues", 5))
        self.assertEqual(report.storage_target_replicas, 3)
        self.assertEqual(
            {service.type for service in plan.services if service.type.startswith("dashboard")},
            {"dashboard", "dashboard-mysql", "dashboard-migrate"},
        )
        self.assertEqual(
            {service.id for service in plan.services if service.machine_id.startswith("site-b")},
            {"validator06", "validator07", "observer02", "store-api-reader-site-b", "resolver-api-site-b", "resolver-public-site-b"},
        )

    def test_aws_two_site_declares_local_resolver_reader_and_observer_rpc(self):
        path = ROOT / "examples/operator-inventory/aws-active-two-site-nine-host.json"
        resolution = resolve_inventory(json.loads(path.read_text()), source_path=path)
        services = resolution.document["services"]
        self.assertEqual(services["resolver-api-site-b"]["connections"]["rpc"], {"service": "observer02"})
        self.assertEqual(services["resolver-api-site-b"]["connections"]["store_api"], {"service": "store-api-reader-site-b"})
        self.assertEqual(services["store-api-reader-site-b"]["configuration"], {"mode": "read_only"})
        self.assertEqual(services["resolver-public-site-b"]["connections"]["arks-site-b"], {"service": "resolver-api-site-b"})

    def test_aws_active_six_disables_explorer_and_uses_private_minter_prefix(self):
        path = ROOT / "examples/operator-inventory/dark2-prod-aws.json"
        resolution = resolve_inventory(json.loads(path.read_text()), source_path=path)
        services = resolution.document["services"]
        self.assertNotIn("explorer", services)
        self.assertNotIn("dashboard-redis", services)
        routes = [
            (proxy, route)
            for proxy in services.values()
            if proxy["type"] == "edge-proxy"
            for site in proxy["configuration"]["sites"]
            for route in site["routes"]
        ]
        resolved_routes = [
            (route, proxy["connections"][route["connection"]]["service"])
            for proxy, route in routes
        ]
        self.assertEqual(
            [(route["path"], route["upstream_path"]) for route, service in resolved_routes if service == "minter-api"],
            [
                ("/health", "/health"),
                ("/api/v1/", "/api/v1/"),
            ],
        )
        self.assertFalse(any(route["path"] in {"/api/docs", "/api/openapi.json"} for route, _ in resolved_routes))

    def test_resolver_store_replica_uses_only_same_site_clusters(self):
        document = json.loads((ROOT / "examples" / "operator-inventory/aws-active-two-site-nine-host.json").read_text())
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        connections = resolution.document["services"]["store-api-reader"]["connections"]
        self.assertEqual(set(connections), {"cluster_cluster-storage-a", "cluster_cluster-storage-b"})
        self.assertNotIn("cluster_cluster-storage-c", connections)

    def test_local_observer_uses_resolver_observer_bundle(self):
        path = ROOT / "examples/operator-inventory/local-observer.json"
        resolution = resolve_inventory(json.loads(path.read_text()), source_path=path)
        bundle = resolution.document["groups"]["resolver-observer"]["members"]
        self.assertEqual(set(bundle), {"observer01", "resolver-api", "store-api-reader"})

    def test_operator_v1_is_rejected(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["format_version"] = 1
        with self.assertRaisesRegex(Exception, r"not one of \[2, 3\]"):
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

    def test_operator_v3_derives_lan_and_vpn_peer_matrix(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "production-six-host.json").read_text())
        document["format_version"] = 3
        document["networks"] = {
            "eu-lan": {"kind": "lan", "cidr": "10.10.0.0/24"},
            "us-lan": {"kind": "lan", "cidr": "10.20.0.0/24"},
            "mesh-vpn": {"kind": "vpn", "cidr": "10.200.0.0/24"},
        }
        document["sites"] = {"eu": {"lan": "eu-lan"}, "us": {"lan": "us-lan"}}
        eu = {"apps", "resolver", "blockchain-a", "storage-1", "storage-2"}
        for index, (machine_id, machine) in enumerate(document["machines"].items(), start=10):
            local = "eu-lan" if machine_id in eu else "us-lan"
            site = "eu" if machine_id in eu else "us"
            prefix = "10.10.0" if site == "eu" else "10.20.0"
            machine["site"] = site
            machine["addresses"] = {local: f"{prefix}.{index}", "mesh-vpn": f"10.200.0.{index}"}
        for proxy in document["proxies"].values():
            if proxy["listener"]["bind"] == "private":
                proxy["listener"]["network"] = "eu-lan"
        document["routing"] = {
            "defaults": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
            "blockchain_p2p": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
            "storage_p2p": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
            "storage_api": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
            "application_api": {"same_site": "site_lan", "cross_site": "mesh-vpn"},
        }
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        peerings = resolution.document["peerings"]
        self.assertEqual(resolution.metadata["operator_format_version"], "3")
        local_edge = next(edge for edge in peerings["blockchain"] if edge["from"] == "validator01" and edge["to"] == "validator02")
        remote_edge = next(edge for edge in peerings["blockchain"] if edge["from"] == "validator01" and edge["to"] == "validator03")
        self.assertEqual(local_edge["network"], "eu-lan")
        self.assertEqual(remote_edge["network"], "mesh-vpn")
        self.assertEqual(len(peerings["ipfs"]), 2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "operator.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
            bundle = Path(temporary) / "bundle"
            render_plan(plan, bundle)
            env = (bundle / "machines" / "storage-1" / "groups" / "storage-1" / "env" / "ipfs-storage-a.env").read_text()
            compose = (bundle / "machines" / "blockchain-a" / "groups" / "blockchain-a" / "compose.yaml").read_text()
            self.assertIn('IPFS_ANNOUNCE_MULTIADDRESSES=["/ip4/10.10.0.14/tcp/4001"]', env)
            self.assertIn("10.10.0.12:30303:30303/tcp", compose)
            self.assertIn("10.200.0.12:30303:30303/tcp", compose)
        context = chain_context(plan, "0x" + "1" * 40)
        self.assertEqual(context["version"], 3)
        peers = static_nodes(context, {node: "a" * 128 for node in context["nodes"]})
        self.assertIn("@10.10.0.12:", " ".join(peers["validator01"]))
        self.assertIn("@10.200.0.", " ".join(peers["validator01"]))

    def test_local_two_site_docker_lab_uses_network_qualified_dns(self):
        path = ROOT / "examples" / "operator-inventory" / "local-two-site.json"
        plan = build_plan(path)
        self.assertTrue(all(machine.execution == "docker-lab" for machine in plan.machines))
        self.assertTrue(all(item["ok"] for item in run_network_preflight(plan)))
        local = next(edge for edge in plan.raw["peerings"]["blockchain"] if edge["from"] == "validator01" and edge["to"] == "validator02")
        remote = next(edge for edge in plan.raw["peerings"]["blockchain"] if edge["from"] == "validator01" and edge["to"] == "validator03")
        self.assertEqual(local["network"], "site-a-lan")
        self.assertEqual(remote["network"], "mesh-vpn")
        context = chain_context(plan, "0x" + "1" * 40)
        peers = static_nodes(context, {node: "a" * 128 for node in context["nodes"]})
        local_peer = next(edge for edge in context["peerings"]["blockchain"] if edge["from"] == "validator01" and edge["to"] == "validator02")
        self.assertIn(f"@{local_peer['address']}:{local_peer['port']}", " ".join(peers["validator01"]))
        self.assertIn("@172.24.30.", " ".join(peers["validator01"]))
        self.assertEqual(plan.service("resolver-api-site-a").connections["rpc"], {"service": "observer02"})
        self.assertEqual(plan.service("resolver-api-site-a").connections["store_api"], {"service": "store-api-reader-site-a"})
        self.assertEqual(plan.service("store-api-reader-site-a").configuration["mode"], "read_only")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            render_plan(plan, bundle)
            compose = (bundle / "machines" / "storage-1" / "groups" / "storage-1" / "compose.yaml").read_text()
            env = (bundle / "machines" / "storage-1" / "groups" / "storage-1" / "env" / "ipfs-storage-a.env").read_text()
            self.assertIn("dark-operator-local-two-site-mesh-vpn", compose)
            self.assertIn("ipfs-storage-a-mesh-vpn", compose)
            self.assertRegex(compose, r"ipv4_address: 172\.24\.10\.\d+")
            self.assertIn("/dns4/ipfs-storage-b-mesh-vpn/tcp/5001@4001", env)

    def test_lima_two_site_storage_prefers_lan_and_uses_vpn_cross_site(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-two-site-five-host.json")
        self.assertEqual({edge["network"] for edge in plan.raw["peerings"]["ipfs"]}, {"mesh-vpn"})
        self.assertEqual({edge["network"] for edge in plan.raw["peerings"]["cluster"]}, {"mesh-vpn"})

        kubo_a = plan.service("ipfs-storage-a")
        cluster_a = plan.service("cluster-storage-a")
        self.assertEqual({item["network"] for item in kubo_a.listeners}, {"site-a-lan", "mesh-vpn"})
        self.assertEqual({item["network"] for item in cluster_a.listeners}, {"site-a-lan", "mesh-vpn"})
        self.assertEqual(plan.service("ipfs-storage-b").exposure["network"], "mesh-vpn")
        self.assertEqual(plan.service("cluster-storage-b").exposure["network"], "mesh-vpn")

        store = plan.service("store-api")
        self.assertEqual(store.connections["cluster_cluster-storage-a"]["network"], "site-a-lan")
        self.assertEqual(store.connections["cluster_cluster-storage-b"]["network"], "mesh-vpn")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            render_plan(plan, bundle)
            compose = (bundle / "machines" / "storage-1" / "groups" / "storage-1" / "compose.yaml").read_text()
            self.assertIn("10.241.10.12:5001:5001/tcp", compose)
            self.assertIn("10.250.30.12:5001:5001/tcp", compose)
            self.assertIn("10.250.30.12:4001:4001/tcp", compose)
            self.assertIn("10.241.10.12:9094:9094/tcp", compose)
            self.assertIn("10.250.30.12:9094:9094/tcp", compose)
            self.assertIn("10.250.30.12:9096:9096/tcp", compose)

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

    def test_store_api_replica_is_colocated_with_its_resolver_consumer(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "production-six-host.json").read_text())
        resolution = resolve_inventory(document, source_path=ROOT / "inventory.json")
        services = resolution.document["services"]
        self.assertEqual(services["store-api-reader"]["type"], "store-api")
        self.assertEqual(services["store-api-reader"]["machine"], "resolver")
        self.assertEqual(services["store-api-reader"]["configuration"]["mode"], "read_only")
        self.assertEqual(services["resolver-api"]["connections"]["store_api"], {"service": "store-api-reader"})
        self.assertEqual(services["minter-api"]["connections"]["store_api"], {"service": "store-api"})

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
