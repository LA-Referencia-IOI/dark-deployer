#!/usr/bin/env bash
# Obsolete fixed-topology interface.
set -euo pipefail

echo "[ERROR] fixed-node reset was removed; use deploy.py recreate or explicitly remove data for a declared inventory service" >&2
exit 2
