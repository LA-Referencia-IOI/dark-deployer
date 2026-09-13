#!/usr/bin/env bash
# Obsolete fixed-role interface. Dynamic exports are inventory-driven.
set -euo pipefail

echo "[ERROR] fixed role exports were removed; use: deploy.py chain-export --inventory INVENTORY --artifact-root PATH --group GROUP --output PATH" >&2
exit 2
