#!/usr/bin/env bash
# e2e_ui.sh — Realms GOS UI end-to-end on the local replica, without gaas.
#
# Prerequisite: the product orchestra is up on the local replica and the codex
# under test is published to the fleet file registry, e.g.
#
#   scripts/up.sh -e local --yes                    # build + casals up + publish everything
#
# Then this script:
#   1. deploys one standalone realm (realm_backend + realm_frontend) next to the
#      orchestra and plays the installer (scripts/e2e_standalone_realm.py): setup
#      steps, then the core extensions and the codex's dependencies with their
#      frontends, and
#   2. runs the Playwright suite in src/realm_frontend/tests/standalone, which logs
#      in as the founder through the portal bridge and walks the setup wizard
#      (codex, token, branding, languages, launch) and the launched realm.
#
#   scripts/e2e_ui.sh                       # syntropia
#   E2E_CODEX=agora scripts/e2e_ui.sh       # another codex
#   scripts/e2e_ui.sh --headed              # extra args go to `playwright test`
#
# Environment: CASALS_HOME (as for up.sh -e local), E2E_CODEX, E2E_DIR (work dir,
# default /tmp/realms-e2e), REALM_IDENTITY (default local-dev).
set -euo pipefail

REALMS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASALS_HOME="${CASALS_HOME:-$HOME/casals-home}"
E2E_DIR="${E2E_DIR:-/tmp/realms-e2e}"
REALM_JSON="$E2E_DIR/realm.json"
export TERM="${TERM:-xterm}"

python3 "$REALMS_DIR/scripts/e2e_standalone_realm.py" \
  --casals-home "$CASALS_HOME" \
  --identity "${REALM_IDENTITY:-local-dev}" \
  --project "$E2E_DIR/standalone-realm" \
  --codex "${E2E_CODEX:-syntropia}" \
  --out "$REALM_JSON"

cd "$REALMS_DIR/src/realm_frontend"
E2E_CODEX="${E2E_CODEX:-syntropia}" REALM_JSON="$REALM_JSON" npx playwright test -c playwright.standalone.config.ts "$@"
