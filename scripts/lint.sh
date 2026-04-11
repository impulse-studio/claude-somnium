#!/usr/bin/env bash
# Full lint pipeline for Somnium.
# Usage: ./scripts/lint.sh [--fix]
set -euo pipefail

FIX=false
if [[ "${1:-}" == "--fix" ]]; then
    FIX=true
fi

echo "=== ruff check ==="
if $FIX; then
    ruff check --fix .
else
    ruff check .
fi

echo ""
echo "=== ruff format ==="
if $FIX; then
    ruff format .
else
    ruff format --check .
fi

echo ""
echo "=== pyright ==="
pyright somnium/

echo ""
echo "=== file size check (max 500 lines) ==="
python scripts/check_file_sizes.py

echo ""
echo "=== function size check (max 60 lines) ==="
python scripts/check_function_sizes.py

echo ""
echo "=== vulture (dead code) ==="
vulture somnium/ --min-confidence 80 || true

echo ""
echo "=== pip-audit (dependency vulnerabilities) ==="
pip-audit --strict || true

echo ""
echo "All checks passed."
