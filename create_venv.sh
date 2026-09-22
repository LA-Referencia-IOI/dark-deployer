#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: create_venv.sh [--python PYTHON] [--venv PATH] [--with-tui]

Creates the deployer's virtual environment and installs requirements.txt.
Use --with-tui to install the optional Textual dependencies as well.
Defaults: Python 3.14 and venv/.
EOF
  exit 2
}

PYTHON_BIN="${PYTHON_BIN:-python3.14}"
VENV_DIR="${VENV_DIR:-venv}"
WITH_TUI=0

while (($#)); do
  case "$1" in
    --python) [[ $# -ge 2 ]] || usage; PYTHON_BIN=$2; shift 2 ;;
    --venv) [[ $# -ge 2 ]] || usage; VENV_DIR=$2; shift 2 ;;
    --with-tui) WITH_TUI=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

command -v "$PYTHON_BIN" >/dev/null || {
  echo "Python executable not found: $PYTHON_BIN" >&2
  echo 'Use --python PYTHON to select another supported interpreter.' >&2
  exit 1
}

if [ -x "$VENV_DIR/bin/python" ]; then
  echo "using existing virtual environment: $VENV_DIR"
else
  echo "creating virtual environment: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -r requirements.txt
if [ "$WITH_TUI" -eq 1 ]; then
  "$VENV_DIR/bin/python" -m pip install -r requirements-tui.txt
fi

echo "virtual environment ready: $VENV_DIR"
