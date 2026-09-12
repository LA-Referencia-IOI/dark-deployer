"""Coverage for explicit-placement deployment inventory v3."""

from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from deployment_v3.inventory import InventoryError, load_inventory
from deployment_v3.artifacts import chain_context
from deployment_v3 import readiness
from deployment_v3 import verify as verify_module
from deployment_v3.planner import build_plan
from deployment_v3.render import render_plan
from deployment_v3.inventory_editor.textual_app import SECTIONS, SECTION_HELP
from deployment_v3.inventory_resolver import OperatorInventoryError, resolve_inventory_path
from deployment_v3.executor import CommandResult
from deployment_v3 import runner as runner_module
from deployment_v3 import cli as cli_module
from deployment_v3.secrets import initialize_greenfield_secrets
from deployment_v3.services import compose_document


ROOT = Path(__file__).resolve().parents[1]


class DeploymentV3Tests(unittest.TestCase):
    def test_contract_artifacts_are_mounted_from_the_rendered_apps_group(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        service = next(item for item in plan.services if item.type == "contracts-deploy")
        group = next(item for item in plan.groups if item.id == "apps")
        services = tuple(plan.service(identifier) for identifier in group.service_ids)
        spec = compose_document(plan, plan.machine(service.machine_id), services, "apps")
        self.assertIn("./artifacts/contracts:/contracts:ro", spec["services"][service.id]["volumes"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            render_plan(plan, bundle)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            for name in ("AuthorityABI.json", "AuthorityBytecode.txt", "dARKABI.json", "dARKBytecode.txt"):
                (artifacts / name).write_text(name)
            runner_module._attach_contract_artifacts(plan, bundle, artifacts)
            copied = bundle / "machines" / service.machine_id / "groups" / "apps" / "artifacts" / "contracts"
            self.assertEqual(sorted(item.name for item in copied.iterdir()), sorted(item.name for item in artifacts.iterdir()))

    def test_multihost_storage_renders_api_bootstrap_and_announced_p2p_endpoints(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-five-host.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            kubo = (output / "machines" / "storage-2" / "groups" / "storage-2" / "env" / "ipfs-storage-b.env").read_text()
            cluster = (output / "machines" / "storage-2" / "groups" / "storage-2" / "env" / "cluster-storage-b.env").read_text()
        self.assertIn("IPFS_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.6/tcp/5001@4001", kubo)
        self.assertIn("IPFS_ANNOUNCE_MULTIADDRESS=/ip4/192.168.105.7/tcp/4001", kubo)
        self.assertIn("CLUSTER_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.6/tcp/9094@9096", cluster)
        self.assertIn("CLUSTER_ANNOUNCE_MULTIADDRESS=/ip4/192.168.105.7/tcp/9096", cluster)

    def test_private_endpoint_can_advertise_a_nat_address_and_port(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["services"]["rpc01"]["exposure"].update({"advertise_address": "203.0.113.10", "advertise_port": 18545})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nat.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        endpoint = next(item for item in plan.endpoints if item.service == "rpc01" and item.host == "203.0.113.10")
        self.assertEqual(endpoint.url, "http://203.0.113.10:18545")

    def test_operator_supports_routes_nat_and_p2p_advertisement(self):
        source = ROOT / "examples" / "operator-inventory" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["access"] = {"mode": "none"}
        document["machines"]["observer"] = {
            "execution": "ssh", "management_address": "192.0.2.20", "addresses": {"lan": "192.0.2.20"},
        }
        document["placement"]["observer"] = "observer"
        document["overrides"]["explorer"] = {"group": "observer"}
        document["routing"]["application_api"] = "vpn"
        document["routes"] = [{"from": "lan", "to": "vpn", "via": "site-vpn"}]
        document["networking"] = {"services": {
            "rpc01": {"advertise_address": "203.0.113.10", "advertise_port": 18545},
            "ipfs-storage-a": {"p2p_advertise_port": 4101},
        }}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "routed-operator.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            compose = (output / "machines" / "storage-1" / "groups" / "storage-1" / "compose.yaml").read_text()
        endpoint = next(item for item in plan.endpoints if item.service == "rpc01" and item.host == "203.0.113.10")
        self.assertEqual(endpoint.url, "http://203.0.113.10:18545")
        self.assertIn("198.51.100.13:4101:4001/tcp", compose)

    def test_operator_accepts_cidrs_for_automatic_local_address(self):
        source = ROOT / "examples" / "operator-inventory" / "local-simple.json"
        document = json.loads(source.read_text())
        document["networks"]["lab"] = {"kind": "lan", "cidrs": ["10.99.0.0/24", "10.100.0.0/24"]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local-cidrs.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        self.assertEqual(plan.machine("local").address_on("lab"), "10.99.0.1")

    def test_operator_rejects_unknown_override(self):
        source = ROOT / "examples" / "operator-inventory" / "local-simple.json"
        document = json.loads(source.read_text())
        document["overrides"]["servcies"] = {}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "typo.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(OperatorInventoryError, "unsupported fields: servcies"):
                resolve_inventory_path(path)

    def test_service_fingerprint_tracks_mounted_cluster_entrypoint(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-five-host.json")
        service = plan.service("cluster-storage-a")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            project = Path(temporary) / "project"
            entrypoint = project / "components" / "dark-ipfs" / "scripts" / "cluster-entrypoint.sh"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("first")
            render_plan(plan, bundle)
            first = runner_module._service_fingerprint(plan, service, bundle, project)
            entrypoint.write_text("second")
            second = runner_module._service_fingerprint(plan, service, bundle, project)
        self.assertNotEqual(first, second)

    def test_managed_private_inputs_attach_sources_without_leaking_key_to_state(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            private_root = Path(temporary)
            secret_root = private_root / "secrets"
            artifact_root = private_root / "chain-artifact"
            wallet = private_root / "master-wallet.txt"
            private_key = "0x" + "1" * 64
            wallet.write_text(
                "Address: 0x" + "2" * 40 + "\nPrivate Key: " + private_key + "\n"
            )
            wallet.chmod(0o600)
            initialize_greenfield_secrets(plan, secret_root)
            effective = cli_module._private_plan(
                plan,
                secret_root=secret_root,
                wallet_file=wallet,
                signer_file=wallet,
                artifact_root=artifact_root,
            )
            for definition in effective.raw["secrets"].values():
                if definition.get("consumers"):
                    self.assertIn("source", definition)
                    self.assertTrue(Path(definition["source"]).is_file())
            runtime = secret_root / effective.raw["secrets"]["minter-runtime-env"]["path"]
            self.assertIn("DARK_ADMIN_PRIVATE_KEY=" + private_key, runtime.read_text())
            cli_module._write_private_input_state(
                private_root,
                wallet=wallet,
                signer=wallet,
                secrets=secret_root,
                artifact=artifact_root,
            )
            state_text = (private_root / "private-inputs.json").read_text()
            self.assertNotIn(private_key, state_text)
            self.assertEqual((private_root / "private-inputs.json").stat().st_mode & 0o777, 0o600)
            state = cli_module._load_private_input_state(private_root / "private-inputs.json")
            self.assertEqual(Path(state["master_wallet_source"]), wallet)
            wallet.write_text("0x" + "3" * 64 + "\n")
            with self.assertRaisesRegex(cli_module.ApplyError, "changed since the previous run"):
                cli_module._load_private_input_state(private_root / "private-inputs.json")

    def test_empty_overlapping_network_can_be_removed_only_when_requested(self):
        class FakeExecutor:
            def __init__(self):
                self.create_attempts = 0
                self.removed = []

            def run(self, argv, *, timeout=30):
                command = tuple(argv)
                if command[:3] == ("docker", "network", "ls"):
                    return CommandResult(command, 0, "stale-local\n", "")
                if command[:3] == ("docker", "network", "inspect") and command[3] == "new-local":
                    return CommandResult(command, 1, "", "not found")
                if command[:3] == ("docker", "network", "inspect") and command[3] == "stale-local":
                    if command[-1] == "{{len .Containers}}":
                        return CommandResult(command, 0, "0\n", "")
                    return CommandResult(command, 0, "172.30.0.0/24\n", "")
                if command[:3] == ("docker", "network", "create"):
                    self.create_attempts += 1
                    return CommandResult(command, 1 if self.create_attempts == 1 else 0, "", "pool overlaps")
                if command[:3] == ("docker", "network", "rm"):
                    self.removed.append(command[3])
                    return CommandResult(command, 0, command[3], "")
                raise AssertionError(command)

        executor = FakeExecutor()
        plan = SimpleNamespace(deployment_id="new", docker_subnets={"local": "172.30.0.0/24"})
        runner_module._ensure_machine_network(
            plan,
            SimpleNamespace(id="local"),
            executor,
            clean_empty_conflicts=True,
            prompt_cleanup_empty_conflicts=False,
        )
        self.assertEqual(executor.removed, ["stale-local"])
        self.assertEqual(executor.create_attempts, 2)

        prompted = FakeExecutor()
        with patch("builtins.input", return_value="y"):
            runner_module._ensure_machine_network(
                plan,
                SimpleNamespace(id="local"),
                prompted,
                clean_empty_conflicts=False,
                prompt_cleanup_empty_conflicts=True,
            )
        self.assertEqual(prompted.removed, ["stale-local"])

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

    def test_edge_proxy_publishes_nginx_port_80(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-five-host.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            compose = (output / "machines" / "apps" / "groups" / "apps" / "compose.yaml").read_text()
        self.assertIn("0.0.0.0:8080:80/tcp", compose)

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

    def test_multi_machine_chain_uses_host_p2p_even_with_local_execution(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        for machine in document["machines"].values():
            machine["execution"] = "local"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "multi-local.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        context = chain_context(plan, "0x" + "1" * 40)
        self.assertEqual(context["nodes"]["validator01"]["private_address"], "198.51.100.11")
        self.assertEqual(context["nodes"]["validator01"]["p2p_port"], 30303)
        self.assertEqual(context["nodes"]["rpc01"]["p2p_port"], 30307)

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

    def test_multihost_plan_includes_network_preflight_before_services(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "production-five-host.json")
        names = [step.id for step in plan.steps]
        self.assertIn("network-preflight", names)
        self.assertLess(names.index("network-preflight"), names.index("apply:validator01"))

    def test_logical_network_accepts_multiple_routed_cidrs(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["networks"]["vpn"].pop("cidr")
        document["networks"]["vpn"]["cidrs"] = ["198.51.100.0/24", "203.0.113.0/24"]
        document["machines"]["storage-2"]["addresses"]["vpn"] = "203.0.113.14"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "routed.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        self.assertEqual(plan.machine("storage-2").address_on("vpn"), "203.0.113.14")

    def test_connection_can_use_declared_route_to_provider_network(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["machines"]["observer"] = {
            "execution": "ssh", "management_address": "192.0.2.20", "addresses": {"lan": "192.0.2.20"},
        }
        document["groups"]["observer"] = {"kind": "apps", "machine": "observer", "members": ["explorer"]}
        document["services"].pop("edge-proxy")
        document["services"]["explorer"]["machine"] = "observer"
        document["services"]["explorer"]["connections"]["rpc"]["network"] = "vpn"
        document["services"]["rpc01"]["exposure"]["network"] = "vpn"
        document["routes"] = [{"from": "lan", "to": "vpn", "via": "site-vpn"}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "routed.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        endpoint = next(item for item in plan.endpoints if item.service == "rpc01" and item.host == "198.51.100.10")
        self.assertEqual(endpoint.network, "vpn")

    def test_connection_without_shared_or_declared_route_is_rejected(self):
        source = ROOT / "examples" / "deployment-v3" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["machines"]["observer"] = {
            "execution": "ssh", "management_address": "192.0.2.20", "addresses": {"lan": "192.0.2.20"},
        }
        document["groups"]["observer"] = {"kind": "apps", "machine": "observer", "members": ["explorer"]}
        document["services"].pop("edge-proxy")
        document["services"]["explorer"]["machine"] = "observer"
        document["services"]["explorer"]["connections"]["rpc"]["network"] = "vpn"
        document["services"]["rpc01"]["exposure"]["network"] = "vpn"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unrouted.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "neither shared nor routed"):
                build_plan(path)


if __name__ == "__main__": unittest.main()
