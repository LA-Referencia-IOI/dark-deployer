#!/usr/bin/env bash
# Obsolete fixed-topology interface.
set -euo pipefail

echo "[ERROR] fixed runtime status was removed; use deploy.py status or deploy.py verify with an inventory" >&2
exit 2
