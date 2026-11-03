#!/usr/bin/env bash
# Conformance gates G0-G6. CI sets UWS_REQUIRE_ORACLE=1 so a missing Go oracle fails instead of skipping.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== G0 provenance"
uv run python scripts/provenance.py --check
echo "== G1 static"
uv run ruff check .
uv run ruff format --check .
uv run mypy
echo "== G2-G6 tests"
uv run pytest "$@"
