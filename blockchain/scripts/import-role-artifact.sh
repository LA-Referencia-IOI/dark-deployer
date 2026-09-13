#!/usr/bin/env bash
# Obsolete fixed-role interface. Install distributes inventory group artifacts.
set -euo pipefail

echo "[ERROR] fixed role imports were removed; use deploy.py install with an operator v2 inventory" >&2
exit 2
