#!/usr/bin/env bash
# local_up.sh — the whole local stack in one command: the Realms product
# orchestra (and, with --gaas, the GaaS orchestra plus one realm born through
# the portal) on a local replica, built, converged, populated, URLs printed.
#
# It is a wrapper: the identity, the replica and the URLs are handled here;
# Realms still converges through `scripts/up.sh -e local`. GaaS (`--gaas`)
# converges through the Casals e2e harness, then `realms files publish`.
# Nothing
# here touches mainnet or a YubiKey: the identity is the plaintext `local-dev`
# key, the environment is casals.json `environments.local`. The replica
# defaults to icp's implicit `local` network on port 8000. To run beside
# another replica on the same laptop (a corpus, a second local_up):
#
#   CASALS_HOME=~/casals-home-b scripts/local_up.sh --gaas --replica-port auto
#
#   scripts/local_up.sh              # build everything, up, grade, publish the catalog, print URLs
#   scripts/local_up.sh --gaas       # ... plus the GaaS orchestra (portal, installer, registry)
#                                    #   and one realm born through the portal path
#   scripts/local_up.sh --no-realm   # with --gaas: do not mint the first realm (a spec launches its own)
#   scripts/local_up.sh --skip-build # reuse the last build
#   scripts/local_up.sh --skip-publish   # provision only (empty registries / marketplace)
#   scripts/local_up.sh --urls       # only print the URLs of a running stand
#   scripts/local_up.sh --down       # stop THIS run's replica and forget the bindings
#   scripts/local_up.sh --replica-port auto|N   # isolated replica (not :8000)
#
# REALM_NAME (default first-realm) names the realm --gaas mints; a name that
# already exists is skipped, so re-runs are cheap.
#
# Layout: this repo, Casals, file-registry and gos-as-a-service must be sibling
# checkouts (the sheets resolve `local:` sources against their own directory).
# Override with REALMS_DIR / CASALS_DIR / GAAS_DIR / FILE_REGISTRY_DIR (and GAAS_SHEET for another
# GaaS sheet, e.g. one tests/e2e/local_sheet.py wrote with local builds). Bindings
# live in CASALS_HOME (default ~/casals-home). Logs live in LOCAL_UP_LOG_DIR
# (default ~/casals-logs) so --down does not wipe them.
set -euo pipefail

REALMS_DIR="${REALMS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ROOT="$(dirname "$REALMS_DIR")"
CASALS_DIR="${CASALS_DIR:-$ROOT/Casals}"
GAAS_DIR="${GAAS_DIR:-$ROOT/gos-as-a-service}"
# The GaaS sheet; tests/e2e/local_sheet.py in gos-as-a-service writes one that installs local builds.
GAAS_SHEET="${GAAS_SHEET:-$GAAS_DIR/casals.json}"
export CASALS_HOME="${CASALS_HOME:-$HOME/casals-home}"
IDENTITY="${CASALS_E2E_IDENTITY:-local-dev}"
REALM_NAME="${REALM_NAME:-first-realm}"

export TERM="${TERM:-xterm}"
export DFX_WARNING=-mainnet_plaintext_identity
export CASALS_E2E_ENV=local
export CASALS_E2E_IDENTITY="$IDENTITY"

WITH_GAAS=0 NO_REALM=0 SKIP_BUILD=0 SKIP_PUBLISH=0 ONLY_URLS=0 DOWN=0
REPLICA_PORT_ARG=""
for arg in "$@"; do
  case "$arg" in
    --gaas) WITH_GAAS=1 ;;
    --no-realm) NO_REALM=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --skip-publish) SKIP_PUBLISH=1 ;;
    --urls) ONLY_URLS=1 ;;
    --down) DOWN=1 ;;
    --isolated) REPLICA_PORT_ARG="${REPLICA_PORT_ARG:-auto}" ;;
    --replica-port=*) REPLICA_PORT_ARG="${arg#--replica-port=}" ;;
    --replica-port) echo "local_up.sh: --replica-port needs auto or a port (e.g. --replica-port=auto)" >&2; exit 2 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
if [ -n "$REPLICA_PORT_ARG" ]; then
  export CASALS_REPLICA_PORT="$REPLICA_PORT_ARG"
fi

# Console + file. Logs sit outside CASALS_HOME so --down leaves them.
# `script` keeps a real TTY so python/npm stay line-buffered; tee is the fallback
# when stdout is already a pipe. PYTHONUNBUFFERED covers the tee path.
LOG_DIR="${LOCAL_UP_LOG_DIR:-$HOME/casals-logs}"
mkdir -p "$LOG_DIR" "$CASALS_HOME"
LOG="${LOCAL_UP_LOG:-$LOG_DIR/local_up-$(date +%Y%m%d-%H%M%S).log}"
ln -sfn "$LOG" "$LOG_DIR/latest.log"
export PYTHONUNBUFFERED=1
if [ -z "${LOCAL_UP_REEXEC:-}" ]; then
  export LOCAL_UP_REEXEC=1 LOCAL_UP_LOG="$LOG"
  {
    echo
    echo "======== local_up $(date -Iseconds) cwd=$(pwd) args: $* ========"
    echo "  log:    $LOG"
    echo "  latest: $LOG_DIR/latest.log"
  } | tee -a "$LOG"
  if [ -t 1 ] && command -v script >/dev/null 2>&1; then
    exec script -qaef -c "$(printf '%q ' "$0" "$@")" "$LOG"
  fi
  exec > >(tee -a "$LOG") 2>&1
fi

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1 ($2)"; }

casals() { # casals <sheet-home-name> <args...>  — the CLI with that orchestra's bindings
  local home="$1"; shift
  (cd "$CASALS_DIR" && CASALS_HOME="$CASALS_HOME/$home" \
     python3 -m casals_cli.main -e local --identity "$IDENTITY" "$@")
}

casals_export() { # casals_export <sheet-home-name> <sheet-path>  — {"bindings": {name: id}, ...}
  # A conductor released before `export` carried bindings (the pinned GaaS one) answers without
  # them; `show` lists the same canisters, so fall back to it.
  local home="$1" sheet="$2" out
  out="$(casals "$home" export "$sheet")"
  if printf '%s' "$out" | python3 -c 'import json,sys; sys.exit(0 if "bindings" in json.load(sys.stdin) else 1)' 2>/dev/null; then
    printf '%s\n' "$out"
    return
  fi
  (cd "$CASALS_DIR" && CASALS_HOME="$CASALS_HOME/$home" \
     python3 -m casals_cli.main --json -e local --identity "$IDENTITY" show "$sheet") | python3 -c '
import json, sys
t = sys.stdin.read()
d = json.loads(t[t.index("{"):])
print(json.dumps({"bindings": {c["name"]: c["canister_id"] for c in d["canisters"] if c.get("canister_id")}}))'
}

print_urls() { # print_urls <sheet-home-name> <sheet-path> <title>
  local home="$1" sheet="$2" title="$3"
  [ -f "$CASALS_HOME/$home/$home.local.json" ] || { echo "  ($title: no bindings under $CASALS_HOME/$home)"; return; }
  # `export` is the conductor's view (every stand canister); the conductor's own
  # canisters (casals-frontend, ...) are in the local bindings file.
  casals_export "$home" "$sheet" | python3 -c '
import json, os, sys
title, bindings_file = sys.argv[1], sys.argv[2]
port = os.environ.get("CASALS_REPLICA_PORT") or "8000"
port = port if port.isdigit() else "8000"
d = json.load(sys.stdin)
conductor = (json.load(open(bindings_file)).get("conductor") or {})
rows = dict(d.get("bindings", {}))
rows.update({k: v for k, v in conductor.items() if k not in rows})
print(f"\n  {title}")
for name, cid in rows.items():
    if name.endswith("frontend") and cid:
        print(f"    {name:36} http://{cid}.localhost:{port}/")
for name, cid in rows.items():
    if not name.endswith("frontend") and cid:
        print(f"    {name:36} {cid}")
' "$title" "$CASALS_HOME/$home/$home.local.json"
}

print_portal_links() { # the wizard, and every realm the installer has minted
  [ -f "$CASALS_HOME/gaas/gaas.local.json" ] || return 0
  casals_export gaas "$GAAS_SHEET" | python3 -c '
import json, os, sys
port = os.environ.get("CASALS_REPLICA_PORT") or "8000"
port = port if port.isdigit() else "8000"
b = json.load(sys.stdin).get("bindings", {})
portal = b.get("realm-registry-frontend")
if not portal: sys.exit(0)
print(f"\n  Portal (gaas new / wizard)           http://{portal}.localhost:{port}/")
realms = sorted(k[:-len("-backend")] for k in b
                if k.endswith("-backend") and f"{k[:-8]}-frontend" in b and f"{k[:-8]}-baton" in b)
for r in realms:
    fe = b[f"{r}-frontend"]
    print(f"    realm {r:30} http://{fe}.localhost:{port}/   (portal page: http://{portal}.localhost:{port}/r/{r})")
'
}

# ── --down ────────────────────────────────────────────────────────────────────
if [ "$DOWN" = 1 ]; then
  say "Stopping this run's replica and removing $CASALS_HOME"
  if [ -f "$CASALS_HOME/.replica/env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$CASALS_HOME/.replica/env"
    set +a
    (cd "$CASALS_DIR" && python3 -m casals_cli.replica stop) || true
  else
    (cd "$CASALS_DIR" && icp network stop -e local) || true
  fi
  rm -rf "$CASALS_HOME"
  exit 0
fi

# ── --urls ────────────────────────────────────────────────────────────────────
if [ "$ONLY_URLS" = 1 ]; then
  if [ -f "$CASALS_HOME/.replica/env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$CASALS_HOME/.replica/env"
    set +a
  fi
  print_urls realms-product "$REALMS_DIR/casals.json" "Realms product orchestra"
  print_urls gaas "$GAAS_SHEET" "GaaS orchestra (portal + installer + realms born through it)"
  print_portal_links
  exit 0
fi

# ── preflight ─────────────────────────────────────────────────────────────────
# Tool and python-dependency checks live in each repo's up.sh; this only checks
# what the wrapper itself needs (dfx for the identity mirror, the checkouts).
say "Preflight"
[ -f "$CASALS_DIR/casals_cli/main.py" ] || die "Casals checkout not found at $CASALS_DIR (set CASALS_DIR)"
[ -x "$REALMS_DIR/scripts/up.sh" ] || die "realms scripts/up.sh not found at $REALMS_DIR"
if [ "$WITH_GAAS" = 1 ]; then
  [ -f "$GAAS_SHEET" ] || die "GaaS sheet not found at $GAAS_SHEET (set GAAS_DIR or GAAS_SHEET)"
fi
need icp     "npm install -g @icp-sdk/icp-cli"
need ic-wasm "npm install -g @icp-sdk/ic-wasm"
need dfx     "https://internetcomputer.org/docs/building-apps/getting-started/install"
need python3 "python 3.11"
echo "  realms:  $REALMS_DIR"
echo "  Casals:  $CASALS_DIR"
[ "$WITH_GAAS" = 1 ] && echo "  gaas:    $GAAS_DIR"
echo "  home:    $CASALS_HOME"
echo "  icp $(icp --version | awk '{print $2}'), dfx $(dfx --version | awk '{print $2}'), $(python3 --version)"

# ── identity ──────────────────────────────────────────────────────────────────
say "Identity: $IDENTITY (plaintext, local replica only)"
if ! icp identity list 2>/dev/null | awk '{print $1=="*"?$2:$1}' | grep -qx "$IDENTITY"; then
  icp identity new "$IDENTITY" --storage plaintext
fi
if ! dfx identity list 2>/dev/null | grep -qx "$IDENTITY"; then
  # dfx runs the cross-quarter live test; it must be the same key as icp's.
  # icp exports PKCS#8 ("BEGIN PRIVATE KEY"); dfx only imports SEC1
  # ("BEGIN EC PRIVATE KEY"), so convert in between.
  need openssl "openssl (to convert the PEM for dfx)"
  pem="$(mktemp)"; sec1="$(mktemp)"; trap 'rm -f "$pem" "$sec1"' EXIT
  icp identity export "$IDENTITY" > "$pem"
  openssl ec -in "$pem" -out "$sec1" 2>/dev/null
  dfx identity import "$IDENTITY" "$sec1" --storage-mode plaintext
fi
icp_p="$(icp identity principal --identity "$IDENTITY")"
dfx_p="$(dfx identity get-principal --identity "$IDENTITY")"
[ "$icp_p" = "$dfx_p" ] || die "icp and dfx '$IDENTITY' differ ($icp_p vs $dfx_p); delete one and rerun"
echo "  $icp_p"

# ── stale bindings ────────────────────────────────────────────────────────────
# A local replica starts empty after a reboot / `icp network stop`; bindings
# left from the previous stand then point at canisters that no longer exist.
# Forget them so `casals up` converges from nothing instead of failing.
say "Replica"
if [ -f "$CASALS_HOME/.replica/env" ] && [ -z "${CASALS_REPLICA_HOME:-}" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$CASALS_HOME/.replica/env"
  set +a
fi
eval "$(cd "$CASALS_DIR" && python3 -m casals_cli.replica start)"
echo "  ${CASALS_NETWORK_URL:-http://127.0.0.1:8000}  home=${CASALS_REPLICA_HOME:-default (:8000)}"
icp_extra=()
if [ -n "${CASALS_REPLICA_HOME:-}" ]; then
  icp_extra=(--project-root-override "$CASALS_REPLICA_HOME")
fi
for home in realms-product gaas; do
  f="$CASALS_HOME/$home/$home.local.json"
  [ -f "$f" ] || continue
  backend="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("backend_id") or "")' "$f")"
  if [ -n "$backend" ] && ! (cd "$CASALS_DIR" && icp canister status "$backend" -e local --identity "$IDENTITY" "${icp_extra[@]}" >/dev/null 2>&1); then
    echo "  $home: bindings point at $backend, which is not on this replica — forgetting them"
    rm -rf "$CASALS_HOME/$home"
  else
    echo "  $home: bindings present, conductor $backend live"
  fi
done

# ── build → up → publish → verify, per orchestra ──────────────────────────────
# Realms: scripts/up.sh -e local (the Casals harness, then the fleet catalog).
# GaaS: the Casals harness, then realms files publish into the platform registry.
up_flags=(--identity "$IDENTITY" --yes)
[ "$SKIP_BUILD" = 1 ] && up_flags+=(--skip-build)
[ "$SKIP_PUBLISH" = 1 ] && up_flags+=(--skip-publish)

say "Realms product orchestra: scripts/up.sh -e local ${up_flags[*]}"
CASALS_DIR="$CASALS_DIR" "$REALMS_DIR/scripts/up.sh" -e local "${up_flags[@]}"

if [ "$WITH_GAAS" = 1 ]; then
  say "GaaS orchestra: Casals e2e harness"
  (cd "$CASALS_DIR" && CASALS_HOME="$CASALS_HOME" CASALS_E2E_ROOT="$CASALS_HOME" KEEP=1 \
     SCENARIOS="${SCENARIOS:-fresh,idempotent}" \
     python3 tests/e2e/run_e2e.py "$GAAS_SHEET")

  if [ "$SKIP_PUBLISH" = 0 ]; then
    say "Publish: packages → GaaS file registry"
    command -v realms >/dev/null 2>&1 || python3 -m pip install -q -e "$REALMS_DIR/cli"
    EXPORT_JSON="$CASALS_HOME/gaas/gaas.local.export.json"
    casals_export gaas "$GAAS_SHEET" > "$EXPORT_JSON"
    FILE_REGISTRY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("bindings",{}).get("file-registry",""))' "$EXPORT_JSON")"
    NETWORK_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("network_url",""))' "$CASALS_HOME/gaas/gaas.local.json")"
    [ -n "$FILE_REGISTRY" ] || die "export has no file-registry binding"
    (cd "$REALMS_DIR" && realms files publish --network "$NETWORK_URL" --registry "$FILE_REGISTRY" --identity "$IDENTITY")
  fi

  # A realm is born the way the portal wizard does it: request_deployment on
  # the registry → installer create_stand → the conductor builds the stand from
  # the `Deployments` template → the realm frontend serves /canister_ids.js.
  if [ "$NO_REALM" = 1 ]; then
    say "No first realm (--no-realm)"
  elif casals_export gaas "$GAAS_SHEET" | python3 -c '
import json, sys; sys.exit(0 if sys.argv[1] + "-backend" in json.load(sys.stdin).get("bindings", {}) else 1)' "$REALM_NAME"; then
    say "Realm '$REALM_NAME' already exists on the GaaS orchestra; skipping the portal path"
  else
    say "Deploy realm '$REALM_NAME' through the portal path (request_deployment → installer → conductor)"
    (cd "$GAAS_DIR" && CASALS_HOME="$CASALS_HOME" IDENTITY="$IDENTITY" TIMEOUT_S="${TIMEOUT_S:-1500}" \
       python3 tests/e2e/deploy_realm.py "$REALM_NAME")
  fi
fi

# ── URLs ──────────────────────────────────────────────────────────────────────
say "Ready. Open in the browser (Chrome/Firefox resolve *.localhost; Safari does not):"
print_urls realms-product "$REALMS_DIR/casals.json" "Realms product orchestra"
if [ "$WITH_GAAS" = 1 ]; then
  print_urls gaas "$GAAS_SHEET" "GaaS orchestra (portal + installer + realms born through it)"
  print_portal_links
fi
cat <<EOF

  Also:
    scripts/local_up.sh --urls                                       # print these again
    (cd $CASALS_DIR && CASALS_HOME=$CASALS_HOME/realms-product \\
       python3 -m casals_cli.main -e local --identity $IDENTITY show|plan|oracle $REALMS_DIR/casals.json)
    scripts/local_up.sh --down                                       # stop the replica, forget bindings
    tail -f $LOG_DIR/latest.log                                      # same stream as this console
EOF
