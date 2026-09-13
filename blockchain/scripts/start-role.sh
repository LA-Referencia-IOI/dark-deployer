#!/usr/bin/env bash
# Obsolete fixed-role interface. The deployment plan starts resolved groups.
set -euo pipefail

echo "[ERROR] fixed runtime roles were removed; use deploy.py install/apply with an operator v2 inventory" >&2
exit 2
