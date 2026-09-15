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
from deployment_v3.operations_tui import action_choices, service_detail
from deployment_v3.inventory_resolver import OperatorInventoryError, resolve_inventory_path
from deployment_v3.executor import CommandResult, LocalExecutor
from deployment_v3 import runner as runner_module
from deployment_v3 import cli as cli_module
from deployment_v3.secrets import initialize_greenfield_secrets
from deployment_v3.services import compose_document


ROOT = Path(__file__).resolve().parents[1]


class DeploymentV3Tests(unittest.TestCase):
    def _prepare_lifecycle_bundle(self, plan, project_root: Path, service_id: str) -> Path:
        root = project_root / ".generated" / "deployment-v3" / plan.deployment_id
        render_plan(plan, root / "bundle")
        effective = runner_module._effective_plan(plan, project_root, root)
        service = effective.service(service_id)
        machine = effective.machine(service.machine_id)
        compose = runner_module._compose_directory(effective, machine, root, service_id) / "compose.yaml"
        if machine.execution == "local":
            compose.parent.mkdir(parents=True, exist_ok=True)
            compose.write_text("services: {}\n")
        return root

    class _RecordingLocalExecutor(LocalExecutor):
        def __init__(self):
            self.calls = []

        def run(self, argv, *, timeout=30.0):
            command = tuple(argv)
            self.calls.append((command, timeout))
            stdout = "container-id\n" if command[:3] == ("docker", "ps", "-aq") else ""
            return CommandResult(command, 0, stdout, "")

        def stream(self, argv):
            self.calls.append((tuple(argv), "stream"))
            return 0

    class _RecordingRemoteExecutor:
        def __init__(self):
            self.calls = []

        def run(self, argv, *, timeout=30.0):
            command = tuple(argv)
            self.calls.append((command, timeout))
            stdout = "container-id\n" if command[:3] == ("docker", "ps", "-aq") else ""
            return CommandResult(command, 0, stdout, "")

        def stream(self, argv):
            self.calls.append((tuple(argv), "stream"))
            return 0

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

    def test_dashboard_uses_managed_runtime_mounts_shared_with_migration(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-five-host.json")
        machine = plan.machine("apps")
        group = next(item for item in plan.groups if item.id == "apps")
        services = tuple(plan.service(identifier) for identifier in group.service_ids)
        spec = compose_document(plan, machine, services, "apps")
        managed_runtime = {
            f"{machine.data_root}/{plan.deployment_id}/dashboard/vendor:/var/www/app/vendor",
            f"{machine.data_root}/{plan.deployment_id}/dashboard/storage:/var/www/app/storage",
            f"{machine.data_root}/{plan.deployment_id}/dashboard/bootstrap-cache:/var/www/app/bootstrap/cache",
        }
        for service_id in ("dashboard", "dashboard-migrate"):
            volumes = spec["services"][service_id]["volumes"]
            self.assertTrue(managed_runtime.issubset(volumes))
        migration = spec["services"]["dashboard-migrate"]
        self.assertIn("php artisan optimize:clear", migration["command"][-1])

    def test_multihost_storage_renders_api_bootstrap_and_announced_p2p_endpoints(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-five-host.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            kubo = (output / "machines" / "storage-2" / "groups" / "storage-2" / "env" / "ipfs-storage-b.env").read_text()
            cluster = (output / "machines" / "storage-2" / "groups" / "storage-2" / "env" / "cluster-storage-b.env").read_text()
        self.assertIn("IPFS_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.12/tcp/5001@4001", kubo)
        self.assertIn('IPFS_ANNOUNCE_MULTIADDRESSES=["/ip4/192.168.105.13/tcp/4001"]', kubo)
        self.assertIn("CLUSTER_BOOTSTRAP_ENDPOINTS=/ip4/192.168.105.12/tcp/9094@9096", cluster)
        self.assertIn('CLUSTER_ANNOUNCE_MULTIADDRESSES=["/ip4/192.168.105.13/tcp/9096"]', cluster)

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
        document.pop("proxies")
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

    def test_kubo_entrypoint_consumes_rendered_bootstrap_variable(self):
        entrypoint = (ROOT / "components" / "dark-ipfs" / "scripts" / "ipfs-entrypoint.sh").read_text()
        self.assertIn('BOOTSTRAP_ENDPOINTS="${IPFS_BOOTSTRAP_ENDPOINTS:-${IPFS_BOOTSTRAP_API_MULTIADDRESSES:-${IPFS_BOOTSTRAP_MULTIADDRESSES:-}}}"', entrypoint)

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

    def test_validator_groups_reject_application_services(self):
        source = ROOT / "examples" / "deployment-v3" / "local-ha.json"
        document = json.loads(source.read_text())
        document["groups"]["blockchain-a"]["services"].append("explorer")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "validators may contain only besu-validator"):
                build_plan(path)

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
        for name, services in (("local-simple", 22), ("local-observer", 24), ("local-ha", 24), ("production-five-host", 24)):
            source = ROOT / "examples" / "operator-inventory" / f"{name}.json"
            resolution = resolve_inventory_path(source)
            self.assertEqual(len(resolution.document["services"]), services)
            self.assertEqual(resolution.metadata["catalog"], "dark-platform-baseline-v1.0")
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
        self.assertIn("0.0.0.0:80:80/tcp", compose)

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
        original = readiness._run
        try:
            readiness._run = lambda _plan, _machine, _root, service_id, shell: calls.append((service_id, shell)) or type("Result", (), {"returncode": 0})()
            ok, evidence = readiness.check_phase(plan, ROOT, "data")
        finally:
            readiness._run = original
        self.assertTrue(ok)
        self.assertEqual(evidence, "Store API health is ready")
        self.assertEqual(calls[0][0], "store-api")
        self.assertIn("http://127.0.0.1:8003/health", calls[0][1])

    def test_rpc_readiness_probes_local_rpc_over_loopback(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        calls = []
        original_curl = readiness._curl
        try:
            readiness._curl = lambda _plan, _machine, url, payload=None: calls.append((url, payload)) or type("Result", (), {"returncode": 0, "stdout": '{"result":"0x4"}', "stderr": ""})()
            ok, evidence = readiness.check_phase(plan, ROOT, "rpc")
        finally:
            readiness._curl = original_curl
        self.assertTrue(ok)
        self.assertEqual(evidence, "RPC peers=4; expected at least 4")
        self.assertEqual(calls[0][0], "http://127.0.0.1:8545")
        self.assertIn('"method":"net_peerCount"', calls[0][1])

    def test_rpc_readiness_does_not_wait_for_later_observers(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-six-host.json")
        original_curl = readiness._curl
        try:
            readiness._curl = lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": '{"result":"0x4"}', "stderr": ""})()
            ok, evidence = readiness._rpc(plan, ROOT)
        finally:
            readiness._curl = original_curl
        self.assertTrue(ok)
        self.assertEqual(evidence, "RPC peers=4; expected at least 4")

    def test_observer_readiness_checks_sync_and_validator_exclusion(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "local-ha.json").read_text())
        document["blockchain"]["validator_groups"] = {"blockchain-a": {"validator_count": 1}}
        document["blockchain"]["observer_groups"] = {"observers": {"observer_count": 1}}
        document["placement"]["observers"] = "local"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        observer = plan.service("observer01")
        responses = iter(("0x2", "0xa", "0x2222222222222222222222222222222222222222", ["0x1111111111111111111111111111111111111111"]))
        result = lambda value: type("Result", (), {"returncode": 0, "stdout": json.dumps({"result": value}), "stderr": ""})()
        with patch.object(readiness, "_observer_rpc", side_effect=lambda *_args, **_kwargs: result(next(responses))), patch.object(readiness, "_curl", return_value=result("0xb")):
            ok, evidence, details = readiness.observer_status(plan, ROOT, observer)
        self.assertTrue(ok, evidence)
        self.assertEqual(details["lag"], 1)

    def test_local_observer_template_renders_private_rpc_without_host_publish(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-observer.json")
        observer = plan.service("observer01")
        self.assertEqual(plan.service("resolver-api").connections["rpc"]["service"], observer.id)
        spec = compose_document(plan, plan.machine("local"), tuple(plan.services))
        command = spec["services"][observer.id]["command"]
        self.assertIn("--rpc-http-enabled=true", command)
        self.assertEqual(spec["services"][observer.id]["ports"], [])

    def test_production_six_host_places_resolver_on_local_observer_rpc(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-six-host.json")
        observer = plan.service("observer01")
        resolver = plan.service("resolver-api")
        proxy = plan.service("resolver-public")

        self.assertEqual(observer.machine_id, "resolver")
        self.assertEqual(resolver.machine_id, "resolver")
        self.assertEqual(resolver.connections["rpc"], {"service": "observer01"})
        self.assertEqual(proxy.connections["arks"], {"service": "resolver-api"})
        self.assertEqual(proxy.configuration["sites"][0]["routes"][0]["path"], "/")

        spec = compose_document(plan, plan.machine("resolver"), tuple(plan.services))
        self.assertIn("--rpc-http-enabled=true", spec["services"][observer.id]["command"])
        observer_ports = spec["services"][observer.id]["ports"]
        self.assertEqual(len(observer_ports), 2)
        self.assertTrue(any(port.endswith("/tcp") for port in observer_ports))
        self.assertTrue(any(port.endswith("/udp") for port in observer_ports))
        self.assertFalse(any(":8545:" in port for port in observer_ports))

    def test_production_six_host_places_resolver_on_local_store_api(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-six-host.json")
        resolver = plan.service("resolver-api")
        store = plan.service("store-api-reader")
        self.assertEqual(store.machine_id, resolver.machine_id)
        self.assertEqual(resolver.connections["store_api"], {"service": store.id})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            env = (output / "machines" / "resolver" / "groups" / "resolver-observer" / "env" / "resolver-api.env").read_text()
            reader_env = (output / "machines" / "resolver" / "groups" / "resolver-observer" / "env" / "store-api-reader.env").read_text()
            storage = json.loads((output / "machines" / "resolver" / "groups" / "resolver-observer" / "config" / "storage-endpoints.json").read_text())
        self.assertIn("METADATA_STORE_API_URL=http://store-api-reader:8003\n", env)
        self.assertIn("STORE_API_MODE=read_only\n", reader_env)
        self.assertEqual({node["id"] for node in storage["nodes"]}, {"storage-a", "storage-b"})

    def test_data_readiness_probes_unexposed_store_from_its_container(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "lima-five-host.json")
        calls = []
        original_run = readiness._run
        try:
            readiness._run = lambda _plan, _machine, _root, service_id, shell: calls.append((service_id, shell)) or type("Result", (), {"returncode": 0})()
            ok, evidence = readiness.check_phase(plan, ROOT, "data")
        finally:
            readiness._run = original_run
        self.assertTrue(ok)
        self.assertEqual(evidence, "Store API health is ready")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "store-api")
        self.assertIn("http://127.0.0.1:8003/health", calls[0][1])

    def test_verification_probes_store_health_not_root(self):
        plan = build_plan(ROOT / "examples" / "deployment-v3" / "local-ha.json")
        store = plan.service("store-api")
        self.assertEqual(verify_module._store_health_url(store, plan.machine("local")), "http://127.0.0.1:8003/health?refresh=true")

    def test_compose_states_accepts_json_list(self):
        output = json.dumps([{"Service": "api", "State": "running"}])
        self.assertEqual(verify_module._compose_states(output), {"api": "running"})

    def test_compose_states_accepts_json_lines(self):
        output = '{"Service":"api","State":"running"}\n{"Service":"worker","State":"exited"}\n'
        self.assertEqual(verify_module._compose_states(output), {"api": "running", "worker": "exited"})

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

    def test_proxy_uses_container_listener_and_public_origin(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-five-host.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            nginx = (output / "machines" / "apps" / "groups" / "apps" / "config" / "nginx-gateway.conf").read_text()
            compose = (output / "machines" / "apps" / "groups" / "apps" / "compose.yaml").read_text()
            dashboard_env = (output / "machines" / "apps" / "groups" / "apps" / "env" / "dashboard.env").read_text()
        self.assertIn("listen 80;", nginx)
        self.assertIn("0.0.0.0:80:80/tcp", compose)
        self.assertIn("APP_URL=https://dark.example.org/admin", dashboard_env)
        self.assertIn("ASSET_URL=https://dark.example.org/admin", dashboard_env)
        self.assertIn("SESSION_PATH=/\n", dashboard_env)
        self.assertIn("proxy_redirect ~^/(?!admin(?:/|$))(.*)$ /admin/$1;", nginx)
        self.assertIn("proxy_redirect ~^https?://[^/]+/(?!admin(?:/|$))(.*)$ /admin/$1;", nginx)
        self.assertIn("proxy_cookie_path / /admin/;", nginx)
        self.assertNotIn("sub_filter", nginx)

    def test_dashboard_mount_is_derived_without_route_specific_proxy_code(self):
        document = json.loads((ROOT / "examples" / "deployment-v3" / "local-simple.json").read_text())
        routes = document["services"]["edge-proxy"]["configuration"]["sites"][0]["routes"]
        next(route for route in routes if route["id"] == "dashboard")["path"] = "/control/"
        with tempfile.TemporaryDirectory() as temporary:
            inventory = Path(temporary) / "inventory.json"
            inventory.write_text(json.dumps(document))
            plan = build_plan(inventory)
            output = Path(temporary) / "bundle"
            render_plan(plan, output)
            nginx = (output / "machines" / "local" / "groups" / "apps" / "config" / "nginx-edge-proxy.conf").read_text()
            dashboard_env = (output / "machines" / "local" / "groups" / "apps" / "env" / "dashboard.env").read_text()
        self.assertIn("APP_URL=http://localhost/control", dashboard_env)
        self.assertIn("ASSET_URL=http://localhost/control", dashboard_env)
        self.assertIn("SESSION_PATH=/\n", dashboard_env)
        self.assertIn("proxy_redirect ~^/(?!control(?:/|$))(.*)$ /control/$1;", nginx)
        self.assertIn("proxy_cookie_path / /control/;", nginx)
        self.assertNotIn("dashboard/", nginx)

    def test_rejects_two_proxies_on_one_machine(self):
        document = json.loads((ROOT / "examples" / "deployment-v3" / "local-ha.json").read_text())
        duplicate = json.loads(json.dumps(document["services"]["edge-proxy"]))
        duplicate["connections"] = {"resolver": duplicate["connections"]["resolver"]}
        duplicate["configuration"]["sites"][0]["routes"] = [
            {"id": "second-resolver", "path": "/second/", "connection": "resolver", "upstream_path": "/api/v1/arks/"}
        ]
        document["services"]["second-proxy"] = duplicate
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "two-proxies.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "may run only one edge-proxy"):
                build_plan(path)

    def test_legacy_access_gateway_remains_supported(self):
        document = json.loads((ROOT / "examples" / "operator-inventory" / "production-five-host.json").read_text())
        document.pop("proxies")
        document["access"] = {"mode": "gateway", "bind": "public", "port": 80}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy-access.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        self.assertEqual(len([service for service in plan.services if service.type == "edge-proxy"]), 1)

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

    def test_service_recreate_build_uses_exact_labels_and_compose_project(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            root = self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.manage_service(plan, project_root, "recreate", "service:store-api", build=True)
            status = json.loads((root / "status.json").read_text())
        self.assertEqual(result["project"], "dark-operator-local-ha-local-apps")
        labels = next(command for command, _ in executor.calls if command[:3] == ("docker", "ps", "-aq"))
        self.assertIn("label=com.docker.compose.project=dark-operator-local-ha-local-apps", labels)
        self.assertIn("label=com.docker.compose.service=store-api", labels)
        recreate = executor.calls[-1][0]
        self.assertIn("up", recreate)
        self.assertIn("--force-recreate", recreate)
        self.assertIn("--build", recreate)
        self.assertEqual(status["services"]["store-api"], "applied")

    def test_service_build_uses_the_resolved_compose_service_without_recreating_it(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.manage_service(plan, project_root, "build", "service:store-api")
        self.assertEqual(result["action"], "build")
        command = executor.calls[-1][0]
        self.assertEqual(command[-2:], ("build", "store-api"))

    def test_service_lifecycle_targets_the_resolved_remote_owner(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-six-host.json")
        executor = self._RecordingRemoteExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api-reader")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.manage_service(plan, project_root, "restart", "service:store-api-reader")
        self.assertEqual(result["machine"], "resolver")
        self.assertEqual(result["group"], "resolver-observer")
        self.assertEqual(result["project"], "dark-operator-production-six-resolver-resolver-observer")
        self.assertTrue(any(command[-2:] == ("restart", "store-api-reader") for command, _ in executor.calls))

    def test_service_lifecycle_rejects_ambiguous_or_one_shot_targets(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "contracts-deploy")
            with self.assertRaisesRegex(runner_module.ApplyError, "target must use the exact form"):
                runner_module.manage_service(plan, project_root, "stop", "store-api")
            with self.assertRaisesRegex(runner_module.ApplyError, "one-shot"):
                runner_module.manage_service(plan, project_root, "stop", "service:contracts-deploy")

    def test_service_lifecycle_reports_unknown_target_cleanly(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "one-server-aws-sandbox.json")
        with tempfile.TemporaryDirectory() as temporary:
            self._prepare_lifecycle_bundle(plan, Path(temporary), "gateway")
            with self.assertRaisesRegex(runner_module.ApplyError, "target service is not declared"):
                runner_module.follow_service_logs(plan, Path(temporary), "service:edge-proxy")

    def test_service_lifecycle_dry_run_does_not_touch_docker(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            root = self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.manage_service(plan, project_root, "stop", "service:store-api", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(executor.calls, [])
        self.assertFalse((root / "status.json").exists())

    def test_service_logs_follow_the_resolved_compose_service(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.follow_service_logs(plan, project_root, "service:store-api", tail=25)
        self.assertEqual(result["service"], "store-api")
        self.assertEqual(result["tail"], 25)
        command, timeout = executor.calls[-1]
        self.assertEqual(timeout, "stream")
        self.assertEqual(command[-5:], ("logs", "--follow", "--tail", "25", "store-api"))

    def test_operations_console_helpers_keep_actions_and_runtime_context(self):
        row = {
            "service": "store-api", "type": "store-api", "state": "running", "machine": "local",
            "group": "apps", "description": "Store API", "detail": "Up", "actions": ["build", "restart", "recreate"],
        }
        self.assertEqual(action_choices(row), ("build", "restart", "recreate", "recreate-build", "logs"))
        self.assertIn("Machine: local", service_detail(row))
        self.assertNotIn("Actions:", service_detail(row))

    def test_service_logs_accept_one_shot_service_output(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "contracts-deploy")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.follow_service_logs(plan, project_root, "service:contracts-deploy")
        self.assertEqual(result["service"], "contracts-deploy")

    def test_service_lifecycle_uses_deployed_snapshot_when_working_plan_changes(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        changed = json.loads(json.dumps(plan.raw))
        changed["settings"]["minter"]["shoulder"] = "201"
        changed_plan = runner_module.build_plan_document(changed, ROOT / "changed.json")
        executor = self._RecordingLocalExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                result = runner_module.manage_service(changed_plan, project_root, "stop", "service:store-api")
        self.assertEqual(result["deployment_id"], plan.deployment_id)
        self.assertTrue(any(command[-2:] == ("stop", "store-api") for command, _ in executor.calls))

    def test_legacy_dashboard_redis_snapshot_remains_operable_for_removal(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        legacy = json.loads(json.dumps(plan.raw))
        legacy["images"]["redis"] = "redis:7-alpine"
        legacy["services"]["dashboard-redis"] = {"type": "dashboard-redis", "machine": "local"}
        legacy["services"]["dashboard"]["connections"]["redis"] = {"service": "dashboard-redis"}
        legacy["services"]["dashboard-migrate"]["connections"]["redis"] = {"service": "dashboard-redis"}
        legacy["groups"]["apps"]["services"].append("dashboard-redis")
        restored = runner_module.build_plan_document(legacy, ROOT / "legacy-snapshot.json")
        self.assertEqual(restored.service("dashboard-redis").type, "dashboard-redis")

    def test_service_listing_reads_runtime_labels_and_exposes_exact_actions(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-six-host.json")

        class RuntimeExecutor(self._RecordingRemoteExecutor):
            def run(self, argv, *, timeout=30.0):
                command = tuple(argv)
                self.calls.append((command, timeout))
                project = command[4] if len(command) > 4 and command[:3] == ("docker", "ps", "-a") else ""
                if project.endswith("-resolver-resolver-observer"):
                    return CommandResult(command, 0, "store-api-reader\trunning\tUp 10 seconds\nresolver-api\texited\tExited (1)\n", "")
                return CommandResult(command, 0, "", "")

        executor = RuntimeExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api-reader")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                rows = runner_module.list_managed_services(plan, project_root)
        by_service = {row["service"]: row for row in rows}
        store = by_service["store-api-reader"]
        resolver = by_service["resolver-api"]
        self.assertEqual(store["state"], "running")
        self.assertEqual(store["description"], "Store API de lectura local para resolver-api")
        self.assertEqual(store["actions"], ["build", "stop", "restart", "recreate", "remove"])
        self.assertEqual(resolver["state"], "exited")
        self.assertEqual(by_service["minter-api"]["state"], "missing")
        self.assertEqual(by_service["contracts-deploy"]["actions"], ["build"])
        self.assertEqual(store["deployment_id"], plan.deployment_id)

    def test_managed_deployment_listing_summarizes_runtime_state(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        class RuntimeExecutor(self._RecordingLocalExecutor):
            def run(self, argv, *, timeout=30.0):
                command = tuple(argv)
                self.calls.append((command, timeout))
                if command[:3] == ("docker", "ps", "-a"):
                    return CommandResult(command, 0, "store-api\trunning\tUp 10 seconds\n", "")
                return super().run(argv, timeout=timeout)

        executor = RuntimeExecutor()
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            self._prepare_lifecycle_bundle(plan, project_root, "store-api")
            with patch.object(runner_module, "resolve_executor", return_value=executor):
                deployments = runner_module.list_managed_deployments(project_root)
        self.assertEqual(deployments[0]["deployment_id"], plan.deployment_id)
        self.assertEqual(deployments[0]["state"], "degraded")

    def test_compose_labels_identify_managed_deployment_and_service(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        spec = compose_document(plan, plan.machine("local"), tuple(plan.services))
        self.assertEqual(spec["services"]["store-api"]["labels"], {
            "org.dark.deployment.id": plan.deployment_id,
            "org.dark.service.id": "store-api",
        })


if __name__ == "__main__": unittest.main()
