#!/usr/bin/env bash
# up.sh — the Realms product orchestra (casals.json), end-to-end, in one command:
#
#   preflight → build → casals up → export → domains → publish → verify
#
#   scripts/up.sh -e local --yes                       # laptop / CI: everything, test variant
#   scripts/up.sh -e production --identity prod-session --yes   # prod-session: icp delegation from the HSM
#   scripts/up.sh -e production --publish-only          # only the content phases (catalog changed)
#   scripts/up.sh -e local --build-only                 # just the artifacts the sheet references
#
# Options
#   -e, --env ENV               local | production (environments.<env> of the sheet)  [required]
#   --identity NAME             deploy identity; default local-dev for local, required otherwise.
#                               With a touch-policy YubiKey pass a session identity made with
#                               `icp identity delegation` (Casals/docs/OPERATIONS.md, "Hardware keys")
#   --skip-build                reuse the artifacts already on disk
#   --build-only                stop after the build phase
#   --skip-publish              stop after up + domains (provision only)
#   --publish-only              skip build/up/domains: export bindings and publish + verify
#   --no-domains                skip `realms domains apply`
#   --branding                  also `realms files publish-branding` (demo realm branding)
#   --extensions LIST           forward to files/marketplace publish (comma-separated ids)
#   --codices LIST              forward to files/marketplace publish
#   --extensions-only | --codices-only
#   --bootstrap                 forward to casals up (production: allow a brand-new conductor)
#   -y, --yes                   non-interactive (casals up --yes, domains --yes)
#   -h, --help
#
# Environment
#   CASALS_HOME          bindings root. local: <CASALS_HOME>/<orchestra>/ (the e2e harness layout,
#                        default ~/casals-home); other envs: <CASALS_HOME> itself (default ~/.casals),
#                        which is where a previous `casals up` wrote <orchestra>.<env>.json.
#   CASALS_DIR, FILE_REGISTRY_DIR   sibling checkouts (default ../Casals, ../file-registry)
#   IC_TOKENS_VERSION    ic-tokens release for token/nft wasms (default 0.1.0)
#   SCENARIOS            local only: harness scenarios (default fresh,idempotent)
#   DFX_HSM_PIN          production: PIN of the hardware key behind --identity
#   CLOUDFLARE_API_TOKEN production: Zone:Read + DNS:Edit on the zone, for the domains phase
#
# Every phase is idempotent: a second run rebuilds (or --skip-build),
# `casals up` reporting the orchestra already built, domains in place, 0 changed files / listings, and exits 0.
#
# This builds the sheet (day one) and re-runs are no-ops; it does not roll out a
# new build to canisters that already exist. A release is
# `casals upgrade casals.json --wasm <family> | --content <namespace>` (Casals #52).
set -euo pipefail

REALMS_DIR="${REALMS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT="$(dirname "$REALMS_DIR")"
CASALS_DIR="${CASALS_DIR:-$ROOT/Casals}"
FILE_REGISTRY_DIR="${FILE_REGISTRY_DIR:-$ROOT/file-registry}"
SHEET="$REALMS_DIR/casals.json"
IC_TOKENS_VERSION="${IC_TOKENS_VERSION:-0.1.0}"
SCENARIOS="${SCENARIOS:-fresh,idempotent}"

export TERM="${TERM:-xterm}"
export DFX_WARNING=-mainnet_plaintext_identity
export PYTHONUNBUFFERED=1

ENV="" IDENTITY=""
SKIP_BUILD=0 BUILD_ONLY=0 SKIP_PUBLISH=0 PUBLISH_ONLY=0 NO_DOMAINS=0 BRANDING=0 YES=0 BOOTSTRAP=0
EXTENSIONS="" CODICES="" PUBLISH_FILTER=()
usage() { sed -n '2,41p' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    -e|--env) ENV="$2"; shift 2 ;;
    --env=*) ENV="${1#*=}"; shift ;;
    --identity) IDENTITY="$2"; shift 2 ;;
    --identity=*) IDENTITY="${1#*=}"; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --build-only) BUILD_ONLY=1; shift ;;
    --skip-publish) SKIP_PUBLISH=1; shift ;;
    --publish-only) PUBLISH_ONLY=1; shift ;;
    --no-domains) NO_DOMAINS=1; shift ;;
    --branding) BRANDING=1; shift ;;
    --extensions) EXTENSIONS="$2"; shift 2 ;;
    --extensions=*) EXTENSIONS="${1#*=}"; shift ;;
    --codices) CODICES="$2"; shift 2 ;;
    --codices=*) CODICES="${1#*=}"; shift ;;
    --extensions-only|--codices-only) PUBLISH_FILTER+=("$1"); shift ;;
    --bootstrap) BOOTSTRAP=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "up.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 ($2)"; }
confirm() { # confirm <prompt>  — true with --yes, else ask
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || die "$1 — no TTY to ask; re-run with --yes"
  read -r -p "$1 [y/N] " ans; [ "$ans" = y ] || [ "$ans" = Y ]
}

[ -n "$ENV" ] || { usage >&2; die "-e/--env is required (local | production)"; }
[ -f "$SHEET" ] || die "sheet not found: $SHEET"
[ -f "$CASALS_DIR/casals_cli/main.py" ] || die "Casals checkout not found at $CASALS_DIR (set CASALS_DIR)"
[ -f "$FILE_REGISTRY_DIR/Makefile" ] || die "file-registry checkout not found at $FILE_REGISTRY_DIR (set FILE_REGISTRY_DIR)"

# sheet_get <python expression over `s` (the sheet) and `env`> — prints the value or ""
sheet_get() {
  python3 - "$SHEET" "$ENV" "$1" <<'EOF'
import json, sys
s = json.load(open(sys.argv[1])); env = s.get("environments", {}).get(sys.argv[2])
if env is None:
    sys.exit(f"environment {sys.argv[2]!r} is not in the sheet (have: {', '.join(s.get('environments', {}))})")
try:
    v = eval(sys.argv[3], {}, {"s": s, "env": env})
except (KeyError, TypeError, AttributeError):
    v = ""
print("" if v is None else (json.dumps(v) if isinstance(v, (dict, list, bool)) else v))
EOF
}
ORCHESTRA="$(sheet_get 's["name"]')"
NETWORK="$(sheet_get 'env["network"]')"
DNS_PROVIDER="$(sheet_get 'env["dns"]["provider"]')"
BUILD_VARIANT="$(sheet_get 'env.get("build_variant")')"
if [ -z "$BUILD_VARIANT" ]; then
  if [ "$ENV" = local ]; then BUILD_VARIANT="test"; else BUILD_VARIANT="production"; fi
fi

if [ "$ENV" = local ]; then
  IDENTITY="${IDENTITY:-local-dev}"
  export CASALS_HOME="${CASALS_HOME:-$HOME/casals-home}"
  ORCH_HOME="$CASALS_HOME/$ORCHESTRA"          # the e2e harness / local_up.sh layout
else
  [ -n "$IDENTITY" ] || die "--identity is required for -e $ENV (the environment's deployer key)"
  export CASALS_HOME="${CASALS_HOME:-$HOME/.casals}"
  ORCH_HOME="$CASALS_HOME"
fi
BINDINGS_FILE="$ORCH_HOME/$ORCHESTRA.$ENV.json"
mkdir -p "$ORCH_HOME"

casals() { (cd "$CASALS_DIR" && CASALS_HOME="$ORCH_HOME" python3 -m casals_cli.main -e "$ENV" --identity "$IDENTITY" "$@"); }

# ── preflight ─────────────────────────────────────────────────────────────────
say "Preflight: $ORCHESTRA -e $ENV (network $NETWORK, build variant $BUILD_VARIANT)"
need icp     "npm install -g @icp-sdk/icp-cli"
need node    "node 20+"
need npm     "node 20+"
need python3 "python 3.11"
need curl    "curl"
need make    "make (file-registry build)"
python3 -c 'import basilisk' 2>/dev/null || die "python deps missing: pip install -r $CASALS_DIR/requirements-dev.txt -r $REALMS_DIR/requirements.txt"
python3 -c 'import ic' 2>/dev/null       || die "python deps missing: pip install -r $CASALS_DIR/requirements-dev.txt"
note "realms:        $REALMS_DIR"
note "Casals:        $CASALS_DIR"
note "file-registry: $FILE_REGISTRY_DIR"
note "bindings:      $BINDINGS_FILE"
note "identity:      $IDENTITY"

if [ "$ENV" = local ]; then
  # Plaintext key, local replica only. local_up.sh mirrors it into dfx for the live tests.
  if ! icp identity list 2>/dev/null | awk '{print $1=="*"?$2:$1}' | grep -qx "$IDENTITY"; then
    icp identity new "$IDENTITY" --storage plaintext
  fi
  # A sidecar replica (CASALS_REPLICA_PORT) leaves its coordinates under CASALS_HOME.
  if [ -f "$CASALS_HOME/.replica/env" ] && [ -z "${CASALS_REPLICA_HOME:-}" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$CASALS_HOME/.replica/env"
    set +a
  fi
else
  icp identity list 2>/dev/null | awk '{print $1=="*"?$2:$1}' | grep -qx "$IDENTITY" \
    || die "icp identity '$IDENTITY' not found (icp identity list)"
  [ -n "${DFX_HSM_PIN:-}" ] || die "DFX_HSM_PIN is not set; every signature of a hardware identity needs it"
  if [ "$PUBLISH_ONLY" = 0 ] && [ ! -f "$BINDINGS_FILE" ] && [ "$BOOTSTRAP" = 0 ]; then
    die "no bindings at $BINDINGS_FILE — point CASALS_HOME at the directory of the first \`up\`, or pass --bootstrap to create a brand-new $ENV conductor on purpose"
  fi
  if [ "$NO_DOMAINS" = 0 ] && [ "$DNS_PROVIDER" != none ] && [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    die "CLOUDFLARE_API_TOKEN is not set (dns.provider=$DNS_PROVIDER); export it or pass --no-domains"
  fi
fi
# The `realms` product CLI, from this checkout (editable, so a re-run is a no-op check).
if ! command -v realms >/dev/null 2>&1 || \
   [ "$(cd /tmp && python3 -c 'import realms.cli.main as m; print(m.__file__)' 2>/dev/null)" != "$REALMS_DIR/cli/realms/cli/main.py" ]; then
  say "Preflight: pip install -e cli (the realms CLI from this checkout)"
  python3 -m pip install -q -e "$REALMS_DIR/cli" >/dev/null
fi
need realms "pip install -e $REALMS_DIR/cli"

# ── build ─────────────────────────────────────────────────────────────────────
# The exact recipes CI runs (.github/workflows/realms-e2e.yml). Every artifact
# is a `local:` source of the sheet; a missing one makes `casals up` refuse.
if [ "$PUBLISH_ONLY" = 0 ] && [ "$SKIP_BUILD" = 0 ]; then
  say "Build: pinned python deps (a stale ic-python-db yields a wasm that traps at init)"
  (cd "$REALMS_DIR" && python3 -m pip install -q -r requirements.txt)

  say "Build: fleet file registry (wasm + UI dist) in $FILE_REGISTRY_DIR"
  (cd "$FILE_REGISTRY_DIR" && make build
   ls -l .basilisk/ic_file_registry/ic_file_registry.wasm frontend/dist/index.html)

  say "Build: ic-tokens wasms (v$IC_TOKENS_VERSION)"
  mkdir -p "$REALMS_DIR/.external-wasms"
  for w in token_backend nft_backend; do
    [ -s "$REALMS_DIR/.external-wasms/$w.wasm" ] && { note "have $w.wasm"; continue; }
    curl -fsSL "https://github.com/smart-social-contracts/ic-tokens/releases/download/v${IC_TOKENS_VERSION}/${w}.wasm" \
      -o "$REALMS_DIR/.external-wasms/$w.wasm"
    note "fetched $w.wasm"
  done

  say "Build: backends (realm_backend, marketplace_backend)"
  (cd "$REALMS_DIR"
   CANISTER_CANDID_PATH="$PWD/src/realm_backend/realm_backend.did" \
     python3 scripts/pack_realm_backend.py
   CANISTER_CANDID_PATH="$PWD/src/marketplace_backend/marketplace_backend.did" \
     python3 -m basilisk marketplace_backend src/marketplace_backend/main.py
   ls -l .basilisk/realm_backend/realm_backend.wasm .basilisk/marketplace_backend/marketplace_backend.wasm)

  say "Build: frontends (realm_frontend REALMS_BUILD_VARIANT=$BUILD_VARIANT, marketplace_frontend)"
  (cd "$REALMS_DIR"
   # Root workspace install: a workspace-local install strands vite away from the
   # hoisted @sveltejs/kit. extension-bridge is imported from its dist/, so first.
   npm install --legacy-peer-deps
   npm run build --workspace packages/extension-bridge
   (cd src/realm_frontend && REALMS_BUILD_VARIANT="$BUILD_VARIANT" npm run build && test -f dist/index.html)
   python3 scripts/check_frontend_variant.py src/realm_frontend/dist "$BUILD_VARIANT"
   (cd src/marketplace_frontend && npm run build && test -f dist/index.html))
fi
[ "$BUILD_ONLY" = 1 ] && { say "Build only: done"; exit 0; }

# ── up ────────────────────────────────────────────────────────────────────────
if [ "$PUBLISH_ONLY" = 0 ]; then
  if [ "$ENV" = local ]; then
    # The Casals e2e harness: starts the replica if needed, funds the identity,
    # then grades the sheet (fresh: converges from nothing, plan empty, oracle
    # PASS; idempotent: a second up changes nothing). KEEP=1 leaves it up.
    # The harness keeps each orchestra under $CASALS_E2E_ROOT/<name> (a temp dir
    # when unset) and runs every scenario in tests/e2e/scenarios.py; the
    # wrapper narrows that list to $SCENARIOS (the release/content scenarios
    # need `release:` sources a local build does not have).
    say "casals up (harness, scenarios: $SCENARIOS)"
    (cd "$CASALS_DIR" && CASALS_E2E_ROOT="$CASALS_HOME" CASALS_E2E_IDENTITY="$IDENTITY" \
       KEEP=1 SCENARIOS="$SCENARIOS" python3 - "$SHEET" <<'PY'
import os, sys
sys.path.insert(0, "tests/e2e")
import run_e2e, scenarios
want = [s for s in os.environ["SCENARIOS"].split(",") if s]
have = {f.__name__: f for f in scenarios.SCENARIOS}
unknown = [s for s in want if s not in have]
if unknown:
    sys.exit(f"unknown harness scenario(s): {', '.join(unknown)} (have: {', '.join(have)})")
scenarios.SCENARIOS[:] = [have[s] for s in want]
sys.exit(run_e2e.main(sys.argv[1:]))
PY
    )
  else
    up_args=(up "$SHEET")
    [ "$YES" = 1 ] && up_args+=(--yes)
    [ "$BOOTSTRAP" = 1 ] && up_args+=(--bootstrap)
    global_args=()
    if [ "$ENV" = local ]; then
      eval "$(cd "$CASALS_DIR" && python3 -m casals_cli.replica start)"   # idempotent
    elif [ "$YES" = 0 ] && [ -f "$BINDINGS_FILE" ]; then
      say "casals up --dry-run (what up would create)"
      casals up "$SHEET" --dry-run || true
      confirm "Apply this plan to $ENV as $IDENTITY?" || die "stopped before up"
    fi
    say "casals ${global_args[*]:-} ${up_args[*]}"
    [ "$ENV" = local ] || note "a hardware identity signs bootstrap, set_sheet and every apply: expect touches / PIN prompts"
    casals ${global_args[@]+"${global_args[@]}"} "${up_args[@]}"
  fi
fi
[ -f "$BINDINGS_FILE" ] || die "no bindings at $BINDINGS_FILE after up"

# ── export ────────────────────────────────────────────────────────────────────
# Ids come from the live conductor, never from a table in the repo.
say "Export: live bindings"
EXPORT_JSON="$ORCH_HOME/$ORCHESTRA.$ENV.export.json"
casals export "$SHEET" > "$EXPORT_JSON"
# Casals nests ids as sections[].stands[].canisters[]; add the flat name → id
# map (`bindings`) that this script, `realms domains --export` and the e2e read.
python3 - "$EXPORT_JSON" <<'PY'
import json, sys
path = sys.argv[1]
d = json.load(open(path))
flat = dict(d.get("bindings") or {})
for sec in d.get("sections") or []:
    for st in sec.get("stands") or []:
        for c in st.get("canisters") or []:
            if c.get("name") and c.get("canister_id"):
                flat.setdefault(c["name"], c["canister_id"])
d["bindings"] = flat
json.dump(d, open(path, "w"), indent=2)
PY
binding() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("bindings",{}).get(sys.argv[2],""))' "$EXPORT_JSON" "$1"; }
FLEET_REGISTRY="$(binding fleet-file-registry)"
MARKETPLACE="$(binding marketplace-backend)"
MARKETPLACE_FE="$(binding marketplace-frontend)"
REGISTRY_FE="$(binding fleet-file-registry-frontend)"
CASALS_FE="$(python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get("conductor") or {}).get("casals-frontend",""))' "$BINDINGS_FILE")"
NETWORK_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("network_url",""))' "$BINDINGS_FILE")"
[ -n "$FLEET_REGISTRY" ] || die "export has no fleet-file-registry binding (is the Product section built?)"
[ -n "$MARKETPLACE" ]    || die "export has no marketplace-backend binding"
if [ "$ENV" = local ]; then PUBLISH_NET="$NETWORK_URL"; else PUBLISH_NET="$NETWORK"; fi
note "fleet-file-registry  $FLEET_REGISTRY"
note "marketplace-backend  $MARKETPLACE"
note "network              $PUBLISH_NET"

# ── domains ───────────────────────────────────────────────────────────────────
if [ "$PUBLISH_ONLY" = 0 ]; then
  if [ "$NO_DOMAINS" = 1 ]; then
    say "Domains: skipped (--no-domains)"
  elif [ "$DNS_PROVIDER" = none ] || [ -z "$DNS_PROVIDER" ]; then
    say "Domains: none for -e $ENV (dns.provider=${DNS_PROVIDER:-unset})"
  else
    say "Domains: realms domains apply (Cloudflare records → IC gateway registration → HTTP 200)"
    dom_args=(domains apply "$SHEET" -e "$ENV" --export "$EXPORT_JSON")
    [ "$YES" = 1 ] && dom_args+=(--yes)
    (cd "$REALMS_DIR" && CASALS_HOME="$ORCH_HOME" realms "${dom_args[@]}")
  fi
fi
[ "$SKIP_PUBLISH" = 1 ] && { say "Provisioned; publish skipped (--skip-publish)"; exit 0; }

# ── publish ───────────────────────────────────────────────────────────────────
# `casals up` provisions canisters, not content. The sheet granted the operator
# publish rights on the fleet registry and a developer license + reviewer seat
# on the marketplace; the product CLI fills them. Both commands are idempotent
# (unchanged files skipped, listings upserted). A listing ends `verified` only if
# registry ACL → create_extension → review_listing → set_namespace_approval all work.
pub_filter=(${PUBLISH_FILTER[@]+"${PUBLISH_FILTER[@]}"})
[ -n "$EXTENSIONS" ] && pub_filter+=(--extensions "$EXTENSIONS")
[ -n "$CODICES" ]    && pub_filter+=(--codices "$CODICES")
say "Publish: packages → fleet file registry $FLEET_REGISTRY"
[ "$ENV" = local ] || note "signed by $IDENTITY (a hardware key: one touch per upload batch)"
(cd "$REALMS_DIR" && realms files publish --network "$PUBLISH_NET" --registry "$FLEET_REGISTRY" --identity "$IDENTITY" ${pub_filter[@]+"${pub_filter[@]}"})
if [ "$BRANDING" = 1 ]; then
  say "Publish: demo realm branding"
  (cd "$REALMS_DIR" && realms files publish-branding --network "$PUBLISH_NET" --registry "$FLEET_REGISTRY" --identity "$IDENTITY") \
    || note "branding publish skipped (no demo branding sources)"
fi
say "Publish: marketplace listings on $MARKETPLACE (created/updated + review-approved)"
(cd "$REALMS_DIR" && realms marketplace publish --network "$PUBLISH_NET" --marketplace "$MARKETPLACE" --registry "$FLEET_REGISTRY" \
   --identity "$IDENTITY" ${pub_filter[@]+"${pub_filter[@]}"})

# ── verify ────────────────────────────────────────────────────────────────────
say "Verify"
# `casals up` builds the sheet once and fails unless every row converged (locally
# the harness also grades fresh + idempotent); there is no separate plan to check.
# Read-only queries as `anonymous`: no touch on a hardware key, and the same
# call shape on a local replica (by URL) and on mainnet (by name).
if [ "$ENV" = local ]; then
  port="${NETWORK_URL##*:}"
  icp_net=(--network "$NETWORK_URL" --root-key fetch)
  url_of() { echo "http://$1.localhost:$port/"; }
  reg_url="$(url_of "$FLEET_REGISTRY")"
else
  icp_net=(--network "$NETWORK")
  url_of() { echo "https://$1.icp0.io/"; }
  reg_url="https://$FLEET_REGISTRY.raw.icp0.io/"
fi
namespaces="$(icp canister call "$FLEET_REGISTRY" list_namespaces "()" --query "${icp_net[@]}" --identity anonymous 2>&1 || true)"
if printf '%s' "$namespaces" | grep -q '\\"namespace\\"\|"namespace"'; then
  note "fleet file registry: $(printf '%s' "$namespaces" | grep -o 'namespace\\\?"' | wc -l) namespace(s)"
else
  printf '%s\n' "$namespaces" | tail -3
  die "fleet file registry $FLEET_REGISTRY lists no namespaces after publish"
fi
# One listing must be verified end to end (the CI assertion). Prefer an id we published.
probe="${EXTENSIONS%%,*}"; [ -n "$probe" ] || probe=hello_world
if [[ " ${PUBLISH_FILTER[*]:-} " != *" --codices-only "* ]]; then
  listing="$(icp canister call "$MARKETPLACE" get_extension_details "(\"$probe\")" --query "${icp_net[@]}" --identity anonymous 2>&1 || true)"
  # icp prints the field as its candid hash (1_933_258_390) when it cannot fetch the .did.
  if printf '%s' "$listing" | grep -Eq '(verification_status|1_933_258_390) = "verified"'; then
    note "marketplace: listing '$probe' is verified"
  else
    printf '%s\n' "$listing" | tail -5
    die "marketplace listing '$probe' is not verified (registry ACL / license / reviewer chain)"
  fi
fi

say "Done: $ORCHESTRA -e $ENV"
[ -n "$MARKETPLACE_FE" ] && note "marketplace            $(url_of "$MARKETPLACE_FE")"
[ -n "$REGISTRY_FE" ]    && note "fleet file registry UI $(url_of "$REGISTRY_FE")"
note "fleet file registry    $reg_url"
[ -n "$CASALS_FE" ]      && note "Casals frontend        $(url_of "$CASALS_FE")"
note "bindings               $BINDINGS_FILE"
note "export                 $EXPORT_JSON"
