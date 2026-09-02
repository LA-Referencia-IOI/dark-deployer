import hashlib
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dark_installer", PROJECT_ROOT / "install.py")
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


class CommandParsingTests(unittest.TestCase):
    def test_developer_wizard_selects_ha_topology(self):
        env = {
            "TYPE": "developer",
            "DEVELOPER_INSTALL_COMPONENTS": "all",
            "DEVELOPER_STORAGE_SITE_ID": "site-a",
        }
        with (
            mock.patch.object(installer, "_ask_choice", side_effect=[1, 2]),
            mock.patch.object(installer, "_ask_confirm", return_value=True),
            mock.patch.object(
                installer, "_prepare_developer_storage_assets"
            ) as prepare,
            mock.patch.object(installer, "update_env_file"),
        ):
            selected = installer.run_setup_wizard(env)

        self.assertEqual(selected["DEVELOPER_STORAGE_MODE"], "ha")
        self.assertEqual(
            selected["DEVELOPER_STORAGE_TOPOLOGY_FILE"],
            "storage-topology.developer-ha.json",
        )
        prepare.assert_called_once_with(selected)

    def test_resume_requires_an_explicit_known_stage(self):
        args = installer.build_arg_parser().parse_args(
            ["resume", "--from", "store-api"]
        )
        self.assertEqual(args.command, "resume")
        self.assertEqual(args.from_stage, "store-api")

    def test_json_commands_preserve_shell_pipes(self):
        commands = installer.parse_commands('["producer | consumer","docker compose up -d"]')
        self.assertEqual(commands, ["producer | consumer", "docker compose up -d"])

    def test_legacy_commands_remain_supported(self):
        commands = installer.parse_commands("first|second")
        self.assertEqual(commands, ["first", "second"])

    def test_commands_json_takes_precedence(self):
        env = {
            "TEST_COMMANDS": "legacy|commands",
            "TEST_COMMANDS_JSON": '["new | pipeline"]',
        }
        self.assertEqual(installer.get_commands(env, "TEST_COMMANDS"), ["new | pipeline"])

    def test_dashboard_setup_falls_back_when_commands_json_is_empty(self):
        env = {
            "SANDBOX_DASHBOARD_REPOSITORY_URL": "git@github.com:example/dashboard-web.git",
            "SANDBOX_DASHBOARD_COMMANDS_JSON": "[]",
        }
        with (
            mock.patch.object(installer, "install_repo"),
            mock.patch.object(installer, "run_commands") as run_commands,
        ):
            installer.install_dashboard("SANDBOX", env)

        run_commands.assert_called_once()
        self.assertEqual(run_commands.call_args.args[0], ["python3 install.py"])

    def test_all_example_json_commands_are_valid(self):
        env = installer.parse_env_file(PROJECT_ROOT / ".env.example")
        for key, value in env.items():
            if key.endswith("_COMMANDS_JSON"):
                with self.subTest(key=key):
                    installer.parse_commands(value)

    def test_documentation_does_not_embed_private_keys(self):
        assignment = re.compile(r"PRIVATE_KEY\s*=\s*(?:0x)?[0-9a-fA-F]{64}\b")
        paths = [PROJECT_ROOT / ".env.example", PROJECT_ROOT / "README.md"]
        paths.extend((PROJECT_ROOT / "docs").glob("*.md"))
        for path in paths:
            with self.subTest(path=path.name):
                self.assertIsNone(assignment.search(path.read_text()))

    def test_minter_worker_probe_accepts_current_heartbeat_schema(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "workers": {
                "metadata": {"alive": True, "status": "running"},
                "chain": {"alive": True, "status": "running"},
            }
        }).encode()
        response.__enter__.return_value = response

        with mock.patch.object(installer.urllib.request, "urlopen", return_value=response):
            status = installer.probe_minter_worker_status("http://localhost:8001")

        self.assertEqual(status, "metadata=up, chain=up")

    def test_developer_storage_probe_uses_host_loopback(self):
        topology = installer.StorageTopology(
            "dark-developer",
            (),
            development_single_node=True,
        )

        self.assertEqual(
            installer.storage_probe_url(
                topology,
                "http://dark-ipfs-local:5001",
                "127.0.0.1",
                5001,
            ),
            "http://127.0.0.1:5001",
        )

    def test_developer_storage_operations_use_published_cluster_port(self):
        peer = installer.StoragePeer(
            id="site-a-storage-1",
            site="site-a",
            vpn_address="127.0.0.1",
            ipfs_api_url="http://dark-ipfs-local:5001",
            cluster_api_url="http://dark-ipfs-cluster-local:9094",
            cluster_proxy_url="http://dark-ipfs-cluster-local:9095",
        )
        topology = installer.StorageTopology(
            "dark-developer",
            (peer,),
            development_single_node=True,
        )

        self.assertEqual(
            installer.cluster_operation_endpoints(topology, "site-a"),
            ["http://127.0.0.1:9094"],
        )


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.public_path = root / ".env.integration"
        self.secret_path = root / ".env.integration.secrets"
        self.public_patch = mock.patch.object(
            installer, "_ROOT_ENV_INTEGRATION", self.public_path
        )
        self.secret_patch = mock.patch.object(
            installer, "_ROOT_ENV_INTEGRATION_SECRETS", self.secret_path
        )
        self.public_patch.start()
        self.secret_patch.start()

    def tearDown(self):
        self.secret_patch.stop()
        self.public_patch.stop()
        self.temporary.cleanup()

    def test_apps_handoff_is_authoritative(self):
        self.public_path.write_text(
            "DARK_RPC_URL=http://10.0.0.5:8545\n"
            "DARK_CHAIN_ID=2025\n"
            "DARK_CONTRACT_ADDRESS=0xabc\n"
            "DARK_AUTHORITY_ADDRESS=0xdef\n"
            "METADATA_STORE_API_URL=http://10.0.0.6:8003\n"
        )
        self.secret_path.write_text("DARK_ADMIN_PRIVATE_KEY=0xsecret\n")
        env = {
            "RPC_URL": "http://localhost:8545",
            "ADMIN_PRIVATE_KEY": "stale-admin",
            "MINTER_PRIVATE_KEY": "stale-minter",
            "SANDBOX_DARK_CONTRACT_ADDRESS": "",
            "SANDBOX_AUTHORITY_CONTRACT_ADDRESS": "",
            "SANDBOX_STORE_API_URL": "",
        }

        installer._merge_root_env_integration_into_env(
            env, "SANDBOX", authoritative=True
        )

        self.assertEqual(env["RPC_URL"], "http://10.0.0.5:8545")
        self.assertEqual(env["ADMIN_PRIVATE_KEY"], "0xsecret")
        self.assertEqual(env["MINTER_PRIVATE_KEY"], "0xsecret")
        self.assertEqual(env["SANDBOX_DARK_CONTRACT_ADDRESS"], "0xabc")
        self.assertEqual(env["SANDBOX_AUTHORITY_CONTRACT_ADDRESS"], "0xdef")
        self.assertEqual(env["SANDBOX_STORE_API_URL"], "http://10.0.0.6:8003")

    def test_non_authoritative_handoff_only_fills_blanks(self):
        self.public_path.write_text(
            "DARK_RPC_URL=http://10.0.0.5:8545\n"
            "DARK_CHAIN_ID=2025\n"
            "DARK_CONTRACT_ADDRESS=0xabc\n"
        )
        env = {
            "RPC_URL": "http://custom.example:8545",
            "SANDBOX_DARK_CONTRACT_ADDRESS": "",
        }

        installer._merge_root_env_integration_into_env(
            env, "SANDBOX", authoritative=False
        )

        self.assertEqual(env["RPC_URL"], "http://custom.example:8545")
        self.assertEqual(env["SANDBOX_DARK_CONTRACT_ADDRESS"], "0xabc")

    def test_generated_handoff_splits_private_key(self):
        env = {
            "RPC_URL": "http://localhost:8545",
            "CHAIN_ID": "2025",
            "MASTER_PRIVATE_KEY": "0xprivate",
        }
        with (
            mock.patch.object(
                installer,
                "resolve_contract_addresses",
                return_value=("0xdark", "0xauthority"),
            ),
            mock.patch.object(
                installer,
                "read_deployed_contract_abis",
                return_value=("[]", "[]"),
            ),
        ):
            installer._generate_root_env_integration_blockchain(env)

        public = installer.load_optional_env(self.public_path)
        secrets = installer.load_optional_env(self.secret_path)
        self.assertNotIn("DARK_ADMIN_PRIVATE_KEY", public)
        self.assertEqual(secrets["DARK_ADMIN_PRIVATE_KEY"], "0xprivate")
        self.assertEqual(secrets["DARK_MINTER_PRIVATE_KEY"], "0xprivate")
        self.assertEqual(self.secret_path.stat().st_mode & 0o777, 0o600)

    def test_shared_signer_handoff_contains_only_platform_address(self):
        key_path = Path(self.temporary.name) / "platform.key"
        key_path.write_text("33" * 32 + "\n")
        key_path.chmod(0o600)
        env = {
            "TYPE": "production",
            "SIGNER_MODE": "shared",
            "PLATFORM_PRIVATE_KEY_FILE": str(key_path),
            "RPC_URL": "http://localhost:8545",
            "CHAIN_ID": "2025",
        }
        with (
            mock.patch.object(
                installer,
                "resolve_contract_addresses",
                return_value=("0xdark", "0xauthority"),
            ),
            mock.patch.object(
                installer,
                "read_deployed_contract_abis",
                return_value=("[]", "[]"),
            ),
        ):
            installer._generate_root_env_integration_blockchain(env)

        public = installer.load_optional_env(self.public_path)
        self.assertRegex(public["DARK_PLATFORM_ADDRESS"], r"^0x[0-9A-Fa-f]{40}$")
        self.assertFalse(self.secret_path.exists())

    def test_profile_minter_shoulder_is_written_to_minter_integration_env(self):
        minter_path = Path(self.temporary.name) / "minter"
        minter_path.mkdir()
        (minter_path / ".env.example").write_text(
            "MINTER_SHOULDER=200\n"
            "METADATA_STORAGE_TYPE=store_api\n"
            "REPLICATION_WORKER_CONCURRENCY=2\n"
            "REPLICATION_WORKER_RUNTIME_NAME=replication-reconciler\n"
        )
        env = {
            "TYPE": "production",
            "PRODUCTION_MINTER_SHOULDER": "201",
            "CHAIN_ID": "2025",
            "RPC_URL": "http://localhost:8545",
            "REPLICATION_WORKER_CONCURRENCY": "3",
        }

        with mock.patch.object(
            installer,
            "resolve_contract_addresses",
            return_value=("0xdark", "0xauthority"),
        ):
            installer.generate_minter_env_integration(minter_path, env)

        generated = installer.load_optional_env(minter_path / ".env.integration")
        self.assertEqual(generated["MINTER_SHOULDER"], "201")
        self.assertEqual(generated["REPLICATION_WORKER_ENABLED"], "true")
        self.assertEqual(generated["REPLICATION_WORKER_PAGE_SIZE"], "50")
        self.assertEqual(generated["REPLICATION_WORKER_CONCURRENCY"], "3")
        self.assertEqual(generated["REPLICATION_WORKER_SLEEP_SECONDS"], "30")
        self.assertEqual(generated["REPLICATION_WORKER_RECHECK_SECONDS"], "300")
        self.assertEqual(generated["REPLICATION_WORKER_STORAGE_RETRY_SECONDS"], "10")
        self.assertEqual(
            generated["REPLICATION_WORKER_RUNTIME_NAME"],
            "replication-reconciler",
        )

    def test_invalid_profile_minter_shoulder_is_rejected(self):
        with self.assertRaises(SystemExit):
            installer.resolve_minter_shoulder(
                "PRODUCTION", {"PRODUCTION_MINTER_SHOULDER": "001"}
            )

    def test_secure_writer_replaces_permissive_mode(self):
        target = Path(self.temporary.name) / "service.env"
        target.write_text("OLD=value\n")
        target.chmod(0o644)

        installer.write_env_secure(target, {"NEW": "value"})

        self.assertEqual(target.read_text(), "NEW=value\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_store_api_env_is_derived_from_both_site_peers(self):
        root = Path(self.temporary.name)
        topology_path = root / "topology.json"
        topology_path.write_text(json.dumps({
            "version": 1,
            "cluster_name": "dark-global",
            "sites": [
                {
                    "id": "site-a",
                    "peers": [
                        {"id": "site-a-storage-1", "vpn_address": "10.20.1.11"},
                        {"id": "site-a-storage-2", "vpn_address": "10.20.1.12"},
                    ],
                },
                {
                    "id": "site-b",
                    "peers": [
                        {"id": "site-b-storage-1", "vpn_address": "10.20.2.11"},
                        {"id": "site-b-storage-2", "vpn_address": "10.20.2.12"},
                    ],
                },
            ],
        }))
        store_path = root / "store-api"
        store_path.mkdir()
        (store_path / ".env.example").write_text("STORAGE_BACKEND=ipfs_cluster\n")
        env = {
            "TYPE": "sandbox",
            "SANDBOX_STORAGE_TOPOLOGY_FILE": str(topology_path),
            "SANDBOX_STORAGE_SITE_ID": "site-a",
        }

        installer.generate_store_api_env_integration(store_path, env)

        generated = installer.load_optional_env(store_path / ".env.integration")
        self.assertEqual(len(json.loads(generated["IPFS_API_URLS_JSON"])), 2)
        self.assertEqual(generated["IPFS_CLUSTER_LOCAL_SITE_ID"], "site-a")
        self.assertNotIn("IPFS_CLUSTER_EXPECTED_PEERS", generated)
        self.assertNotIn("IPFS_CLUSTER_WRITE_MIN_PEERS", generated)
        self.assertNotIn("IPFS_CLUSTER_WRITE_MIN_SITES", generated)


class ValidationTests(unittest.TestCase):
    def test_developer_ha_assets_create_two_independent_peers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "TYPE": "developer",
                "DEVELOPER_INSTALL_COMPONENTS": "all",
                "DEVELOPER_STORAGE_MODE": "ha",
                "DEVELOPER_STORAGE_TOPOLOGY_FILE": (
                    "storage-topology.developer-ha.json"
                ),
                "DEVELOPER_STORAGE_SITE_ID": "site-a",
                "DEVELOPER_STORAGE_NODE_ID": "site-a-storage-1",
            }

            with mock.patch.object(installer, "PROJECT_ROOT", root):
                installer._prepare_developer_storage_assets(env)
                topology = installer.configured_storage_topology(
                    "DEVELOPER", env
                )
                installer.validate_install_configuration("DEVELOPER", env)

            self.assertFalse(topology.development_single_node)
            self.assertEqual(len(topology.peers), 2)
            self.assertEqual(topology.policy.replication_min, 1)
            self.assertEqual(topology.policy.replication_max, 2)
            self.assertEqual(
                topology.peers[1].host_ipfs_api_url,
                "http://127.0.0.1:5101",
            )
            self.assertEqual(
                topology.peers[1].host_cluster_api_url,
                "http://127.0.0.1:9194",
            )

    def test_developer_ha_install_starts_both_storage_peers(self):
        topology = installer.StorageTopology(
            "dark-developer",
            (
                installer.StoragePeer(
                    "site-a-storage-1",
                    "site-a",
                    "127.0.0.1",
                    "http://dark-ipfs-site-a-storage-1:5001",
                    "http://dark-ipfs-cluster-site-a-storage-1:9094",
                    "http://dark-ipfs-cluster-site-a-storage-1:9095",
                    "http://127.0.0.1:5001",
                    "http://127.0.0.1:9094",
                    "http://127.0.0.1:9095",
                ),
                installer.StoragePeer(
                    "site-a-storage-2",
                    "site-a",
                    "127.0.0.2",
                    "http://dark-ipfs-site-a-storage-2:5001",
                    "http://dark-ipfs-cluster-site-a-storage-2:9094",
                    "http://dark-ipfs-cluster-site-a-storage-2:9095",
                    "http://127.0.0.1:5101",
                    "http://127.0.0.1:9194",
                    "http://127.0.0.1:9195",
                ),
            ),
        )
        env = {
            "DEVELOPER_IPFS_REPOSITORY_URL": "https://example.invalid/ipfs.git",
            "DEVELOPER_IPFS_REPOSITORY_BRANCH": "main",
            "DEVELOPER_IPFS_SETUP": "True",
            "DEVELOPER_STORAGE_SITE_ID": "site-a",
            "DEVELOPER_STORAGE_NODE_ID": "site-a-storage-1",
            "DEVELOPER_IPFS_SWARM_KEY_FILE": "/tmp/swarm.key",
            "DEVELOPER_IPFS_CLUSTER_SECRET_FILE": "/tmp/cluster.secret",
        }
        writes = []
        setups = []

        with (
            mock.patch.object(installer, "install_repo"),
            mock.patch.object(
                installer, "configured_storage_topology", return_value=topology
            ),
            mock.patch.object(installer, "ensure_dark_net"),
            mock.patch.object(
                installer,
                "write_env_secure",
                side_effect=lambda path, values: writes.append((path, values)),
            ),
            mock.patch.object(
                installer,
                "setup_repo",
                side_effect=lambda **kwargs: setups.append(kwargs),
            ),
        ):
            installer.install_dark_ipfs("DEVELOPER", env)

        self.assertEqual(len(writes), 2)
        self.assertEqual(len(setups), 2)
        self.assertEqual(writes[0][1]["IPFS_API_HOST_PORT"], "5001")
        self.assertEqual(writes[1][1]["IPFS_API_HOST_PORT"], "5101")
        self.assertIn(
            "dark-ipfs-site-a-storage-1",
            writes[1][1]["IPFS_BOOTSTRAP_HOSTS"],
        )
        self.assertIn(".env.node.site-a-storage-1", setups[0]["commands_str"][0])
        self.assertIn(".env.node.site-a-storage-2", setups[1]["commands_str"][0])

    def test_developer_wizard_assets_are_generated_once_and_securely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {
                "TYPE": "developer",
                "DEVELOPER_INSTALL_COMPONENTS": "all",
                "DEVELOPER_STORAGE_TOPOLOGY_FILE": "storage-topology.json",
                "DEVELOPER_STORAGE_SITE_ID": "site-a",
                "DEVELOPER_STORAGE_NODE_ID": "site-a-storage-1",
                "DEVELOPER_IPFS_SWARM_KEY_FILE": "/missing/swarm.key",
                "DEVELOPER_IPFS_CLUSTER_SECRET_FILE": "/missing/cluster.secret",
            }

            with mock.patch.object(installer, "PROJECT_ROOT", root):
                installer._prepare_developer_storage_assets(env)
                first_swarm = Path(env["DEVELOPER_IPFS_SWARM_KEY_FILE"]).read_text()
                first_cluster = Path(
                    env["DEVELOPER_IPFS_CLUSTER_SECRET_FILE"]
                ).read_text()
                installer._prepare_developer_storage_assets(env)
                installer.validate_install_configuration("DEVELOPER", env)

            topology = installer.load_storage_topology(
                root / "storage-topology.json"
            )
            self.assertTrue(topology.development_single_node)
            self.assertEqual(len(topology.peers), 1)
            self.assertEqual(
                topology.peers[0].ipfs_api_url,
                "http://dark-ipfs-local:5001",
            )
            self.assertEqual(
                Path(env["DEVELOPER_IPFS_SWARM_KEY_FILE"]).read_text(),
                first_swarm,
            )
            self.assertEqual(
                Path(env["DEVELOPER_IPFS_CLUSTER_SECRET_FILE"]).read_text(),
                first_cluster,
            )
            self.assertEqual(
                Path(env["DEVELOPER_IPFS_SWARM_KEY_FILE"]).stat().st_mode & 0o777,
                0o600,
            )
            self.assertEqual(
                Path(env["DEVELOPER_IPFS_CLUSTER_SECRET_FILE"]).stat().st_mode
                & 0o777,
                0o600,
            )

    def test_cluster_storage_assets_generate_secrets_and_topology(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_dir = root / "secrets"
            env = {
                "TYPE": "sandbox",
                "SANDBOX_INSTALL_COMPONENTS": "storage-node",
                "SANDBOX_STORAGE_SITE_ID": "site-a",
                "SANDBOX_STORAGE_NODE_ID": "site-a-storage-1",
                "SANDBOX_STORAGE_TOPOLOGY_FILE": "storage-topology.json",
                # placeholder from a stale .env.example must be treated as unset
                "SANDBOX_IPFS_SWARM_KEY_FILE": "/absolute/secret/path/ipfs-swarm.key",
                "SANDBOX_IPFS_CLUSTER_SECRET_FILE": "/absolute/secret/path/ipfs-cluster-secret",
            }
            with (
                mock.patch.object(installer, "PROJECT_ROOT", root),
                mock.patch.object(
                    installer, "_DEFAULT_STORAGE_SECRET_DIR", secret_dir
                ),
            ):
                installer._prepare_cluster_storage_assets("SANDBOX", env)
                swarm = Path(env["SANDBOX_IPFS_SWARM_KEY_FILE"])
                cluster = Path(env["SANDBOX_IPFS_CLUSTER_SECRET_FILE"])
                self.assertEqual(swarm, secret_dir / "ipfs-swarm.key")
                first_swarm = swarm.read_bytes()
                # second run is a no-op — a later node keeps the copied file
                installer._prepare_cluster_storage_assets("SANDBOX", env)
                self.assertEqual(swarm.read_bytes(), first_swarm)

            self.assertEqual(swarm.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cluster.stat().st_mode & 0o777, 0o600)
            installer._validate_storage_secret_file("S", str(swarm), "swarm")
            installer._validate_storage_secret_file("C", str(cluster), "cluster")
            topology = installer.load_storage_topology(root / "storage-topology.json")
            self.assertEqual(len(topology.site_peers("site-a")), 2)

    def test_cluster_storage_assets_keep_existing_secret_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret_dir = root / "secrets"
            secret_dir.mkdir()
            swarm = secret_dir / "ipfs-swarm.key"
            copied = "/key/swarm/psk/1.0.0/\n/base16/\n" + ("a" * 64) + "\n"
            swarm.write_text(copied)
            swarm.chmod(0o600)
            env = {
                "TYPE": "production",
                "PRODUCTION_INSTALL_COMPONENTS": "storage-node",
                "PRODUCTION_STORAGE_SITE_ID": "site-a",
                "PRODUCTION_STORAGE_NODE_ID": "site-a-storage-2",
                "PRODUCTION_STORAGE_TOPOLOGY_FILE": "storage-topology.json",
            }
            with (
                mock.patch.object(installer, "PROJECT_ROOT", root),
                mock.patch.object(
                    installer, "_DEFAULT_STORAGE_SECRET_DIR", secret_dir
                ),
            ):
                installer._prepare_cluster_storage_assets("PRODUCTION", env)
            self.assertEqual(swarm.read_text(), copied)

    def test_resume_from_store_api_skips_completed_stages(self):
        env = {
            "TYPE": "developer",
            "DEVELOPER_INSTALL_COMPONENTS": "all",
        }
        with (
            mock.patch.object(installer, "validate_root_env_integration"),
            mock.patch.object(installer, "_merge_root_env_integration_into_env"),
            mock.patch.object(installer, "validate_install_configuration"),
            mock.patch.object(installer, "install_blockchain") as blockchain,
            mock.patch.object(installer, "install_core_lib") as core_lib,
            mock.patch.object(installer, "install_core_admin_api") as admin,
            mock.patch.object(installer, "install_dark_ipfs") as ipfs,
            mock.patch.object(installer, "install_dark_store_api") as store_api,
            mock.patch.object(installer, "_update_root_env_integration_storage"),
            mock.patch.object(installer, "install_core_resolver_api") as resolver,
            mock.patch.object(installer, "install_single_component") as minter,
            mock.patch.object(installer, "install_dashboard") as dashboard,
        ):
            installer.install_profile(
                "DEVELOPER",
                env,
                resume_from="store-api",
            )

        blockchain.assert_not_called()
        core_lib.assert_not_called()
        admin.assert_not_called()
        ipfs.assert_not_called()
        store_api.assert_called_once()
        resolver.assert_called_once()
        minter.assert_called_once()
        dashboard.assert_called_once()

    def test_legacy_storage_role_is_rejected(self):
        with self.assertRaises(SystemExit):
            installer._selected_tiers("SANDBOX", {"SANDBOX_INSTALL_COMPONENTS": "storage"})

    def test_single_node_developer_topology_is_rejected_by_production(self):
        document = {
            "version": 1,
            "cluster_name": "dark-developer",
            "development_single_node": True,
            "sites": [
                {
                    "id": "site-a",
                    "peers": [
                        {
                            "id": "site-a-storage-1",
                            "vpn_address": "127.0.0.1",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "topology.json"
            path.write_text(json.dumps(document))
            topology = installer.load_storage_topology(path)
            env = {
                "TYPE": "production",
                "PRODUCTION_INSTALL_COMPONENTS": "storage-node",
            }
            with (
                mock.patch.object(
                    installer,
                    "configured_storage_topology",
                    return_value=topology,
                ),
                self.assertRaises(SystemExit),
            ):
                installer.validate_install_configuration("PRODUCTION", env)

    def test_storage_secrets_have_distinct_valid_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            swarm = root / "swarm.key"
            cluster = root / "cluster.secret"
            swarm.write_text(
                "/key/swarm/psk/1.0.0/\n/base16/\n" + "a" * 64 + "\n"
            )
            cluster.write_text("b" * 64 + "\n")
            swarm.chmod(0o600)
            cluster.chmod(0o600)

            self.assertEqual(
                installer._validate_storage_secret_file("SWARM", str(swarm), "swarm"),
                swarm,
            )
            self.assertEqual(
                installer._validate_storage_secret_file("CLUSTER", str(cluster), "cluster"),
                cluster,
            )

            cluster.write_text("not-a-secret\n")
            with self.assertRaises(SystemExit):
                installer._validate_storage_secret_file("CLUSTER", str(cluster), "cluster")

    def test_reconcile_only_reapplies_pins(self):
        topology = installer.load_storage_topology(PROJECT_ROOT / "storage-topology.example.json")
        calls = []

        def request(urls, method, path, **kwargs):
            calls.append((tuple(urls), method, path, kwargs))
            if path == "/allocations":
                return '{"cid":"bafy-one"}\n{"cid":"bafy-two"}\n'
            if path == "/pins":
                return ""
            return "{}"

        with mock.patch.object(installer, "cluster_request", side_effect=request):
            installer.reconcile_storage(topology, "site-a")

        mutation_calls = [call for call in calls if call[1] == "POST"]
        self.assertEqual(len(mutation_calls), 2)
        self.assertTrue(all(call[2].startswith("/pins/") for call in mutation_calls))
        self.assertFalse(any(call[1] == "DELETE" for call in calls))

    def test_published_key_guard_is_disabled_only_for_developer(self):
        private_key = "11" * 32
        digest = hashlib.sha256(bytes.fromhex(private_key)).hexdigest()
        with mock.patch.object(installer, "_LEGACY_EXAMPLE_PRIVATE_KEY_SHA256", digest):
            installer.validate_private_key(private_key, "developer")
            with self.assertRaises(SystemExit):
                installer.validate_private_key(private_key, "production")

    def test_invalid_contract_address_is_rejected(self):
        env = {
            "TYPE": "sandbox",
            "SANDBOX_INSTALL_COMPONENTS": "blockchain",
            "CHAIN_ID": "2025",
            "RPC_URL": "http://localhost:8545",
            "SANDBOX_DARK_CONTRACT_ADDRESS": "0xinvalid",
        }
        with self.assertRaises(SystemExit):
            installer.validate_install_configuration("SANDBOX", env)

    def test_production_accepts_explicit_shared_platform_signer(self):
        with tempfile.TemporaryDirectory() as temporary:
            key_path = Path(temporary) / "platform.key"
            key_path.write_text("22" * 32 + "\n")
            key_path.chmod(0o600)
            env = {
                "TYPE": "production",
                "PRODUCTION_INSTALL_COMPONENTS": "apps",
                "RPC_URL": "https://rpc.example",
                "CHAIN_ID": "2025",
                "PRODUCTION_DARK_CONTRACT_ADDRESS": "0x" + "1" * 40,
                "PRODUCTION_AUTHORITY_CONTRACT_ADDRESS": "0x" + "2" * 40,
                "PRODUCTION_STORE_API_URL": "https://store.example",
                "PRODUCTION_STORAGE_SITE_ID": "site-a",
                "SIGNER_MODE": "shared",
                "PLATFORM_PRIVATE_KEY_FILE": str(key_path),
            }
            topology = installer.load_storage_topology(
                PROJECT_ROOT / "storage-topology.example.json"
            )
            with (
                mock.patch.object(
                    installer, "configured_storage_topology", return_value=topology
                ),
                mock.patch.object(installer, "validate_production_release"),
            ):
                installer.validate_install_configuration("PRODUCTION", env)

        self.assertRegex(env["PLATFORM_ADDRESS"], r"^0x[0-9A-Fa-f]{40}$")

    def test_production_release_requires_matching_deployer_branch(self):
        result = mock.Mock(returncode=0, stdout="release-branch\n")
        with mock.patch.object(installer.subprocess, "run", return_value=result):
            installer.validate_production_release(
                "PRODUCTION", {"DEPLOYER_BRANCH": "release-branch"}
            )
            with self.assertRaises(SystemExit):
                installer.validate_production_release(
                    "PRODUCTION", {"DEPLOYER_BRANCH": "other-branch"}
                )

        with self.assertRaises(SystemExit):
            installer.validate_production_release("PRODUCTION", {})

    def test_shared_signer_preseeds_genesis_and_rejects_existing_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key_path = root / "platform.key"
            key_path.write_text("44" * 32 + "\n")
            key_path.chmod(0o600)
            target = root / "dark-env"
            env = {
                "TYPE": "production",
                "SIGNER_MODE": "shared",
                "PLATFORM_PRIVATE_KEY_FILE": str(key_path),
            }

            installer.prepare_dark_env_platform_wallet(str(target), env)
            address_file = target / "config" / "master-wallet"
            self.assertEqual(address_file.read_text().strip(), env["PLATFORM_ADDRESS"])
            self.assertEqual(address_file.stat().st_mode & 0o777, 0o600)

            (target / "config" / "genesis.json").write_text(json.dumps({
                "alloc": {"0x" + "0" * 40: {"balance": "1"}}
            }))
            with self.assertRaises(SystemExit):
                installer.prepare_dark_env_platform_wallet(str(target), env)


class GitInstallerTests(unittest.TestCase):
    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def test_existing_checkout_switches_to_configured_branch_and_fast_forwards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            target = root / "target"

            self.git("init", "--bare", str(remote))
            self.git("init", "-b", "main", str(source))
            self.git("config", "user.email", "tests@example.invalid", cwd=source)
            self.git("config", "user.name", "Installer Tests", cwd=source)
            (source / "version.txt").write_text("one\n")
            self.git("add", "version.txt", cwd=source)
            self.git("commit", "-m", "first", cwd=source)
            self.git("remote", "add", "origin", str(remote), cwd=source)
            self.git("push", "-u", "origin", "main", cwd=source)

            installer.install_repo("test", str(remote), "main", str(target))
            self.git("switch", "-c", "local-work", cwd=target)

            (source / "version.txt").write_text("two\n")
            self.git("commit", "-am", "second", cwd=source)
            self.git("push", cwd=source)

            installer.install_repo("test", str(remote), "main", str(target))

            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(branch, "main")
            self.assertEqual((target / "version.txt").read_text(), "two\n")

    def test_existing_checkout_switches_to_a_branch_not_previously_tracked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            target = root / "target"

            self.git("init", "--bare", str(remote))
            self.git("init", "-b", "main", str(source))
            self.git("config", "user.email", "tests@example.invalid", cwd=source)
            self.git("config", "user.name", "Installer Tests", cwd=source)
            (source / "version.txt").write_text("main\n")
            self.git("add", "version.txt", cwd=source)
            self.git("commit", "-m", "main", cwd=source)
            self.git("remote", "add", "origin", str(remote), cwd=source)
            self.git("push", "-u", "origin", "main", cwd=source)

            self.git("switch", "-c", "release-candidate", cwd=source)
            (source / "version.txt").write_text("release branch\n")
            self.git("commit", "-am", "release branch", cwd=source)
            self.git("push", "-u", "origin", "release-candidate", cwd=source)

            installer.install_repo("test", str(remote), "main", str(target))
            installer.install_repo("test", str(remote), "release-candidate", str(target))

            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(branch, "release-candidate")
            self.assertEqual((target / "version.txt").read_text(), "release branch\n")

    def test_configured_branch_controls_new_clone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            target = root / "target"

            self.git("init", "--bare", str(remote))
            self.git("init", "-b", "main", str(source))
            self.git("config", "user.email", "tests@example.invalid", cwd=source)
            self.git("config", "user.name", "Installer Tests", cwd=source)
            (source / "version.txt").write_text("main\n")
            self.git("add", "version.txt", cwd=source)
            self.git("commit", "-m", "main", cwd=source)
            self.git("remote", "add", "origin", str(remote), cwd=source)
            self.git("push", "-u", "origin", "main", cwd=source)

            self.git("switch", "-c", "release-candidate", cwd=source)
            (source / "version.txt").write_text("release branch\n")
            self.git("commit", "-am", "release branch", cwd=source)
            self.git("push", "-u", "origin", "release-candidate", cwd=source)

            installer.install_repo("test", str(remote), "release-candidate", str(target))

            self.assertEqual((target / "version.txt").read_text(), "release branch\n")
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(branch, "release-candidate")

    def test_missing_configured_branch_falls_back_to_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            target = root / "target"

            self.git("init", "--bare", str(remote))
            self.git("init", "-b", "main", str(source))
            self.git("config", "user.email", "tests@example.invalid", cwd=source)
            self.git("config", "user.name", "Installer Tests", cwd=source)
            (source / "version.txt").write_text("main\n")
            self.git("add", "version.txt", cwd=source)
            self.git("commit", "-m", "main", cwd=source)
            self.git("remote", "add", "origin", str(remote), cwd=source)
            self.git("push", "-u", "origin", "main", cwd=source)

            installer.install_repo("test", str(remote), "missing-branch", str(target))

            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(branch, "main")
            self.assertEqual((target / "version.txt").read_text(), "main\n")


if __name__ == "__main__":
    unittest.main()
