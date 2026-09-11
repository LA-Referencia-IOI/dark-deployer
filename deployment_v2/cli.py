"""CLI implementation for deploy.py. Only validate, plan and render exist initially."""

from __future__ import annotations

import argparse
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
from .runner import ApplyError, apply, push, recreate_service
from .artifacts import ArtifactError, export_chain_role, initialize_chain, verify_artifact_manifest, write_chain_bootstrap, write_static_nodes
from .secrets import SecretError, initialize_greenfield_secrets
from .verify import VerifyError, verify
from .sources import SourceError
from .acquire import AcquisitionError, acquire_components
from .inventory_editor import InventoryDocument, InventoryDocumentError, create_from_template, template_names
from .inventory_editor.textual_app import run_textual_editor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deploy.py", description="dARK declarative deployment v2")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "plan", "render", "preflight", "push", "apply", "resume", "status", "verify", "install", "recreate", "chain-bootstrap", "chain-static-nodes", "chain-init", "chain-export", "chain-verify", "secrets-init", "inventory-edit"):
        command = actions.add_parser(name)
        command.add_argument("--inventory", required=True, type=Path)
        if name == "plan":
            command.add_argument("--json", action="store_true")
        if name == "render":
            command.add_argument("--output", required=True, type=Path)
        if name == "install":
            command.add_argument("--non-interactive", action="store_true")
            command.add_argument("--yes", action="store_true")
            command.add_argument("--resume", action="store_true")
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--master-wallet-address")
            command.add_argument("--master-wallet-file", type=Path)
            command.add_argument("--create-master-wallet", action="store_true",
                                 help="generate blockchain/master-wallet.txt when no wallet file is supplied")
            command.add_argument("--contract-signer-file", type=Path)
            command.add_argument("--chain-artifact", type=Path,
                                 help="existing complete chain artifact; otherwise generate one for a new local chain")
            command.add_argument("--skip-acquire", action="store_true",
                                 help="reuse existing component checkouts instead of cloning/updating them")
        if name == "recreate":
            command.add_argument("--group", required=True)
            command.add_argument("--service", required=True)
            command.add_argument("--build", action="store_true")
        if name == "chain-bootstrap":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--master-wallet-address", required=True)
        if name == "chain-static-nodes":
            command.add_argument("--artifact-root", required=True, type=Path)
            command.add_argument("--public-keys", required=True, type=Path,
                                 help="JSON object mapping validator01..validator04 and rpc01 to public keys")
        if name == "secrets-init":
            command.add_argument("--output", required=True, type=Path,
                                 help="empty secure directory to provision on the destination host")
            command.add_argument("--overwrite", action="store_true")
        if name == "chain-init":
            command.add_argument("--output", required=True, type=Path)
            command.add_argument("--master-wallet-address", required=True)
        if name == "chain-export":
            command.add_argument("--artifact-root", required=True, type=Path)
            command.add_argument("--role", required=True, choices=("rpc", "validators-a", "validators-b"))
            command.add_argument("--output", required=True, type=Path)
        if name == "chain-verify":
            command.add_argument("--artifact-root", required=True, type=Path)
    create = actions.add_parser("inventory-create", help="create an inventory from a maintained template")
    create.add_argument("--template", required=True, choices=template_names())
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--overwrite", action="store_true")
    create.add_argument("--edit", action="store_true", help="open the new inventory in the interactive editor")
    return parser


def _install(args: argparse.Namespace, plan) -> None:
    """Run the normal installation path with only essential operator input.

    Low-level commands remain available for advanced operations; this helper
    deliberately coordinates them and keeps generated private material inside
    the deployment run directory.
    """
    project_root = Path(__file__).resolve().parents[1]
    root = project_root / ".generated" / "deployment-v2" / plan.deployment_id
    if args.dry_run:
        print(f"Deployment: {plan.deployment_id}")
        print("Mode: " + ", ".join(f"{m.id}={m.execution}" for m in plan.machines))
        for step in plan.steps:
            print(f"{step.id}: {step.description}")
        return
    if not args.yes and not args.non_interactive:
        answer = input(f"Install deployment '{plan.deployment_id}' now? [Y/n]: ").strip().lower()
        if answer not in {"", "y", "yes"}:
            print("[INFO] Installation cancelled.")
            return
    # Fail before rendering or starting any group when a destination cannot
    # execute the required Docker/Compose toolchain.  This is deliberately a
    # read-only preflight; filesystem creation and secret checks remain part
    # of apply, where the complete inventory context is available.
    preflight = {machine.id: run_preflight(machine) for machine in plan.machines}
    failed = {
        machine_id: [item for item in checks if not item["ok"]]
        for machine_id, checks in preflight.items()
        if any(not item["ok"] for item in checks)
    }
    if failed:
        raise ApplyError("installation preflight failed: " + json.dumps(failed, sort_keys=True))
    if not args.skip_acquire:
        try:
            update_existing = args.yes or args.non_interactive
            if not update_existing:
                answer = input("Existing component checkouts found. Update them from their configured branches? [Y/n]: ").strip().lower()
                update_existing = answer in {"", "y", "yes"}
            acquire_components(plan, project_root, update_existing=update_existing)
        except AcquisitionError as exc:
            raise ApplyError(f"component acquisition failed: {exc}") from exc
    # Apply uses a staged local destination for local machines. Prepare the
    # same private tree it will consume before invoking the runner.
    local_machines = [m for m in plan.machines if m.execution == "local"]
    if not args.master_wallet_file:
        wallet_definition = plan.raw.get("secrets", {}).get("master-wallet", {})
        source_path = wallet_definition.get("source_path")
        if source_path:
            candidate = Path(source_path)
            if not candidate.is_absolute():
                candidate = project_root / candidate
            if candidate.is_file():
                print(f"[INFO] Master wallet found at inventory path: {candidate}")
                if args.non_interactive or args.yes:
                    args.master_wallet_file = candidate
                else:
                    answer = input(f"Use the master wallet at '{candidate}'? [Y/n]: ").strip().lower()
                    if answer in {"", "y", "yes"}:
                        args.master_wallet_file = candidate
            elif args.create_master_wallet:
                print(f"[INFO] Inventory wallet path does not exist yet: {candidate}")
                args.master_wallet_file = candidate
    if args.create_master_wallet and (
        not args.master_wallet_file or not Path(args.master_wallet_file).is_file()
    ):
        wallet_script = project_root / "blockchain" / "scripts" / "create-master-wallet.sh"
        wallet_file = args.master_wallet_file or (project_root / "blockchain" / "master-wallet.txt")
        print("[INFO] Creating master wallet with the blockchain wallet generator...")
        try:
            subprocess.run(["bash", str(wallet_script)], cwd=str(project_root / "blockchain"), check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ApplyError(f"master wallet creation failed: {exc}") from exc
        generated = project_root / "blockchain" / "master-wallet.txt"
        if wallet_file != generated:
            wallet_file.parent.mkdir(parents=True, exist_ok=True)
            if wallet_file.exists():
                raise ApplyError(f"refusing to replace existing wallet: {wallet_file}")
            shutil.copy2(generated, wallet_file)
        args.master_wallet_file = wallet_file
        print(f"[OK] Master wallet created: {wallet_file}")
    # In the common local setup one master-wallet.txt supplies both runtime
    # credentials. Explicit signer/address arguments still take precedence.
    signer_file = args.contract_signer_file or args.master_wallet_file
    for machine in local_machines:
        secret_root = root / "local" / machine.id / "secrets"
        secret_root.mkdir(parents=True, exist_ok=True)
        if not any(secret_root.iterdir()):
            initialize_greenfield_secrets(plan, secret_root)
        for secret_id, supplied in (("master-wallet", args.master_wallet_file), ("contract-signer", signer_file)):
            if supplied:
                if not supplied.is_file():
                    raise ApplyError(f"secret file not found for {secret_id}: {supplied}")
                definition = plan.raw["secrets"].get(secret_id)
                if definition:
                    target = secret_root / definition["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    raw = supplied.read_text(encoding="utf-8").strip()
                    match = re.search(r"Private Key\s*:\s*(0x[0-9a-fA-F]{64})", raw)
                    if match:
                        raw = match.group(1)
                    elif not re.fullmatch(r"0x?[0-9a-fA-F]{64}", raw):
                        raise ApplyError(f"secret file for {secret_id} is not a raw key or supported master-wallet.txt: {supplied}")
                    value = (raw + "\n").encode("ascii")
                    if target.exists() and target.read_bytes() != value:
                        raise ApplyError(f"refusing to replace existing secret: {target}")
                    if not target.exists():
                        target.write_bytes(value)
                        target.chmod(0o600)
                    if secret_id == "contract-signer":
                        runtime_def = plan.raw["secrets"].get("minter-runtime-env")
                        if runtime_def:
                            runtime = secret_root / runtime_def["path"]
                            existing = runtime.read_text(encoding="utf-8") if runtime.exists() else ""
                            if "DARK_ADMIN_PRIVATE_KEY=" not in existing:
                                runtime.write_text(existing.rstrip("\n") + "\nDARK_ADMIN_PRIVATE_KEY=" + raw + "\n", encoding="utf-8")
                                runtime.chmod(0o600)
        artifact_rel = Path(plan.raw["blockchain"]["artifact"]["path"])
        artifact_root = secret_root / artifact_rel
        if args.chain_artifact:
            source = args.chain_artifact.resolve()
            if not source.is_dir():
                raise ApplyError(f"chain artifact directory not found: {source}")
            if not artifact_root.exists():
                artifact_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, artifact_root)
        if not (artifact_root / "genesis.json").exists():
            address = args.master_wallet_address
            if not address and args.master_wallet_file:
                wallet_text = args.master_wallet_file.read_text(encoding="utf-8")
                address_match = re.search(r"Address\s*:\s*(0x[0-9a-fA-F]{40})", wallet_text)
                if address_match:
                    address = address_match.group(1)
            if not address and not args.non_interactive:
                print("[INFO] No master wallet address is configured.")
                print("       Re-run with --create-master-wallet to generate one automatically.")
                address = input("Master wallet public address for a new chain (0x...): ").strip()
            if not address:
                raise ApplyError("missing master wallet address; use --master-wallet-address or provision a chain artifact")
            initialize_chain(plan, artifact_root, address)
        else:
            verify_artifact_manifest(artifact_root)
    output = apply(plan, project_root, resume=args.resume, defer_verification=True)
    report = _verify_with_retries(plan, project_root)
    _write_install_report(root, report, resumed=args.resume)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report.get("ok"):
        raise SystemExit(2)
    print(f"[OK] {'Installation resumed' if args.resume else 'Installation complete'} from {output}")


def _verify_with_retries(plan, project_root: Path, attempts: int = 120) -> dict:
    """Allow newly started containers and migrations a bounded readiness window."""
    report = {}
    for attempt in range(attempts):
        report = verify(plan, project_root)
        if report.get("ok"):
            return report
        if attempt + 1 < attempts:
            time.sleep(5)
    return report


def _write_install_report(root: Path, verification: dict, *, resumed: bool) -> Path:
    """Persist a public completion report without copying secret material."""
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "deployment_id": verification.get("deployment_id"),
        "state": "verified" if verification.get("ok") else "failed",
        "resumed": resumed,
        "verification": verification,
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
        if args.action == "validate":
            raw, machines, groups, _ = load_inventory(args.inventory)
            print(f"[OK] {raw['deployment']['id']}: {len(machines)} machine(s), {len(groups)} group(s).")
            return
        plan = build_plan(args.inventory)
        if args.action == "preflight":
            result = {machine.id: run_preflight(machine) for machine in plan.machines}
            print(json.dumps(result, indent=2, sort_keys=True))
            if not all(check["ok"] for checks in result.values() for check in checks):
                raise SystemExit(2)
            return
        if args.action == "plan":
            value = {"deployment_id": plan.deployment_id, "docker_subnets": plan.docker_subnets, "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints], "steps": [step.__dict__ for step in plan.steps]}
            if args.json:
                print(json.dumps(value, indent=2, sort_keys=True))
            else:
                print(f"Deployment: {plan.deployment_id}")
                for step in plan.steps:
                    print(f"{step.id}: {step.description}")
            return
        if args.action == "install":
            _install(args, plan)
            return
        if args.action == "recreate":
            project_root = Path(__file__).resolve().parents[1]
            recreate_service(plan, project_root, args.group, args.service, build=args.build)
            print(f"[OK] Recreated service {args.service} in group {args.group}")
            return
        if args.action == "push":
            project_root = Path(__file__).resolve().parents[1]
            output = push(plan, project_root)
            print(f"[OK] Pushed public deployment bundle from {output}")
            return
        if args.action in {"apply", "resume"}:
            project_root = Path(__file__).resolve().parents[1]
            output = apply(plan, project_root, resume=args.action == "resume")
            print(f"[OK] {'Resumed' if args.action == 'resume' else 'Applied'} deployment from {output}")
            return
        if args.action == "status":
            project_root = Path(__file__).resolve().parents[1]
            status_file = project_root / ".generated" / "deployment-v2" / plan.deployment_id / "status.json"
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
            exported = export_chain_role(args.artifact_root, args.role, args.output)
            print(f"[OK] Exported {args.role} chain artifact to {exported}")
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
