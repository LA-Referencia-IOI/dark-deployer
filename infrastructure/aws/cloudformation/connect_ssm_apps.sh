#!/usr/bin/env bash
# Open an interactive Session Manager shell on the running apps host.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: connect_ssm_apps.sh [--deployment NAME] [--region REGION] [--profile PROFILE]

Finds the single running EC2 instance tagged dARKRole=apps and opens an
interactive AWS Systems Manager session to it.
EOF
  exit 2
}

DEPLOYMENT='dark2-prod-aws'
REGION=''
PROFILE=''

while (($#)); do
  case "$1" in
    --deployment) [[ $# -ge 2 ]] || usage; DEPLOYMENT=$2; shift 2 ;;
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

aws_bin=''
for candidate in "${AWS_CLI:-}" /opt/homebrew/bin/aws "$(command -v aws 2>/dev/null || true)" /usr/local/bin/aws; do
  [[ -n "$candidate" && -x "$candidate" ]] || continue
  if "$candidate" --version >/dev/null 2>&1; then
    aws_bin="$candidate"
    break
  fi
done
[[ -n "$aws_bin" ]] || { echo 'a native AWS CLI is required (on Apple Silicon: brew install awscli)' >&2; exit 1; }
command -v session-manager-plugin >/dev/null || {
  echo 'session-manager-plugin is required (macOS: brew install --cask session-manager-plugin)' >&2
  exit 1
}

aws_args=()
[[ -n "$REGION" ]] && aws_args+=(--region "$REGION")
[[ -n "$PROFILE" ]] && aws_args+=(--profile "$PROFILE")

instances="$("$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ec2 describe-instances \
  --filters "Name=tag:dARKDeployment,Values=$DEPLOYMENT" \
            'Name=tag:dARKRole,Values=apps' \
            'Name=instance-state-name,Values=running' \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
ids=($instances)
if [[ "${#ids[@]}" -ne 1 ]]; then
  echo "expected exactly one running apps host for dARKDeployment=$DEPLOYMENT, found ${#ids[@]}" >&2
  exit 1
fi

echo "Opening Session Manager session on ${ids[0]} ($DEPLOYMENT)"
exec "$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ssm start-session --target "${ids[0]}"
