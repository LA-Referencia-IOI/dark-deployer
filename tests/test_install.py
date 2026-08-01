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

    def test_secure_writer_replaces_permissive_mode(self):
        target = Path(self.temporary.name) / "service.env"
        target.write_text("OLD=value\n")
        target.chmod(0o644)

        installer.write_env_secure(target, {"NEW": "value"})

        self.assertEqual(target.read_text(), "NEW=value\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class ValidationTests(unittest.TestCase):
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

    def test_production_requires_distinct_application_signers(self):
        shared_key = "22" * 32
        env = {
            "TYPE": "production",
            "PRODUCTION_INSTALL_COMPONENTS": "apps",
            "RPC_URL": "https://rpc.example",
            "CHAIN_ID": "2025",
            "PRODUCTION_DARK_CONTRACT_ADDRESS": "0x" + "1" * 40,
            "PRODUCTION_AUTHORITY_CONTRACT_ADDRESS": "0x" + "2" * 40,
            "PRODUCTION_STORE_API_URL": "https://store.example",
            "ADMIN_PRIVATE_KEY": shared_key,
            "MINTER_PRIVATE_KEY": shared_key,
        }
        with self.assertRaises(SystemExit):
            installer.validate_install_configuration("PRODUCTION", env)


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

    def test_component_lock_checks_out_exact_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            target = root / "target"
            lock_path = root / "components.lock.json"

            self.git("init", "--bare", str(remote))
            self.git("init", "-b", "main", str(source))
            self.git("config", "user.email", "tests@example.invalid", cwd=source)
            self.git("config", "user.name", "Installer Tests", cwd=source)
            (source / "version.txt").write_text("locked\n")
            self.git("add", "version.txt", cwd=source)
            self.git("commit", "-m", "locked", cwd=source)
            locked_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.git("remote", "add", "origin", str(remote), cwd=source)
            self.git("push", "-u", "origin", "main", cwd=source)

            (source / "version.txt").write_text("newer\n")
            self.git("commit", "-am", "newer", cwd=source)
            self.git("push", cwd=source)
            lock_path.write_text(json.dumps({
                "version": 1,
                "components": {
                    "test": {
                        "repository": str(remote),
                        "commit": locked_commit,
                    }
                },
            }))

            with mock.patch.object(installer, "COMPONENT_LOCK_PATH", lock_path):
                installer.install_repo("test", str(remote), "main", str(target))

            self.assertEqual((target / "version.txt").read_text(), "locked\n")
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(branch, "")


if __name__ == "__main__":
    unittest.main()
