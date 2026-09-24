#!/usr/bin/env bash
# Uploads an SSH private key to the running apps host through SSM.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: upload_pem_ssm_apps.sh --source FILE [--target FILE]
                              [--region REGION] [--profile PROFILE]
                              [--deployment DEPLOYMENT]

Uploads a PEM through AWS Systems Manager to the single running apps host.
The remote file defaults to /home/ubuntu/<source-basename>, is owned by
ubuntu, and has mode 600.
EOF
  exit 2
}

DEPLOYMENT='dark2-prod-aws'
REGION='us-east-1'
PROFILE=''
SOURCE=''
TARGET=''
ROLE='apps'

while (($#)); do
  case "$1" in
    --source) [[ $# -ge 2 ]] || usage; SOURCE=$2; shift 2 ;;
    --target) [[ $# -ge 2 ]] || usage; TARGET=$2; shift 2 ;;
    --region) [[ $# -ge 2 ]] || usage; REGION=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || usage; PROFILE=$2; shift 2 ;;
    --deployment) [[ $# -ge 2 ]] || usage; DEPLOYMENT=$2; shift 2 ;;
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
command -v base64 >/dev/null || { echo 'base64 is required in PATH' >&2; exit 1; }
[ -n "$SOURCE" ] || { echo '--source FILE is required' >&2; usage; }
[ -r "$SOURCE" ] || { echo "PEM is not readable: $SOURCE" >&2; exit 1; }
[ -n "$TARGET" ] || TARGET="/home/ubuntu/$(basename "$SOURCE")"

aws_args=(--region "$REGION")
[[ -n "$PROFILE" ]] && aws_args+=(--profile "$PROFILE")

instances=()
while IFS= read -r instance_id; do
  [ -n "$instance_id" ] || continue
  [ "$instance_id" = 'None' ] && continue
  instances[${#instances[@]}]="$instance_id"
done < <("$aws_bin" "${aws_args[@]}" ec2 describe-instances \
  --filters \
    "Name=tag:dARKDeployment,Values=$DEPLOYMENT" \
    "Name=tag:dARKRole,Values=$ROLE" \
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

# Keep the key out of the terminal output. It is sent as base64 only inside
# the SSM command and written with restrictive permissions on the host.
payload=$(base64 < "$SOURCE" | tr -d '\n')
target_dir=$(dirname "$TARGET")
remote_command="install -d -m 700 -o ubuntu -g ubuntu '$target_dir' && printf '%s' '$payload' | base64 -d > '$TARGET' && chown ubuntu:ubuntu '$TARGET' && chmod 600 '$TARGET'"
parameters_json='{"commands":["'
parameters_json+="$remote_command"
parameters_json+='"]}'

command_id=$("$aws_bin" "${aws_args[@]}" ssm send-command \
  --instance-ids "${instances[0]}" \
  --document-name AWS-RunShellScript \
  --comment 'upload dark deployer SSH key' \
  --parameters "$parameters_json" \
  --query 'Command.CommandId' \
  --output text)

echo "SSM command submitted: $command_id"
echo "target: ${instances[0]}:$TARGET"
echo 'Check completion with aws ssm get-command-invocation if needed.'
