#!/usr/bin/env bash
# Run the lab extension on a realm in PocketIC: ./run.sh [--find] [--repack] [function] [n ...] (see README.md).
# The first run makes a Python environment in .cache/venv, with the system's packages plus the database library
# version requirements.txt pins, which the realm's wasm bundles.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
VENV="$HERE/.cache/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Making $VENV (once)..." >&2
  python3 -m venv --system-site-packages "$VENV"
  "$VENV/bin/pip" install -q "$(grep '^ic-python-db==' "$ROOT/requirements.txt")"
fi
cd "$ROOT"
exec env -u VIRTUAL_ENV "$VENV/bin/python" "$HERE/run.py" "$@"
