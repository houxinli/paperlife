#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m unittest discover -s tests
node --test web/life/*.test.mjs
node --test miniprogram/tests/*.test.cjs
for file in web/life/*.js; do node --check "$file"; done
"$PYTHON_BIN" scripts/public_check.py
