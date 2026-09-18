"""CLI implementation for deploy.py. Only validate, plan and render exist initially."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .inventory import InventoryError, load_inventory
from .executor import ExecutionError, run_preflight
from .planner import build_plan
from .render import render_plan
from .runner import ApplyError, _effective_plan, apply, existing_chain_data, follow_service_logs, list_managed_deployments, list_managed_services, manage_service, managed_plan, persistent_data_inventory, prepare, push
from .artifacts import ArtifactError, export_chain_group, initialize_chain, verify_artifact_compatibility, verify_artifact_manifest, write_chain_bootstrap, write_static_nodes
from .secrets import SecretError, initialize_greenfield_secrets
from .verify import VerifyError, verify
from .sources import SourceError
from .acquire import AcquisitionError, acquire_components
from .inventory_editor import InventoryDocument, InventoryDocumentError, create_from_template, template_names
from .inventory_editor.textual_app import run_textual_editor
from .metrics.textual_app import run_metrics_tui
from .metrics.prometheus import collect_to_textfile
from .operations_tui import run_operations_tui
from .inventory_resolver import resolve_inventory_path
from .availability import analyze as analyze_availability


def _format_preflight_failure(failed: dict[str, list[dict[str, object]]]) -> str:
    lines = ["installation preflight failed"]
    for machine_id, checks in failed.items():
        lines.append(f"  {machine_id}: FAIL")
        for item in checks:
            lines.append(f"    - {item['check']}")
            detail = str(item.get("stderr") or item.get("stdout") or "").strip()
            if detail:
                for line in detail.splitlines():
                    lines.append(f"      {line}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deploy.py", description="dARK declarative deployment v3")
    actions = parser.add_subparsers(dest="action", required=True)
    operational = {"services", "logs", "build", "stop", "start", "restart", "recreate", "remove"}
    for name in ("validate", "plan", "render", "preflight", "prepare", "push", "apply", "resume", "status", "verify", "install", "services", "logs", "build", "stop", "start", "restart", "recreate", "remove", "chain-bootstrap", "chain-static-nodes", "chain-init", "chain-export", "chain-verify", "secrets-init", "inventory-edit", "inventory-resolve", "inventory-explain", "inventory-network-matrix"):
        command = actions.add_parser(name)
        if name in operational:
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--inventory", type=Path, help="inventory used to locate the managed deployment")
            source.add_argument("--deployment", help="managed deployment ID")
        else:
            command.add_argument("--inventory", required=True, type=Path)
        if name == "plan":
            command.add_argument("--json", action="store_true")
        if name == "services":
            command.add_argument("--json", action="store_true", help="emit the runtime service list as JSON")
        if name == "logs":
            command.add_argument("--target", required=True, help="exact managed service selector: service:ID")
            command.add_argument("--tail", type=int, default=100, help="number of existing log lines to print before following (default: 100)")
        if name == "render":
            command.add_argument("--output", required=True, type=Path)
        if name == "push":
            command.add_argument("--revision", help="prepared revision to transfer")
        if name == "inventory-resolve":
            command.add_argument("--output", required=True, type=Path)
        if name == "inventory-explain":
            command.add_argument("--path", required=True, help="JSON Pointer in the resolved v3 document")
        if name == "inventory-network-matrix":
            command.add_argument("--traffic", choices=("blockchain", "ipfs", "cluster"), help="limit output to one P2P family")
        if name == "install":
            command.add_argument("--json", action="store_true", help="print the complete verification JSON")
            command.add_argument("--non-interactive", action="store_true")
            command.add_argument("--yes", action="store_true")
            command.add_argument("--resume", action="store_true")
            command.add_argument("--refresh-bundle", action="store_true",
                                 help="re-render the generated public bundle before applying a resumed deployment")
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--master-wallet-address")
            command.add_argument("--master-wallet-file", type=Path)
            command.add_argument("--create-master-wallet", action="store_true",
                                 help="generate blockchain/master-wallet.txt when no wallet file is supplied")
            command.add_argument("--new-chain", action="store_true",
                                 help="initialize a new managed chain artifact when none exists")
            command.add_argument("--initialize-managed-secrets", action="store_true",
                                 help="generate safe application/storage secrets in the deployment run directory")
            command.add_argument("--use-master-wallet-as-signer", action="store_true",
                                 help="use the selected master wallet as contract signer")
            command.add_argument("--contract-signer-file", type=Path)
            command.add_argument("--chain-artifact", type=Path,
                                 help="existing complete chain artifact; otherwise generate one for a new local chain")
            command.add_argument("--skip-acquire", action="store_true",
                                 help="reuse existing component checkouts instead of cloning/updating them")
            command.add_argument("--verbose", action="store_true",
                                 help="show detailed installation progress and readiness attempts")
            command.add_argument("--clean-empty-network-conflicts", action="store_true",
                                 help="remove only empty Docker bridge networks that overlap the requested deployment subnet")
        if name in {"apply", "resume"}:
            command.add_argument("--revision", help="prepared revision to apply")
            command.add_argument("--clean-empty-network-conflicts", action="store_true",
                                 help="remove only empty Docker bridge networks that overlap the requested deployment subnet")
        if name in {"build", "stop", "start", "restart", "recreate", "remove"}:
            command.add_argument("--target", help="exact managed service selector: service:ID")
            command.add_argument("--dry-run", action="store_true", help="show the resolved target without changing Docker")
        if name == "recreate":
            command.add_argument("--service", help="deprecated alias for --target service:ID")
            command.add_argument("--build", action="store_true")
        if name == "chain-bootstrap":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--master-wallet-address", required=True)
        if name == "chain-static-nodes":
            command.add_argument("--artifact-root", required=True, type=Path)
            command.add_argument("--public-keys", required=True, type=Path,
                                 help="JSON object mapping every resolved Besu node to its public key")
        if name == "secrets-init":
            command.add_argument("--output", required=True, type=Path,
                                 help="empty secure directory to provision on the destination host")
            command.add_argument("--overwrite", action="store_true")
        if name == "chain-init":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--master-wallet-address", required=True)
        if name == "chain-export":
            command.add_argument("--artifact-root", required=True, type=Path)
            command.add_argument("--group", required=True)
            command.add_argument("--output", required=True, type=Path)
        if name == "chain-verify":
            command.add_argument("--artifact-root", required=True, type=Path)
    deployments = actions.add_parser("deployments", help="list locally known managed deployments and live summaries")
    deployments.add_argument("--json", action="store_true", help="emit deployment summaries as JSON")
    actions.add_parser("tui", help="open the interactive deployment operations console")
    actions.add_parser("metrics", help="open the read-only Docker metrics panel")
    metrics_export = actions.add_parser("metrics-export", help="write managed Docker metrics for node-exporter's textfile collector")
    metrics_export.add_argument("--deployment", required=True, help="managed deployment ID")
    metrics_export.add_argument("--output", required=True, type=Path, help="destination .prom file")
    metrics_export.add_argument("--include-disk", action="store_true", help="also run docker system df (host filesystems still require node-exporter)")
    create = actions.add_parser("inventory-create", help="create an inventory from a maintained template")
    create.add_argument("--template", required=True, choices=template_names())
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--overwrite", action="store_true")
    create.add_argument("--edit", action="store_true", help="open the new inventory in the interactive editor")
    diff = actions.add_parser("inventory-diff", help="compare resolved inventories without operational effects")
    diff.add_argument("--before", required=True, type=Path)
    diff.add_argument("--after", required=True, type=Path)
    return parser


def _yes_no(prompt: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _wallet_material(path: Path) -> tuple[str, str | None]:
    """Return a normalized private key and optional public address."""
    text = path.read_text(encoding="utf-8").strip()
    private_match = re.search(r"Private Key\s*:\s*(0x[0-9a-fA-F]{64})", text)
    if private_match:
        private_key = private_match.group(1)
    elif re.fullmatch(r"0x?[0-9a-fA-F]{64}", text):
        private_key = text
    else:
        raise ApplyError(f"secret file is not a raw key or supported master-wallet.txt: {path}")
    address_match = re.search(r"Address\s*:\s*(0x[0-9a-fA-F]{40})", text)
    return private_key, address_match.group(1) if address_match else None


def _write_managed_key(path: Path, source: Path) -> None:
    raw, _ = _wallet_material(source)
    value = (raw + "\n").encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != value:
        raise ApplyError(f"refusing to replace existing managed secret: {path}")
    if not path.exists():
        path.write_bytes(value)
    path.chmod(0o600)


def _private_plan(plan, *, secret_root: Path, wallet_file: Path, signer_file: Path, artifact_root: Path):
    """Attach controller-only sources without changing the operator inventory."""
    raw = deepcopy(plan.raw)
    for secret_id, source in (("master-wallet", wallet_file), ("contract-signer", signer_file)):
        definition = raw.get("secrets", {}).get(secret_id)
        if definition:
            target = secret_root / definition["path"]
            _write_managed_key(target, source)
            definition["source"] = str(target)
            if secret_id == "contract-signer":
                runtime_definition = raw.get("secrets", {}).get("minter-runtime-env")
                if runtime_definition:
                    runtime = secret_root / runtime_definition["path"]
                    existing = runtime.read_text(encoding="utf-8") if runtime.exists() else ""
                    if "DARK_ADMIN_PRIVATE_KEY=" not in existing:
                        key, _ = _wallet_material(source)
                        runtime.parent.mkdir(parents=True, exist_ok=True)
                        runtime.write_text(existing.rstrip("\n") + "\nDARK_ADMIN_PRIVATE_KEY=" + key + "\n", encoding="utf-8")
                        runtime.chmod(0o600)
    for secret_id, definition in raw.get("secrets", {}).items():
        candidate = secret_root / definition["path"]
        # Managed greenfield secrets take precedence over catalog/operator
        # placeholder sources such as REPLACE/secrets.  Explicit private
        # inputs remain supported through the generated secret directory.
        if candidate.is_file():
            definition["source"] = str(candidate)
    raw["blockchain"]["artifact"]["source"] = str(artifact_root)
    return replace(plan, raw=raw)


def _write_private_input_state(root: Path, *, wallet: Path, signer: Path, secrets: Path, artifact: Path) -> None:
    def digest(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    state = {
        "version": 1,
        "chain_mode": "new",
        "master_wallet_source": str(wallet),
        "master_wallet_sha256": digest(wallet),
        "contract_signer_source": str(signer),
        "contract_signer_sha256": digest(signer),
        "generated_secrets_root": str(secrets),
        "chain_artifact_root": str(artifact),
    }
    root.mkdir(parents=True, exist_ok=True)
    destination = root / "private-inputs.json"
    destination.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    destination.chmod(0o600)


def _load_private_input_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    for prefix in ("master_wallet", "contract_signer"):
        source = Path(state[f"{prefix}_source"])
        if not source.is_file():
            raise ApplyError(f"managed private input is missing: {source}")
        expected = state.get(f"{prefix}_sha256")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected and actual != expected:
            raise ApplyError(f"managed private input changed since the previous run: {source}")
    return state


def _install(args: argparse.Namespace, plan) -> None:
    """Run the normal installation path with only essential operator input.

    Low-level commands remain available for advanced operations; this helper
    deliberately coordinates them and keeps generated private material inside
    the deployment run directory.
    """
    project_root = Path(__file__).resolve().parents[1]
    root = project_root / ".generated" / "deployment-v3" / plan.deployment_id
    private_state = None
    private_state_file = root / "private-inputs.json"
    if args.resume:
        if not private_state_file.is_file():
            raise ApplyError(f"cannot resume without managed input state: {private_state_file}")
        private_state = _load_private_input_state(private_state_file)
        args.master_wallet_file = Path(private_state["master_wallet_source"])
        args.contract_signer_file = Path(private_state["contract_signer_source"])
    if args.dry_run:
        print(f"Deployment: {plan.deployment_id}")
        print("Mode: " + ", ".join(f"{m.id}={m.execution}" for m in plan.machines))
        for step in plan.steps:
            print(f"{step.id}: {step.description}")
        return
    def progress(message: str) -> None:
        if args.verbose:
            print(f"[VERBOSE] {message}", flush=True)
    if not args.yes and not args.non_interactive:
        answer = input(f"Install deployment '{plan.deployment_id}' now? [Y/n]: ").strip().lower()
        if answer not in {"", "y", "yes"}:
            print("[INFO] Installation cancelled.")
            return
    # Fail before rendering or starting any service when a destination cannot
    # execute the required Docker/Compose toolchain.  This is deliberately a
    # read-only preflight; filesystem creation and secret checks remain part
    # of apply, where the complete inventory context is available.
    progress("running preflight on all machines")
    preflight = {machine.id: run_preflight(machine) for machine in plan.machines}
    failed = {
        machine_id: [item for item in checks if not item["ok"]]
        for machine_id, checks in preflight.items()
        if any(not item["ok"] for item in checks)
    }
    if failed:
        raise ApplyError(_format_preflight_failure(failed))
    progress("preflight passed")
    if not args.skip_acquire:
        progress("acquiring component checkouts")
        try:
            update_existing = args.yes or args.non_interactive
            if not update_existing:
                answer = input("Existing component checkouts found. Update them from their configured branches? [Y/n]: ").strip().lower()
                update_existing = answer in {"", "y", "yes"}
            acquire_components(plan, project_root, update_existing=update_existing)
        except AcquisitionError as exc:
            raise ApplyError(f"component acquisition failed: {exc}") from exc
    progress("preparing managed inputs")
    private_root = root / "controller"
    secret_root = private_root / "secrets"
    artifact_root = private_root / "chain-artifact"
    if args.resume:
        signer_file = Path(private_state["contract_signer_source"])
        secret_root = Path(private_state["generated_secrets_root"])
        artifact_root = Path(private_state["chain_artifact_root"])
        print(f"[INFO] Reusing managed private inputs from {private_state_file}")
    else:
        wallet_definition = plan.raw.get("secrets", {}).get("master-wallet", {})
        inventory_wallet = Path(wallet_definition["source"]) if wallet_definition.get("source") else None
        if inventory_wallet and not inventory_wallet.is_absolute():
            inventory_wallet = project_root / inventory_wallet
        if not args.master_wallet_file and inventory_wallet and inventory_wallet.is_file():
            args.master_wallet_file = inventory_wallet
            print(f"[INFO] Using master wallet from inventory: {inventory_wallet}")
        default_wallet = project_root / "blockchain" / "master-wallet.txt"
        if not args.master_wallet_file and default_wallet.is_file() and not args.non_interactive:
            if _yes_no(f"Use the existing master wallet at '{default_wallet}'?"):
                args.master_wallet_file = default_wallet
        if not args.master_wallet_file and not args.create_master_wallet and not args.non_interactive:
            args.create_master_wallet = _yes_no("No master wallet was selected. Create one for a new chain?")
        if args.create_master_wallet and not args.master_wallet_file:
            wallet_script = project_root / "blockchain" / "scripts" / "create-master-wallet.sh"
            print("[INFO] Creating master wallet with the blockchain wallet generator...")
            try:
                subprocess.run(["bash", str(wallet_script)], cwd=str(project_root / "blockchain"), check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ApplyError(f"master wallet creation failed: {exc}") from exc
            args.master_wallet_file = project_root / "blockchain" / "master-wallet.txt"
        if not args.master_wallet_file or not Path(args.master_wallet_file).is_file():
            raise ApplyError("installation needs a master wallet; select one or use --create-master-wallet")
        signer_file = args.contract_signer_file
        if not signer_file:
            signer_definition = plan.raw.get("secrets", {}).get("contract-signer", {})
            source = signer_definition.get("source")
            candidate = Path(source) if source else None
            if candidate and not candidate.is_absolute():
                candidate = project_root / candidate
            signer_file = candidate if candidate and candidate.is_file() else None
        if not signer_file:
            use_wallet = args.use_master_wallet_as_signer
            if not use_wallet and not args.non_interactive:
                use_wallet = _yes_no("Use the master wallet as contract signer for this test deployment?")
            if use_wallet:
                signer_file = args.master_wallet_file
        if not signer_file or not Path(signer_file).is_file():
            raise ApplyError("installation needs a contract signer; use --contract-signer-file or approve the master wallet default")
        if not secret_root.exists() or not any(secret_root.iterdir()):
            initialize = args.initialize_managed_secrets
            if not initialize and not args.non_interactive:
                initialize = _yes_no(f"Generate managed application and storage secrets under '{secret_root}'?")
            if not initialize:
                raise ApplyError("managed secrets are required")
            secret_root.mkdir(parents=True, exist_ok=True)
            initialize_greenfield_secrets(plan, secret_root)
            print(f"[OK] Generated managed secrets under {secret_root}")
        supplied_artifact = args.chain_artifact.resolve() if args.chain_artifact else None
        if supplied_artifact and args.new_chain:
            raise ApplyError("--new-chain cannot be combined with --chain-artifact; choose a supplied artifact or generate a new one")
        inventory_artifact = plan.raw.get("blockchain", {}).get("artifact", {}).get("source")
        if not supplied_artifact and inventory_artifact:
            candidate = Path(inventory_artifact)
            supplied_artifact = candidate if candidate.is_absolute() else project_root / candidate
            if not supplied_artifact.is_dir():
                supplied_artifact = None
        if supplied_artifact:
            artifact_root = supplied_artifact
        elif args.new_chain or not (artifact_root / "genesis.json").is_file():
            create_chain = args.new_chain
            if not create_chain and not args.non_interactive:
                create_chain = _yes_no("No blockchain artifact exists. Initialize a new private chain?")
            if not create_chain:
                raise ApplyError("installation needs a chain artifact; use --chain-artifact or approve a new chain")
            _, wallet_address = _wallet_material(Path(args.master_wallet_file))
            address = args.master_wallet_address or wallet_address
            if not address:
                raise ApplyError("the selected wallet has no public address; use --master-wallet-address")
            if args.new_chain and artifact_root.exists():
                # This path is private state owned by this install. It is not
                # an operator-supplied artifact and contains only generated
                # chain material; persistent node data is handled separately.
                shutil.rmtree(artifact_root)
            initialize_chain(plan, artifact_root, address)
            print(f"[OK] Initialized managed chain artifact under {artifact_root}")
    # Site-aware peer maps are operational configuration, not new chain
    # identity. Refresh static nodes from existing public keys before the
    # manifest check/distribution; no genesis or private key is regenerated.
    if plan.raw.get("peerings", {}).get("blockchain"):
        public_keys = {
            node: (artifact_root / "nodes" / node / "key.pub").read_text().strip()
            for node in plan.raw["blockchain"]["nodes"]
        }
        write_static_nodes(artifact_root, public_keys)
    verify_artifact_manifest(artifact_root)
    verify_artifact_compatibility(plan, artifact_root, args.master_wallet_address)
    plan = _private_plan(plan, secret_root=secret_root, wallet_file=Path(args.master_wallet_file), signer_file=Path(signer_file), artifact_root=artifact_root)
    if not args.resume:
        _write_private_input_state(root, wallet=Path(args.master_wallet_file), signer=Path(signer_file), secrets=secret_root, artifact=artifact_root)
    # A fresh install must not reuse a bundle rendered by an earlier code
    # version. Persistent service data remains untouched; only the generated
    # public Compose/config bundle is rebuilt.
    bundle = root / "bundle"
    if bundle.exists() and (not args.resume or args.refresh_bundle):
        # Move the generated bundle out of the active path before removing it.
        # Finder can recreate .DS_Store while rmtree walks a live checkout;
        # the rename keeps the next render independent from that race.
        stale = root / f".bundle-old-{time.time_ns()}"
        bundle.replace(stale)
        shutil.rmtree(stale, ignore_errors=True)
    effective_for_data = _effective_plan(plan, project_root, root)
    existing = existing_chain_data(effective_for_data)
    clean_chain_data = False
    if existing and not args.resume:
        print("[WARNING] Existing persistent deployment data found:")
        print("Managed persistent directories:")
        all_data = persistent_data_inventory(effective_for_data)
        for index, (item, present) in enumerate(all_data, start=1):
            machine, service, service_type, path = item.split(":", maxsplit=3)
            state = "EXISTS - will be deleted" if present else "does not exist - nothing to delete"
            print(f"  {index}. {machine} / {service} ({service_type}) [{state}]")
            print(f"     {path}")
        print("This affects only the selected deployment's persistent data.")
        answer = input("Proceed with this cleanup and create a fresh deployment? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise ApplyError("installation cancelled: existing Besu data was preserved")
        clean_chain_data = True
    progress("applying rendered deployment")
    if args.verbose and not args.resume:
        print("[VERBOSE] compiling contract artifacts (Solidity / solc-js)", flush=True)
    output = apply(
        plan,
        project_root,
        resume=args.resume,
        defer_verification=True,
        clean_chain_data=clean_chain_data,
        clean_empty_network_conflicts=args.clean_empty_network_conflicts,
        prompt_cleanup_empty_network_conflicts=not args.yes and not args.non_interactive,
        verbose=args.verbose,
    )
    progress("running final verification")
    report = _verify_with_retries(plan, project_root, verbose=args.verbose)
    report_path = _write_install_report(root, report, resumed=args.resume)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_verification_summary(report, report_path)
    if not report.get("ok"):
        raise SystemExit(2)
    print(f"[OK] {'Installation resumed' if args.resume else 'Installation complete'} from {output}")


def _print_verification_summary(report: dict, report_path: Path) -> None:
    """Show verification in a compact operator view; keep full JSON on disk."""
    services = report.get("services", {})
    counts = {}
    for item in services.values():
        state = item.get("state", "unknown")
        counts[state] = counts.get(state, 0) + 1
    failed = [name for name, item in services.items() if not item.get("ok", False)]
    result = "OK" if report.get("ok") else "FAILED"
    print(f"Verification: {result} — {report.get('deployment_id', 'unknown')}")
    print(f"Services: {len(services)} total; " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if failed:
        print("Failed services: " + ", ".join(sorted(failed)))
    else:
        print("Checks: all reported services passed")
    by_machine = {}
    for name, item in sorted(services.items()):
        by_machine.setdefault(item.get("machine", "unknown"), []).append((name, item))
    print("Verification details:")
    for machine, entries in sorted(by_machine.items()):
        print(f"  {machine}/")
        for name, item in entries:
            mark = "OK" if item.get("ok") else "FAIL"
            state = item.get("state", "unknown")
            checks = sorted(key for key in item if key not in {"group", "machine", "ok", "state"})
            suffix = f" checks={','.join(checks)}" if checks else ""
            print(f"    [{mark}] {name}: {state}{suffix}")
    print(f"Full JSON report: {report_path}")


def _verify_with_retries(plan, project_root: Path, attempts: int = 120, *, verbose: bool = False) -> dict:
    """Allow newly started containers and migrations a bounded readiness window."""
    report = {}
    for attempt in range(attempts):
        if verbose:
            print(f"[VERBOSE] final verification attempt {attempt + 1}/{attempts}", flush=True)
        report = verify(plan, project_root)
        if report.get("ok"):
            return report
        if attempt + 1 < attempts:
            time.sleep(5)
    return report


def _write_install_report(root: Path, verification: dict, *, resumed: bool) -> Path:
    """Persist a public completion report without copying secret material."""
    root.mkdir(parents=True, exist_ok=True)
    status = json.loads((root / "status.json").read_text(encoding="utf-8")) if (root / "status.json").exists() else {}
    report = {
        "deployment_id": verification.get("deployment_id"),
        "state": "verified" if verification.get("ok") else "failed",
        "resumed": resumed,
        "verification": verification,
        "sources": status.get("sources", {}),
        "readiness": status.get("readiness", {}),
        "chain_artifacts": status.get("chain_artifacts", {}),
    }
    destination = root / "install-report.json"
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _run_inventory_editor(path: Path) -> None:
    """Open the optional terminal UI without changing deployment state."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise InventoryDocumentError(
            "inventory-edit requires an interactive terminal; use inventory-create, "
            "a JSON editor, or the non-interactive deployment commands instead"
        )
    document = InventoryDocument.load(path)
    result = run_textual_editor(document)
    if result.saved:
        message = f"[OK] Saved inventory: {path}"
        if result.backup:
            message += f" (backup: {result.backup})"
        print(message)
    else:
        print("[INFO] Inventory editor closed without saving.")


def _run_operations_console(project_root: Path) -> None:
    """Open the optional deployment operations terminal UI."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise InventoryDocumentError(
            "tui requires an interactive terminal; use deployments, services, logs, "
            "or the non-interactive lifecycle commands instead"
        )
    run_operations_tui(project_root)


def _run_metrics_console(project_root: Path) -> None:
    """Open the read-only metrics panel, which offers no action at all."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise InventoryDocumentError(
            "metrics requires an interactive terminal; use status, deployments or "
            "services for the same state without a panel"
        )
    run_metrics_tui(project_root)


def _resolved_document(path: Path) -> dict:
    """Return v3 for either input format, for read-only CLI inspection."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("format") == "dark-operator-inventory":
        return resolve_inventory_path(path).document
    load_inventory(path)
    return raw


def _inventory_changes(before: dict, after: dict) -> dict:
    """Compact semantic diff intended for operator review, not a JSON patch."""
    result: dict[str, object] = {}
    for key in ("machines", "services", "groups"):
        old, new = set(before.get(key, {})), set(after.get(key, {}))
        result[key] = {
            "added": sorted(new - old),
            "removed": sorted(old - new),
            "changed": sorted(item for item in old & new if before[key][item] != after[key][item]),
        }
    result["changed_sections"] = [key for key in ("deployment", "blockchain", "storage", "infrastructure", "settings", "components", "secrets") if before.get(key) != after.get(key)]
    return result


def _operational_plan(args, project_root: Path):
    if args.deployment:
        return managed_plan(project_root, args.deployment)
    return build_plan(args.inventory)


def _print_table(headers, rows) -> None:
    values = [headers, *rows]
    widths = [max(len(str(row[index])) for row in values) for index in range(len(headers))]
    for index, row in enumerate(values):
        print("  ".join(str(value).ljust(widths[column]) for column, value in enumerate(row)))
        if index == 0:
            print("  ".join("-" * width for width in widths))


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.action == "inventory-create":
            created = create_from_template(args.template, args.output, overwrite=args.overwrite)
            print(f"[OK] Created inventory from {args.template}: {created}")
            if args.edit:
                _run_inventory_editor(created)
            return
        if args.action == "inventory-edit":
            _run_inventory_editor(args.inventory)
            return
        if args.action == "inventory-resolve":
            resolution = resolve_inventory_path(args.inventory)
            if args.output.exists():
                raise ValueError(f"refusing to overwrite existing output: {args.output}")
            args.output.write_text(json.dumps(resolution.document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[OK] Resolved {resolution.metadata['catalog']} to {args.output}")
            for warning in resolution.warnings:
                print(f"[WARN] {warning}")
            return
        if args.action == "inventory-explain":
            resolution = resolve_inventory_path(args.inventory)
            pointer = args.path.rstrip("/") or "/"
            source = resolution.provenance.get(pointer)
            if source is None:
                candidates = [key for key in resolution.provenance if pointer.startswith(key + "/")]
                source = resolution.provenance[max(candidates, key=len)] if candidates else "explicit v3 field or derived recipe detail"
            print(json.dumps({"path": pointer, "source": source, "metadata": resolution.metadata}, indent=2, sort_keys=True))
            return
        if args.action == "inventory-network-matrix":
            raw, machines, services, _ = load_inventory(args.inventory)
            names = {service.id: service for service in services}
            families = (args.traffic,) if args.traffic else ("blockchain", "ipfs", "cluster")
            rows = []
            for family in families:
                for edge in raw.get("peerings", {}).get(family, []):
                    source, target = names[edge["from"]], names[edge["to"]]
                    rows.append({
                        "traffic": family,
                        "from": edge["from"], "from_machine": source.machine_id,
                        "to": edge["to"], "to_machine": target.machine_id,
                        "network": edge["network"], "address": edge["address"], "port": edge["port"],
                        "locality": "same-site" if next(item for item in machines if item.id == source.machine_id).site == next(item for item in machines if item.id == target.machine_id).site else "cross-site",
                    })
            print(json.dumps(rows, indent=2, sort_keys=True))
            return
        if args.action == "inventory-diff":
            before = _resolved_document(args.before)
            after = _resolved_document(args.after)
            changes = _inventory_changes(before, after)
            print(json.dumps(changes, indent=2, sort_keys=True))
            return
        project_root = Path(__file__).resolve().parents[1]
        if args.action == "tui":
            _run_operations_console(project_root)
            return
        if args.action == "metrics":
            _run_metrics_console(project_root)
            return
        if args.action == "metrics-export":
            plan = managed_plan(project_root, args.deployment)
            samples = collect_to_textfile(plan, args.output, include_disk=args.include_disk)
            reachable = sum(item.reachable for item in samples)
            print(f"[OK] Wrote {args.output}: {reachable}/{len(samples)} machine probe(s) reachable.")
            return
        if args.action == "deployments":
            deployments = list_managed_deployments(project_root)
            if args.json:
                print(json.dumps(deployments, indent=2, sort_keys=True))
            else:
                _print_table(("DEPLOYMENT", "STATE", "SERVICES", "RUNNING", "DETAIL"), [
                    (item["deployment_id"], item["state"], item["services"], item["running"], item["detail"] or "-")
                    for item in deployments
                ])
            return
        if args.action == "validate":
            raw, machines, services, _ = load_inventory(args.inventory)
            print(f"[OK] {raw['deployment']['id']}: {len(machines)} machine(s), {len(services)} service(s).")
            source = json.loads(args.inventory.read_text(encoding="utf-8"))
            if source.get("format") == "dark-operator-inventory":
                for warning in resolve_inventory_path(args.inventory).warnings:
                    print(f"[WARN] {warning}")
            availability = analyze_availability(build_plan(args.inventory))
            for warning in availability.warnings:
                print(f"[WARN] {warning}")
            return
        plan = _operational_plan(args, project_root) if args.action in {"services", "logs", "build", "stop", "start", "restart", "recreate", "remove"} else build_plan(args.inventory)
        if args.action == "preflight":
            result = {machine.id: run_preflight(machine) for machine in plan.machines}
            print(json.dumps(result, indent=2, sort_keys=True))
            if not all(check["ok"] for checks in result.values() for check in checks):
                raise SystemExit(2)
            return
        if args.action == "plan":
            availability = analyze_availability(plan)
            value = {"deployment_id": plan.deployment_id, "docker_subnets": plan.docker_subnets, "groups": [group.__dict__ for group in plan.groups], "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints], "steps": [step.__dict__ for step in plan.steps], "availability": asdict(availability)}
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print(f"Deployment: {plan.deployment_id}")
                for group in plan.groups:
                    print(f"group:{group.id}: {group.kind} on {group.machine_id} ({', '.join(group.service_ids)})")
                print(f"availability: validators={availability.validators}, quorum={availability.quorum}, tolerated-validator-losses={availability.validator_failures_tolerated}, primary-rpc={availability.primary_rpc}, chain-copies={availability.blockchain_full_copies}")
                for warning in availability.warnings:
                    print(f"warning: {warning}")
                for step in plan.steps:
                    print(f"{step.id}: {step.description}")
            return
        if args.action == "services":
            services = list_managed_services(plan, project_root)
            if args.json:
                print(json.dumps(services, indent=2, sort_keys=True))
            else:
                headers = ("DEPLOYMENT", "TARGET", "TYPE", "MACHINE", "GROUP", "STATE", "ACTIONS", "DESCRIPTION", "DETAIL")
                values = [
                    (item["deployment_id"], item["target"], item["type"], item["machine"], item["group"] or "-", item["state"], ",".join(item["actions"]) or "-", item["description"], item["detail"])
                    for item in services
                ]
                _print_table(headers, values)
            return
        if args.action == "logs":
            follow_service_logs(plan, project_root, args.target, tail=args.tail)
            return
        if args.action == "install":
            _install(args, plan)
            return
        if args.action in {"build", "stop", "start", "restart", "recreate", "remove"}:
            if args.action == "recreate" and args.service and args.target:
                raise ValueError("use either --service or --target, not both")
            target = args.target or (f"service:{args.service}" if args.action == "recreate" and args.service else None)
            if not target:
                raise ValueError("--target service:ID is required")
            result = manage_service(plan, project_root, args.action, target, build=getattr(args, "build", False), dry_run=args.dry_run)
            print(json.dumps(result, indent=2, sort_keys=True))
            return
        if args.action == "prepare":
            project_root = Path(__file__).resolve().parents[1]
            output = prepare(plan, project_root)
            print(f"[OK] Prepared public deployment bundle at {output}")
            return
        if args.action == "push":
            project_root = Path(__file__).resolve().parents[1]
            output = push(plan, project_root, revision=args.revision)
            print(f"[OK] Pushed public deployment bundle from {output}")
            return
        if args.action in {"apply", "resume"}:
            project_root = Path(__file__).resolve().parents[1]
            output = apply(
                plan,
                project_root,
                resume=args.action == "resume",
                revision=args.revision,
                clean_empty_network_conflicts=args.clean_empty_network_conflicts,
            )
            print(f"[OK] {'Resumed' if args.action == 'resume' else 'Applied'} deployment from {output}")
            return
        if args.action == "status":
            project_root = Path(__file__).resolve().parents[1]
            status_file = project_root / ".generated" / "deployment-v3" / plan.deployment_id / "status.json"
            if not status_file.exists():
                raise ValueError(f"no deployment status found: {status_file}")
            print(status_file.read_text(), end="")
            return
        if args.action == "verify":
            project_root = Path(__file__).resolve().parents[1]
            report = verify(plan, project_root)
            print(json.dumps(report, indent=2, sort_keys=True))
            if not report["ok"]:
                raise SystemExit(2)
            return
        if args.action == "chain-bootstrap":
            artifact_root = write_chain_bootstrap(plan, args.output, args.master_wallet_address)
            print(f"[OK] Wrote public chain bootstrap inputs to {artifact_root}")
            return
        if args.action == "chain-static-nodes":
            try:
                public_keys = json.loads(args.public_keys.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid --public-keys JSON: {exc}") from exc
            if not isinstance(public_keys, dict):
                raise ValueError("--public-keys must contain a JSON object")
            destination = write_static_nodes(args.artifact_root, public_keys)
            print(f"[OK] Wrote static-node lists to {destination}")
            return
        if args.action == "secrets-init":
            created = initialize_greenfield_secrets(plan, args.output, overwrite=args.overwrite)
            print(f"[OK] Created {len(created)} generated secret file(s) under {args.output}")
            print("[INFO] Provision master-wallet and signer secrets separately; they are intentionally not generated.")
            return
        if args.action == "chain-init":
            artifact_root = initialize_chain(plan, args.output, args.master_wallet_address)
            print(f"[OK] Created private QBFT artifact at {artifact_root}")
            return
        if args.action == "chain-export":
            exported = export_chain_group(args.artifact_root, args.group, args.output)
            print(f"[OK] Exported {args.group} chain artifact to {exported}")
            return
        if args.action == "chain-verify":
            files = verify_artifact_manifest(args.artifact_root)
            print(f"[OK] Verified {len(files)} chain artifact file(s) in {args.artifact_root}")
            return
        rendered = render_plan(plan, args.output)
        print(f"[OK] Rendered public plan at {rendered}")
    except (InventoryError, InventoryDocumentError, ExecutionError, ApplyError, ArtifactError, SecretError, VerifyError, SourceError, ValueError) as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc


if __name__ == "__main__":
    main()
