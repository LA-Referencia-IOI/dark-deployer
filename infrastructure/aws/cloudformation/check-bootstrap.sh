#!/usr/bin/env bash
# Verifies that the dark2-prod-aws bootstrap finished on every host.
# The bootstrap only writes the stamp /var/lib/dark/bootstrap-complete after
# all its checks pass; this script reads it via SSM Run Command on every
# running EC2 tagged dARKDeployment=dark2-prod-aws. Read-only.
#
# Operator permissions required: ec2:DescribeInstances, ssm:SendCommand,
# ssm:GetCommandInvocation.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: check-bootstrap.sh [--minimal] [--region REGION] [--profile PROFILE]
                          [--timeout SECONDS]

Checks the bootstrap stamp on the dark2-prod-aws stack via SSM: expects the
six hosts of the full stack, or the single apps host with --minimal. Fails if
any host is missing the stamp or does not answer SSM.
EOF
  exit 2
}

DEPLOYMENT='dark2-prod-aws'
EXPECT=6
REGION=''
PROFILE=''
TIMEOUT=120

while (($#)); do
  case "$1" in
    --minimal) EXPECT=1; shift ;;
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    --timeout) [[ $# -ge 2 ]] || usage; TIMEOUT=$2; shift 2 ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

aws_bin=''
for candidate in "${AWS_CLI:-}" /opt/homebrew/bin/aws "$(command -v aws 2>/dev/null || true)" /usr/local/bin/aws; do
  [[ -n "$candidate" && -x "$candidate" ]] || continue
  # Validate the executable instead of assuming a platform-specific path.
  # This accepts package-managed Linux installations and rejects an
  # incompatible x86_64 binary on Apple Silicon.
  if "$candidate" --version >/dev/null 2>&1; then
    aws_bin="$candidate"
    break
  fi
done
if [[ -z "$aws_bin" ]]; then
  echo 'a native AWS CLI for this machine is required (on Apple Silicon: brew install awscli)' >&2
  exit 1
fi

aws_args=()
[[ -n "$REGION" ]] && aws_args+=(--region "$REGION")
[[ -n "$PROFILE" ]] && aws_args+=(--profile "$PROFILE")

instances="$("$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ec2 describe-instances \
  --filters "Name=tag:dARKDeployment,Values=$DEPLOYMENT" \
            'Name=instance-state-name,Values=running' \
  --query 'Reservations[].Instances[].InstanceId' --output text)" || exit 1

ids=($instances)
count=${#ids[@]}
if [ "$count" -ne "$EXPECT" ]; then
  echo "expected $EXPECT running host(s) tagged dARKDeployment=$DEPLOYMENT, found $count" >&2
  [ "$count" -gt 0 ] || { echo 'is the stack deployed? wrong --region/--profile?' >&2; }
  exit 1
fi
echo "checking $count host(s) of $DEPLOYMENT"

command_id="$("$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ssm send-command \
  --document-name AWS-RunShellScript \
  --instance-ids ${ids[@]+"${ids[@]}"} \
  --comment 'dark bootstrap stamp check' \
  --parameters '{"commands": ["if [ -f /var/lib/dark/bootstrap-complete ]; then cat /var/lib/dark/bootstrap-complete; else echo MISSING:bootstrap-complete; exit 1; fi"]}' \
  --query 'Command.CommandId' --output text)" || exit 1
echo "ssm command: $command_id"

overall=0
for id in "${ids[@]}"; do
  status='Pending'
  waited=0
  while :; do
    status="$("$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ssm get-command-invocation \
      --command-id "$command_id" --instance-id "$id" \
      --query 'Status' --output text 2>/dev/null || echo Unknown)"
    case "$status" in
      Success|Failed|TimedOut|Cancelled) break ;;
    esac
    if [ "$waited" -ge "$TIMEOUT" ]; then
      status='TimedOut'
      break
    fi
    sleep 5
    waited=$((waited + 5))
  done
  out="$("$aws_bin" ${aws_args[@]+"${aws_args[@]}"} ssm get-command-invocation \
    --command-id "$command_id" --instance-id "$id" \
    --query 'StandardOutputContent' --output text 2>/dev/null || true)"
  if [ "$status" = 'Success' ]; then
    echo "OK     $id  $(echo "$out" | tr '\n' ' ')"
  else
    echo "FAIL   $id  status=$status  $(echo "$out" | tr '\n' ' ')"
    overall=1
  fi
done

if [ "$overall" -eq 0 ]; then
  echo "bootstrap complete on all $count host(s)"
else
  echo 'some host(s) did not finish the bootstrap' >&2
fi
exit "$overall"
