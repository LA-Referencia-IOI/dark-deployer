"""CLI implementation for deploy.py. Only validate, plan and render exist initially."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inventory import InventoryError, load_inventory
from .executor import ExecutionError, run_preflight
from .planner import build_plan
from .render import render_plan
from .runner import ApplyError, apply
from .artifacts import ArtifactError, export_chain_role, initialize_chain, write_chain_bootstrap, write_static_nodes
from .secrets import SecretError, initialize_greenfield_secrets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deploy.py", description="dARK declarative deployment v2")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "plan", "render", "preflight", "apply", "resume", "status", "chain-bootstrap", "chain-static-nodes", "chain-init", "chain-export", "secrets-init"):
        command = actions.add_parser(name)
        command.add_argument("--inventory", required=True, type=Path)
        if name == "plan":
            command.add_argument("--json", action="store_true")
        if name == "render":
            command.add_argument("--output", required=True, type=Path)
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
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
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
        rendered = render_plan(plan, args.output)
        print(f"[OK] Rendered public plan at {rendered}")
    except (InventoryError, ExecutionError, ApplyError, ArtifactError, SecretError, ValueError) as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc


if __name__ == "__main__":
    main()
