#!/usr/bin/env bash
set -euo pipefail

REPOSITORY='https://github.com/LA-Referencia-IOI/dark-aws-cloudfront.git'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHECKOUT="$ROOT/.generated/dark-aws-cloudfront"
TARGET="$SCRIPT_DIR/cloudformation"
EXPECTED_LINK='../../.generated/dark-aws-cloudfront/infrastructure/aws/cloudformation'

command -v git >/dev/null 2>&1 || {
  echo 'git is required to fetch dark-aws-cloudfront' >&2
  exit 1
}

mkdir -p "$ROOT/.generated"

if [[ -e "$CHECKOUT" ]]; then
  if [[ ! -d "$CHECKOUT/.git" && ! -f "$CHECKOUT/.git" ]]; then
    echo "refusing to overwrite non-Git path: $CHECKOUT" >&2
    exit 1
  fi
  ORIGIN="$(git -C "$CHECKOUT" remote get-url origin)"
  [[ "$ORIGIN" == "$REPOSITORY" ]] || {
    echo "refusing to pull unexpected repository: $ORIGIN" >&2
    exit 1
  }
  git -C "$CHECKOUT" pull --ff-only origin main
else
  git clone --branch main --single-branch "$REPOSITORY" "$CHECKOUT"
fi

SOURCE="$CHECKOUT/infrastructure/aws/cloudformation"
[[ -d "$SOURCE" ]] || {
  echo "CloudFormation directory missing from fetched repository: $SOURCE" >&2
  exit 1
}

if [[ -L "$TARGET" ]]; then
  CURRENT_LINK="$(readlink "$TARGET")"
  [[ "$CURRENT_LINK" == "$EXPECTED_LINK" ]] || {
    echo "refusing to replace unexpected symlink $TARGET -> $CURRENT_LINK" >&2
    exit 1
  }
elif [[ -e "$TARGET" ]]; then
  BACKUP_PARENT="$ROOT/.generated/aws"
  BACKUP="$BACKUP_PARENT/cloudformation-local-backup-$(date -u '+%Y%m%dT%H%M%SZ')"
  mkdir -p "$BACKUP_PARENT"
  [[ ! -e "$BACKUP" ]] || {
    echo "backup path already exists: $BACKUP" >&2
    exit 1
  }
  mv "$TARGET" "$BACKUP"
  ln -s "$EXPECTED_LINK" "$TARGET"
  echo "Previous local directory preserved at: $BACKUP"
else
  ln -s "$EXPECTED_LINK" "$TARGET"
fi

echo "CloudFormation checkout: $CHECKOUT"
echo "CloudFormation revision: $(git -C "$CHECKOUT" rev-parse --short HEAD)"
