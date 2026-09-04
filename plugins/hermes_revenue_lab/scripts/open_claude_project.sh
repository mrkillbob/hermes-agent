#!/usr/bin/env bash
set -euo pipefail

readonly LAB_ROOT="/Users/mikedemott/HermesRevenueLab"
readonly VAULT_ROOT="/Users/mikedemott/HermesRevenueLabVault"

cd "${LAB_ROOT}"
exec /Users/mikedemott/.local/bin/claude \
  --name "Hermes Revenue Lab" \
  --add-dir "${VAULT_ROOT}" \
  "$@"
