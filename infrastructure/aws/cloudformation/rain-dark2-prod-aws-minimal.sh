#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  rain-dark2-prod-aws-minimal.sh --config FILE [--region REGION] [--profile PROFILE]
                                  [--stack STACK] [--plan|--apply]

Uses the same dark2-prod-aws parameter file, but creates only the VPC, one
apps EC2 with its data volume and bootstrap, plus the Minter ALB/DNS path.
--plan creates a change set only; --apply executes it.
EOF
  exit 2
}

CONFIG=''
REGION=''
PROFILE=''
STACK='dark2-prod-aws-minimal'
MODE='plan'

while (($#)); do
  case "$1" in
    --config) [[ $# -ge 2 ]] || usage; CONFIG=$2; shift 2 ;;
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    --stack) [[ $# -ge 2 ]] || usage; STACK=$2; shift 2 ;;
    --plan) MODE='plan'; shift ;;
    --apply) MODE='apply'; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

[[ -n "$CONFIG" ]] || { echo '--config is required' >&2; usage; }
[[ -f "$CONFIG" ]] || { echo "Rain config not found: $CONFIG" >&2; exit 1; }
command -v rain >/dev/null || { echo 'rain is required in PATH' >&2; exit 1; }

template="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dark2-prod-aws-minimal.yaml"
args=(deploy "$template" "$STACK" --config "$CONFIG" --no-analytics)
[[ -n "$REGION" ]] && args+=(--region "$REGION")
[[ -n "$PROFILE" ]] && args+=(--profile "$PROFILE")
if [[ "$MODE" == 'plan' ]]; then
  args+=(--no-exec)
else
  args+=(--yes)
fi
exec rain "${args[@]}"
