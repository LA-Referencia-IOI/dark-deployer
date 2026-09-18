#!/usr/bin/env bash
# Forwards the Dashboard container port through SSM to macOS localhost.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: forward_admin_ssm_apps.sh [--region REGION] [--profile PROFILE]
                                [--deployment DEPLOYMENT]
                                [--remote-port PORT] [--local-port PORT]

Discovers the Dashboard container IP and opens an interactive SSM port
forward from that container to localhost.
Defaults: deployment dark2-prod-aws, region us-east-1, remote port 8080,
local port 8081, and Dashboard container name derived from the deployment.
EOF
  exit 2
}

DEPLOYMENT='dark2-prod-aws'
REGION='us-east-1'
PROFILE=''
REMOTE_PORT='8080'
LOCAL_PORT='8081'

while (($#)); do
  case "$1" in
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    --deployment) [[ $# -ge 2 ]] || usage; DEPLOYMENT=$2; shift 2 ;;
    --remote-port) [[ $# -ge 2 ]] || usage; REMOTE_PORT=$2; shift 2 ;;
    --local-port) [[ $# -ge 2 ]] || usage; LOCAL_PORT=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

CONTAINER="${DEPLOYMENT}-apps-apps-dashboard-1"

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
    'Name=tag:dARKRole,Values=apps' \
    'Name=instance-state-name,Values=running' \
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

command_id=$(aws "${aws_args[@]}" ssm send-command \
  --instance-ids "${instances[0]}" \
  --document-name AWS-RunShellScript \
  --comment 'discover dashboard container address' \
  --parameters "{\"commands\":[\"docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' '$CONTAINER'\"]}" \
  --query 'Command.CommandId' --output text)

remote_host=''
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
  remote_host=$(aws "${aws_args[@]}" ssm get-command-invocation \
    --command-id "$command_id" --instance-id "${instances[0]}" \
    --query 'StandardOutputContent' --output text 2>/dev/null | tr -d '[:space:]' || true)
  case "$remote_host" in
    172.*|10.*|192.168.*) break ;;
    *) remote_host='' ;;
  esac
  sleep 2
done

[ -n "$remote_host" ] || {
  echo "could not discover Dashboard container IP ($CONTAINER)" >&2
  exit 1
}

echo "forwarding localhost:$LOCAL_PORT -> $remote_host:$REMOTE_PORT"
exec aws "${aws_args[@]}" ssm start-session \
  --target "${instances[0]}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$remote_host\"],\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
