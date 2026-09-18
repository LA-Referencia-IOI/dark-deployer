#!/usr/bin/env bash
# Opens an AWS Systems Manager session on the running apps host.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: connect_ssm_apps.sh [--region REGION] [--profile PROFILE]
                          [--deployment DEPLOYMENT]

Finds the single running EC2 instance tagged dARKRole=apps for the deployment
and opens an interactive Session Manager session.
EOF
  exit 2
}

# Defaults for the active AWS deployment. Command-line options can override
# these values when connecting to another account, region, or stack.
DEPLOYMENT='dark2-prod-aws'
REGION='us-east-1'
ROLE='apps'
INSTANCE_STATE='running'
PROFILE=''

while (($#)); do
  case "$1" in
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    --deployment) [[ $# -ge 2 ]] || usage; DEPLOYMENT=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

command -v aws >/dev/null || { echo 'aws is required in PATH' >&2; exit 1; }

aws_args=(--region "$REGION")
[[ -n "$PROFILE" ]] && aws_args+=(--profile "$PROFILE")

instances=()
while IFS= read -r instance_id; do
  [ -n "$instance_id" ] || continue
  [ "$instance_id" = 'None' ] && continue
  instances[${#instances[@]}]="$instance_id"
done < <(aws "${aws_args[@]}" ec2 describe-instances \
  --filters \
    "Name=tag:dARKDeployment,Values=$DEPLOYMENT" \
    "Name=tag:dARKRole,Values=$ROLE" \
    "Name=instance-state-name,Values=$INSTANCE_STATE" \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text | tr '\t' '\n')

if ((${#instances[@]} == 0)); then
  echo "no running apps instance found for dARKDeployment=$DEPLOYMENT in $REGION" >&2
  exit 1
fi
if ((${#instances[@]} != 1)); then
  printf 'expected exactly one running apps instance, found: %s\n' "${instances[*]}" >&2
  exit 1
fi

echo "connecting to apps instance ${instances[0]}"
exec aws "${aws_args[@]}" ssm start-session --target "${instances[0]}"
