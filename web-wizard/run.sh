#!/usr/bin/env bash
# Local, read-only operator workbench launcher.
#
# Everything this script does happens inside web-wizard/: it bootstraps a
# private virtualenv from web-wizard/requirements.txt so the deployer's own
# environment is never touched. It never runs SSH, Docker or Git.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
venv="$here/.venv"

# Pick the newest usable interpreter for the workbench's own venv. Preference
# order: an explicit DARK_PYTHON, then python3.13..3.10, then the deployer's own
# venv interpreter (Homebrew 3.12 on macOS), then the system python3.
choose_python() {
  if [ -n "${DARK_PYTHON:-}" ]; then
    printf '%s\n' "$DARK_PYTHON"
    return
  fi
  local candidate resolved
  for candidate in python3.13 python3.12 python3.11 python3.10 \
                   "$repo/venv/bin/python" python3 python3.9; do
    if [ -x "$candidate" ]; then
      resolved="$candidate"
    elif command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
    else
      continue
    fi
    # Only accept an interpreter new enough to run the workbench.
    if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$resolved"
      return
    fi
  done
}

if [ ! -x "$venv/bin/python" ]; then
  base="$(choose_python || true)"
  if [ -z "$base" ]; then
    echo "[web-wizard] no python3 found; set DARK_PYTHON to a python interpreter" >&2
    exit 1
  fi
  echo "[web-wizard] creating local virtualenv at $venv using $base"
  "$base" -m venv "$venv"
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  echo "[web-wizard] installing web-wizard/requirements.txt (deployer untouched)"
  "$venv/bin/python" -m pip install --quiet -r "$here/requirements.txt"
fi

# Keep the caller's working directory so a relative --inventory resolves exactly
# as typed; put the package on sys.path instead of cd-ing into it.
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" exec "$venv/bin/python" -m webwizard "$@"
