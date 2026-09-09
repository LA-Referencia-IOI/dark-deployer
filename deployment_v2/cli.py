"""CLI implementation for deploy.py. Only validate, plan and render exist initially."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inventory import InventoryError, load_inventory
from .executor import ExecutionError, run_preflight
from .planner import build_plan
from .render import render_plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deploy.py", description="dARK declarative deployment v2")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "plan", "render", "preflight"):
        command = actions.add_parser(name)
        command.add_argument("--inventory", required=True, type=Path)
        if name == "plan":
            command.add_argument("--json", action="store_true")
        if name == "render":
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
        rendered = render_plan(plan, args.output)
        print(f"[OK] Rendered public plan at {rendered}")
    except (InventoryError, ExecutionError, ValueError) as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc


if __name__ == "__main__":
    main()
