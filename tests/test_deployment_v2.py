import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from deployment_v2.inventory import InventoryError, load_inventory
from deployment_v2.artifacts import ArtifactError, export_chain_role, static_nodes, verify_artifact_manifest, write_artifact_manifest, write_chain_bootstrap
from deployment_v2.secrets import SecretError, initialize_greenfield_secrets
from deployment_v2.executor import CommandResult, SshExecutor, run_preflight
from deployment_v2.model import Machine, SshSettings
from deployment_v2.planner import build_plan
from deployment_v2.render import render_plan
from deployment_v2.runner import apply, push
from deployment_v2.runner import ApplyError, _copy_if_absent_or_identical, _ignore_public_source, _wait_for_rpc
from deployment_v2.state import deployment_lock, run_root, write_status
from deployment_v2.verify import verify
from deployment_v2.sources import SourceError, require_matching_source_evidence, source_evidence
from deployment_v2.cli import _write_install_report


ROOT = Path(__file__).resolve().parents[1]


class DeploymentV2Tests(unittest.TestCase):
    def test_examples_validate_and_plan(self):
        for name, machines, groups in (("local-simple", 1, 4), ("local-ha", 1, 5), ("production-five-host", 5, 5)):
            path = ROOT / "examples" / "deployment-v2" / f"{name}.json"
            _, loaded_machines, loaded_groups, _ = load_inventory(path)
            self.assertEqual(len(loaded_machines), machines)
            self.assertEqual(len(loaded_groups), groups)
            plan = build_plan(path)
            self.assertEqual(plan.deployment_id, json.loads(path.read_text())["deployment"]["id"])
            self.assertEqual(len(plan.docker_subnets), machines)
            self.assertTrue(any(step.id == "verify:deployment" for step in plan.steps))

    def test_install_report_is_public_and_records_verification_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            report = _write_install_report(
                root,
                {"deployment_id": "local-test", "ok": True, "groups": {"apps": {"ok": True}}},
                resumed=False,
            )
            document = json.loads(report.read_text())
            self.assertEqual(document["state"], "verified")
            self.assertFalse(document["resumed"])
            self.assertTrue(document["verification"]["ok"])
            self.assertNotIn("Private Key", report.read_text())
            self.assertNotIn("DARK_ADMIN_PRIVATE_KEY", report.read_text())

    def test_install_dry_run_does_not_create_runtime_state(self):
        from deployment_v2.cli import _install

        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-simple.json")
        args = mock.Mock(dry_run=True, yes=False, non_interactive=True, resume=False)
        with mock.patch("builtins.print") as output:
            _install(args, plan)
        text = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Deployment: dark-local-simple", text)
        self.assertIn("apply:apps", text)
        self.assertFalse((ROOT / ".generated" / "deployment-v2" / "dark-local-simple" / "install-report.json").exists())

    def test_auto_machine_is_resolved_before_networks_are_derived(self):
        document = json.loads((ROOT / "examples" / "deployment-v2" / "local-simple.json").read_text())
        document["machines"]["local"]["execution"] = "auto"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "auto.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
        self.assertEqual(plan.machine("local").execution, "local")
        self.assertEqual(plan.endpoints[0].transport, "docker")

    def test_rejects_duplicate_private_address_and_bad_replication(self):
        source = ROOT / "examples" / "deployment-v2" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["machines"]["storage-2"]["addresses"]["vpn"] = "10.20.30.31"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "duplicates private address"):
                load_inventory(path)
            document["machines"]["storage-2"]["addresses"]["vpn"] = "10.20.30.32"
            document["storage"]["replication"]["target_replicas"] = 3
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "target_replicas"):
                load_inventory(path)

    def test_rejects_unknown_nested_fields(self):
        source = ROOT / "examples" / "deployment-v2" / "local-ha.json"
        document = json.loads(source.read_text())
        document["storage"]["legacy_endpoint_list"] = ["http://old.example"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "storage contains unknown"):
                load_inventory(path)

    def test_rejects_an_unsafe_component_branch(self):
        source = ROOT / "examples" / "deployment-v2" / "local-ha.json"
        document = json.loads(source.read_text())
        document["components"]["dark-core-lib"]["branch"] = "../unexpected"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "safe Git branch"):
                load_inventory(path)

    def test_rejects_multiple_remote_storage_groups_on_one_machine(self):
        source = ROOT / "examples" / "deployment-v2" / "production-five-host.json"
        document = json.loads(source.read_text())
        document["groups"]["storage-b"]["machine"] = "storage-1"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "only one storage group"):
                load_inventory(path)

    def test_render_produces_public_group_configs_without_secret_values(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = render_plan(plan, Path(temporary) / "bundle")
            self.assertTrue((output / "shared" / "plan.json").exists())
            apps = json.loads((output / "groups" / "apps" / "group.json").read_text())
            self.assertEqual(apps["machine"]["execution"], "local")
            self.assertIn("components", apps)
            apps_compose = yaml.safe_load((output / "groups" / "apps" / "compose.yaml").read_text())
            self.assertIn("minter-chain-worker", apps_compose["services"])
            self.assertIn("pg_isready -U dark -d postgres", apps_compose["services"]["postgres"]["healthcheck"]["test"])
            self.assertEqual(apps_compose["services"]["contracts-deploy"]["build"]["context"], "/srv/dark")
            self.assertEqual(apps_compose["services"]["rpc-probe"]["entrypoint"], ["python"])
            rpc = apps_compose["services"]["blockchain-rpc"]
            self.assertIn("--genesis-file=/config/genesis.json", rpc["command"])
            self.assertEqual(rpc["networks"]["dark-local-ha-local"]["ipv4_address"], "172.30.0.14")
            validators = yaml.safe_load((output / "groups" / "validators-a" / "compose.yaml").read_text())
            self.assertEqual(validators["services"]["validator01"]["networks"]["dark-local-ha-local"]["ipv4_address"], "172.30.0.10")
            self.assertEqual(validators["services"]["explorer"]["environment"]["RPC_HTTP_URL"], "http://blockchain-rpc:8545")
            self.assertEqual(validators["services"]["explorer"]["build"]["dockerfile"], "Dockerfile")
            storage_a = yaml.safe_load((output / "groups" / "storage-a" / "compose.yaml").read_text())
            storage_b = yaml.safe_load((output / "groups" / "storage-b" / "compose.yaml").read_text())
            self.assertNotEqual(storage_a["services"]["cluster"]["ports"][0], storage_b["services"]["cluster"]["ports"][0])
            self.assertEqual(storage_a["services"]["ipfs"]["networks"]["dark-local-ha-local"]["ipv4_address"], "172.30.0.100")
            self.assertEqual(storage_b["services"]["cluster"]["networks"]["dark-local-ha-local"]["ipv4_address"], "172.30.0.103")
            self.assertEqual(storage_a["services"]["ipfs"]["environment"]["IPFS_ANNOUNCE_MULTIADDRESS"], "/ip4/172.30.0.100/tcp/4001")
            endpoints = json.loads((output / "groups" / "apps" / "config" / "storage-endpoints.json").read_text())
            self.assertEqual(endpoints["version"], 1)
            self.assertEqual({node["id"] for node in endpoints["nodes"]}, {"storage-a", "storage-b"})
            minter_env = (output / "groups" / "apps" / "env" / "minter.env").read_text()
            self.assertIn("DARK_RPC_URL=http://blockchain-rpc:8545", minter_env)
            self.assertIn("REPLICATION_TARGET_REPLICAS=2", minter_env)
            self.assertIn("REPLICATION_STATUS_BATCH_SIZE=200", minter_env)
            self.assertIn("METADATA_WORKER_CONCURRENCY=4", minter_env)
            store_env = (output / "groups" / "apps" / "env" / "store-api.env").read_text()
            self.assertIn("STORE_STATUS_CONCURRENCY=6", store_env)
            dashboard_env = (output / "groups" / "apps" / "env" / "dashboard.env").read_text()
            self.assertIn("MINTER_BASE_URL=http://minter-api:8001", dashboard_env)

    def test_preflight_is_read_only_and_reports_each_check(self):
        machine = Machine(
            "local", "local", "127.0.0.1", "10.98.0.10",
            SshSettings("dark", 22, "/tmp/key", None), "/srv/dark", "/srv/dark/data", "/srv/dark/secrets"
        )
        with mock.patch("deployment_v2.executor.LocalExecutor.run", return_value=CommandResult(("test",), 0, "ok\n", "")) as command:
            results = run_preflight(machine)
        self.assertEqual([item["check"] for item in results], ["docker", "compose", "architecture"])
        self.assertEqual(command.call_count, 3)
        self.assertTrue(all(item["ok"] for item in results))

    def test_chain_bootstrap_is_public_and_static_nodes_use_derived_private_addresses(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "production-five-host.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = write_chain_bootstrap(plan, Path(temporary) / "chain", "0x" + "a" * 40)
            context = json.loads((root / "chain-context.json").read_text())
            self.assertEqual(context["chain_id"], 2025)
            self.assertEqual(context["qbft"]["block_period_seconds"], 6)
            self.assertEqual(context["nodes"]["validator01"]["private_address"], "10.20.30.10")
            keys = {node: f"{index:0128x}" for index, node in enumerate(("validator01", "validator02", "validator03", "validator04", "rpc01"), 1)}
            peers = static_nodes(context, keys)
            self.assertEqual(len(peers["validator01"]), 4)
            self.assertTrue(any(peer.endswith("@10.20.30.20:30307") for peer in peers["validator01"]))
            with self.assertRaisesRegex(ArtifactError, "public keys"):
                static_nodes(context, {"validator01": keys["validator01"]})

    def test_local_chain_artifact_uses_shared_bridge_addresses_not_fake_vpn_ips(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = write_chain_bootstrap(plan, Path(temporary) / "chain", "0x" + "b" * 40)
            context = json.loads((root / "chain-context.json").read_text())
            self.assertEqual(context["nodes"]["validator01"]["private_address"], "172.30.0.10")
            self.assertEqual(context["nodes"]["rpc01"]["private_address"], "172.30.0.14")

    def test_rejects_invalid_qbft_settings(self):
        document = json.loads((ROOT / "examples" / "deployment-v2" / "local-ha.json").read_text())
        document["blockchain"]["qbft"]["block_period_seconds"] = 0
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "block_period_seconds"):
                load_inventory(path)

    def test_rejects_incomplete_or_incompatible_runtime_settings(self):
        document = json.loads((ROOT / "examples" / "deployment-v2" / "local-ha.json").read_text())
        del document["settings"]["store"]["status_concurrency"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "settings.store is incomplete"):
                load_inventory(path)
            document = json.loads((ROOT / "examples" / "deployment-v2" / "local-ha.json").read_text())
            document["settings"]["minter"]["metadata"]["min_concurrency"] = 5
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(InventoryError, "metadata has incompatible"):
                load_inventory(path)

    def test_exposure_controls_host_ports_and_remote_explorer_needs_private_rpc(self):
        document = json.loads((ROOT / "examples" / "deployment-v2" / "local-ha.json").read_text())
        del document["exposure"]["services"]["store-api"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "local.json"
            path.write_text(json.dumps(document))
            plan = build_plan(path)
            rendered = render_plan(plan, Path(temporary) / "bundle")
            apps = yaml.safe_load((rendered / "groups" / "apps" / "compose.yaml").read_text())
            self.assertEqual(apps["services"]["store-api"]["ports"], [])

            remote = json.loads((ROOT / "examples" / "deployment-v2" / "production-five-host.json").read_text())
            remote["exposure"]["services"]["blockchain-rpc"]["bind"] = "loopback"
            path.write_text(json.dumps(remote))
            with self.assertRaisesRegex(InventoryError, "blockchain-rpc must bind private"):
                load_inventory(path)
            remote = json.loads((ROOT / "examples" / "deployment-v2" / "production-five-host.json").read_text())
            remote["exposure"]["services"]["store-api"]["port"] = 8001
            path.write_text(json.dumps(remote))
            with self.assertRaisesRegex(InventoryError, "duplicates minter-api"):
                load_inventory(path)

    def test_greenfield_secret_init_generates_runtime_files_without_wallet(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "secrets"
            created = initialize_greenfield_secrets(plan, output)
            self.assertEqual(len(created), 5)
            self.assertEqual((output / "ipfs" / "cluster.secret").stat().st_mode & 0o777, 0o600)
            runtime = (output / "runtime" / "apps" / "minter.private.env").read_text()
            self.assertIn("DATABASE_URL=postgresql://dark:", runtime)
            self.assertIn("APP_KEY=base64:", (output / "runtime" / "apps" / "dashboard.private.env").read_text())
            self.assertFalse((output / "blockchain" / "master-wallet").exists())
            with self.assertRaises(SecretError):
                initialize_greenfield_secrets(plan, output)

    def test_chain_role_export_contains_only_assigned_node_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "chain"
            root.mkdir()
            (root / "genesis.json").write_text("{}\n")
            for node in ("validator01", "validator02", "validator03", "validator04", "rpc01"):
                node_dir = root / "nodes" / node
                node_dir.mkdir(parents=True)
                (node_dir / "nodekey").write_text(node)
                (node_dir / "key.pub").write_text(node)
                static = root / "static-nodes"
                static.mkdir(exist_ok=True)
                (static / f"{node}.json").write_text("[]\n")
            exported = export_chain_role(root, "validators-a", Path(temporary) / "role")
            self.assertTrue((exported / "nodes" / "validator01" / "nodekey").exists())
            self.assertFalse((exported / "nodes" / "validator03").exists())
            self.assertTrue((exported / "artifact-manifest.json").exists())
            self.assertIn("genesis.json", verify_artifact_manifest(exported))

    def test_artifact_manifest_rejects_an_altered_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            root.mkdir()
            artifact = root / "genesis.json"
            artifact.write_text("original\n")
            write_artifact_manifest(root)
            artifact.write_text("changed\n")
            with self.assertRaisesRegex(ArtifactError, "does not match"):
                verify_artifact_manifest(root)

    def test_resume_skips_groups_already_recorded_as_applied(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-simple.json")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            with mock.patch("deployment_v2.runner.run_preflight", return_value=[]), mock.patch("deployment_v2.runner.source_evidence", return_value={}), mock.patch("deployment_v2.runner._apply_group") as apply_group, mock.patch("deployment_v2.verify.verify", return_value={"ok": True}):
                apply(plan, project)
                self.assertEqual(apply_group.call_count, 5)
                self.assertEqual([call.kwargs["phase"] for call in apply_group.call_args_list], ["full", "full", "bootstrap", "full", "runtime"])
                apply(plan, project, resume=True)
                self.assertEqual(apply_group.call_count, 5)

    def test_push_prepares_public_bundle_and_apply_accepts_it(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-simple.json")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            with mock.patch("deployment_v2.runner.source_evidence", return_value={}), mock.patch("deployment_v2.runner._local_prepare"):
                root = push(plan, project)
            self.assertEqual(json.loads((root / "status.json").read_text())["state"], "pushed")
            with mock.patch("deployment_v2.runner.run_preflight", return_value=[]), mock.patch("deployment_v2.runner.source_evidence", return_value={}), mock.patch("deployment_v2.runner._apply_group") as apply_group, mock.patch("deployment_v2.verify.verify", return_value={"ok": True}):
                apply(plan, project)
            self.assertEqual(apply_group.call_count, 5)

    def test_apply_refuses_sources_changed_since_push(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-simple.json")
        pushed_evidence = {"dark-core-lib": {"commit": "a", "dirty": False}}
        current_evidence = {"dark-core-lib": {"commit": "b", "dirty": False}}
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            root = run_root(project, plan.deployment_id)
            root.mkdir(parents=True)
            (root / "bundle").mkdir()
            write_status(root, {"deployment_id": plan.deployment_id, "state": "pushed", "groups": {}, "sources": pushed_evidence})
            with mock.patch("deployment_v2.runner.source_evidence", return_value=current_evidence):
                with self.assertRaisesRegex(ApplyError, "source evidence changed"):
                    apply(plan, project)
        with self.assertRaisesRegex(SourceError, "dark-core-lib"):
            require_matching_source_evidence(pushed_evidence, current_evidence)

    def test_verify_reads_compose_state_and_records_result(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")

        class HealthyExecutor:
            def run(self, argv, *, timeout=30.0):
                if "ps" in argv:
                    group_id = next(part.removeprefix(f"{plan.deployment_id}-") for part in argv if part.startswith(f"{plan.deployment_id}-"))
                    group = plan.group(group_id)
                    names = {
                        "apps": ["blockchain-rpc", "postgres", "admin-api", "resolver-api", "store-api", "minter-api", "minter-metadata-worker", "minter-replication-worker", "minter-chain-worker", "dashboard-mysql", "dashboard-redis", "dashboard"],
                        "validators-a": ["validator01", "validator02", "explorer"],
                        "validators-b": ["validator03", "validator04"],
                        "storage-a": ["ipfs", "cluster"],
                        "storage-b": ["ipfs", "cluster"],
                    }[group.id]
                    return CommandResult(tuple(argv), 0, json.dumps([{"Service": name, "State": "running"} for name in names]), "")
                if "eth_chainId" in " ".join(argv):
                    return CommandResult(tuple(argv), 0, "0x7e9\n", "")
                if "eth_blockNumber" in " ".join(argv):
                    return CommandResult(tuple(argv), 0, "0x1\n", "")
                if "/health?refresh=true" in " ".join(argv):
                    return CommandResult(tuple(argv), 0, "2\n", "")
                return CommandResult(tuple(argv), 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            root = run_root(project, plan.deployment_id)
            write_status(root, {"deployment_id": plan.deployment_id, "state": "applied", "groups": {group.id: "applied" for group in plan.groups}})
            with mock.patch("deployment_v2.verify.resolve_executor", return_value=HealthyExecutor()):
                report = verify(plan, project)
            self.assertTrue(report["ok"])
            self.assertEqual(report["probes"]["store_cluster_peers_visible"], 2)
            self.assertEqual(json.loads((root / "status.json").read_text())["state"], "verified")

    def test_source_evidence_rejects_a_declared_branch_mismatch(self):
        plan = build_plan(ROOT / "examples" / "deployment-v2" / "local-ha.json")
        plan.raw["components"]["dark-core-lib"]["branch"] = "definitely-not-current"
        with self.assertRaisesRegex(SourceError, "expected definitely-not-current"):
            source_evidence(plan, ROOT)

    def test_deployment_lock_creates_a_controller_lock_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            with deployment_lock(root):
                self.assertTrue((root / ".controller.lock").exists())

    def test_immutable_artifact_copy_refuses_to_replace_different_file(self):
        class Executor:
            def __init__(self):
                self.commands = []

            def run(self, argv, *, timeout=30.0):
                self.commands.append(tuple(argv))
                if argv[0] == "test":
                    return CommandResult(tuple(argv), 0, "", "")
                if argv[0] == "cmp":
                    return CommandResult(tuple(argv), 1, "", "")
                return CommandResult(tuple(argv), 0, "", "")

        with self.assertRaisesRegex(ApplyError, "refusing to overwrite"):
            _copy_if_absent_or_identical(Executor(), Path("/source"), Path("/destination"), "install test artifact")

    def test_rpc_probe_waits_in_the_compose_network_before_contracts(self):
        class Executor:
            def __init__(self):
                self.calls = 0

            def run(self, argv, *, timeout=30.0):
                self.calls += 1
                return CommandResult(tuple(argv), 0, "", "")

        executor = Executor()
        _wait_for_rpc(executor, ("docker", "compose", "-f", "/bundle/compose.yaml"), 2025)
        self.assertEqual(executor.calls, 1)

    def test_public_source_filter_excludes_private_and_runtime_files(self):
        ignored = _ignore_public_source("/source", [".git", ".env", ".env.example", ".env.node.local", "node_modules", "app.py"])
        self.assertEqual(ignored, {".git", ".env", ".env.node.local", "node_modules"})

    def test_ssh_transfer_passes_public_source_exclusions_to_rsync(self):
        machine = Machine("remote", "ssh", "remote.example", "10.0.0.2", SshSettings("dark", 22, "/tmp/key", None), "/srv/dark", "/srv/data", "/srv/secrets")
        with mock.patch("deployment_v2.executor.subprocess.run") as command:
            command.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            SshExecutor(machine).transfer(Path("/source"), "/destination", excludes=(".git", ".env"))
        argv = command.call_args.args[0]
        self.assertIn("--exclude", argv)
        self.assertIn(".git", argv)
        self.assertIn(".env", argv)


if __name__ == "__main__":
    unittest.main()
