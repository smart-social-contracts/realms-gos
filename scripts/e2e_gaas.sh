#!/usr/bin/env bash
# e2e_gaas.sh — the gos.earth journey in a browser (GaaS portal, a realm born through it), and the same portal
# as an Alpha visitor sees it (invitation codes, no card billing), on a local replica, from a clean checkout to
# Playwright reports.
#
#   scripts/e2e_gaas.sh                 # build, bring the stack up, run both specs
#   scripts/e2e_gaas.sh --headed        # extra args go to `playwright test`
#   SKIP_STACK=1 scripts/e2e_gaas.sh    # the stack is already up (a re-run): only prepare the portal, run the specs
#
# What it does:
#   1. builds the realm (test variant: the portal's test mode needs deterministic identities), the extension
#      bundles, the Casals conductor, and gos-as-a-service's installer, registry and portal,
#   2. writes a copy of the GaaS sheet whose rows are those builds (scripts/e2e_gaas_sheet.py; casals.json
#      installs releases everywhere, and the pinned ones do not work with each other or with the slim realm),
#   3. scripts/local_up.sh --gaas: the product and GaaS orchestras on the local replica,
#   4. scripts/e2e_gaas_scale.py: the portal's test mode and a voucher, written to GAAS_JSON,
#   5. src/realm_frontend/tests/gaas/scale.spec.ts through playwright.gaas.config.ts,
#   6. scripts/e2e_gaas_alpha.py: the registry in Alpha mode and its invitation codes, written to ALPHA_JSON,
#   7. src/realm_frontend/tests/gaas/alpha.spec.ts, reported beside the first (test-results/gaas-alpha,
#      playwright-report/gaas-alpha). Both run even if the first fails.
#
# Layout and environment: as scripts/local_up.sh (sibling checkouts of Casals, file-registry and
# gos-as-a-service, or CASALS_DIR / FILE_REGISTRY_DIR / GAAS_DIR; CASALS_HOME; CASALS_REPLICA_PORT for a replica
# beside another). E2E_DIR (default /tmp/realms-gaas-e2e) holds the sheet and GAAS_JSON.
#
# It writes into the sibling checkouts: the builds of Casals (`.basilisk`) and of gos-as-a-service (`.basilisk`,
# `node_modules`, `dist`, and regenerated .did files), so keep those checkouts free of work in progress.
#
# Casals and gos-as-a-service must be at main: the pinned releases do not resolve the sheet's `$canister:`
# references, and a realm's quarter_add calls the conductor's stand_grow, which they lack.
set -euo pipefail

REALMS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(dirname "$REALMS_DIR")"
export CASALS_DIR="${CASALS_DIR:-$ROOT/Casals}"
export GAAS_DIR="${GAAS_DIR:-$ROOT/gos-as-a-service}"
export CASALS_HOME="${CASALS_HOME:-$HOME/casals-home}"
E2E_DIR="${E2E_DIR:-/tmp/realms-gaas-e2e}"
export TERM="${TERM:-xterm}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

mkdir -p "$E2E_DIR"
export GAAS_JSON="$E2E_DIR/gaas.json"

if [ "${SKIP_STACK:-0}" != 1 ]; then
  say "Build: the product, the extension bundles, the Casals conductor"
  "$REALMS_DIR/scripts/up.sh" -e local --build-only
  (cd "$REALMS_DIR" && realms files build)
  make -C "$CASALS_DIR" build-backend

  say "Build: the installer, the registry and the portal of gos-as-a-service"
  (cd "$GAAS_DIR"
   for canister in realm_installer realm_registry_backend; do
     CANISTER_CANDID_PATH="$PWD/src/$canister/$canister.did" python3 -m basilisk "$canister" "src/$canister/main.py"
   done
   npm ci --legacy-peer-deps
   npm run build --workspace=realm_registry_frontend)

  say "The GaaS sheet with these builds"
  export GAAS_SHEET="$E2E_DIR/casals.local-build.json"
  REALM_VERSION=e2e \
    REALM_BACKEND_WASM="$REALMS_DIR/.basilisk/realm_backend/realm_backend.wasm" \
    REALM_FRONTEND="$REALMS_DIR/src/realm_frontend/dist" \
    CASALS_BACKEND_WASM="$CASALS_DIR/.basilisk/casals_backend/casals_backend.wasm" \
    INSTALLER_WASM="$GAAS_DIR/.basilisk/realm_installer/realm_installer.wasm" \
    REGISTRY_WASM="$GAAS_DIR/.basilisk/realm_registry_backend/realm_registry_backend.wasm" \
    REGISTRY_FRONTEND="$GAAS_DIR/src/realm_registry_frontend/dist" \
    python3 "$REALMS_DIR/scripts/e2e_gaas_sheet.py" "$GAAS_SHEET"

  say "Stack: scripts/local_up.sh --gaas"
  CASALS_LOCAL_ROOTS="$REALMS_DIR:$CASALS_DIR:$GAAS_DIR" "$REALMS_DIR/scripts/local_up.sh" --gaas --no-realm --skip-build
fi

say "The portal: test mode and a voucher"
python3 "$REALMS_DIR/scripts/e2e_gaas_scale.py" --out "$GAAS_JSON"

cd "$REALMS_DIR/src/realm_frontend"
status=0

say "Playwright: the GaaS journey"
npx playwright test -c playwright.gaas.config.ts scale.spec.ts "$@" || status=1

say "The portal in Alpha: invitation mode on, card billing off"
export ALPHA_JSON="$E2E_DIR/alpha.json"
python3 "$REALMS_DIR/scripts/e2e_gaas_alpha.py" --out "$ALPHA_JSON"

say "Playwright: the Alpha visitor"
PLAYWRIGHT_HTML_OUTPUT_DIR=playwright-report/gaas-alpha \
  npx playwright test -c playwright.gaas.config.ts alpha.spec.ts --output test-results/gaas-alpha "$@" || status=1

exit $status
