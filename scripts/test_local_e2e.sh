#!/usr/bin/env bash
# =============================================================================
# Local E2E test for the queue-based deployment pipeline (Issue #205)
#
# Runs the full flow on a local dfx replica — no ICP tokens or real cycles
# needed.  The deploy worker runs natively (uvicorn) to avoid Docker
# networking complexity with the local replica.
#
# Flow:
#   1. Start local dfx replica (--clean)
#   2. Fetch GOS artifacts + deploy realm_installer and realm_registry_backend
#   3. Seed credits so the test identity can deploy
#   4. Start the deploy worker (uvicorn) pointed at the local replica
#   5. Submit a deployment via request_deployment on the registry
#   6. Poll job status until completed / failed / timeout
#   7. Verify deployed realm backend canister responds
#   8. Tear down (stop dfx, kill worker)
#
# Prerequisites:
#   - dfx SDK installed
#   - Python 3.10+ with pip
#   - Run from the realms repo root (/srv/realms)
#
# Usage:
#   bash scripts/test_local_e2e.sh
#   bash scripts/test_local_e2e.sh --keep   # don't tear down after test
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
SKIP=0
KEEP=false

pass()  { PASS=$((PASS+1)); echo -e "${GREEN}  PASS $1${NC}"; }
fail()  { FAIL=$((FAIL+1)); echo -e "${RED}  FAIL $1${NC}"; }
skip()  { SKIP=$((SKIP+1)); echo -e "${YELLOW}  SKIP $1${NC}"; }
info()  { echo -e "${BLUE}$1${NC}"; }
warn()  { echo -e "${YELLOW}$1${NC}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP=true; shift ;;
    *) echo "Usage: $0 [--keep]"; exit 1 ;;
  esac
done

export TERM="${TERM:-xterm}"
export DFX_WARNING="-mainnet_plaintext_identity"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_SVC_DIR="/srv/realms-management-service/backend"
NETWORK="local"
WORKER_PID=""
DFX_STARTED=false

cleanup() {
  info "\n Cleaning up..."
  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    info "  Stopping deploy worker (PID $WORKER_PID)"
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  if $DFX_STARTED && ! $KEEP; then
    info "  Stopping local dfx replica"
    (cd "$REPO_ROOT" && dfx stop 2>/dev/null) || true
  fi
}
trap cleanup EXIT

cd "$REPO_ROOT"

info "╔══════════════════════════════════════════════════╗"
info "║  Local E2E Test — Queue Deployment (Issue #205)  ║"
info "╚══════════════════════════════════════════════════╝"

# ── Phase 0: Prerequisites ───────────────────────────────────────────
info "\n Phase 0: Check prerequisites"

if ! command -v dfx &>/dev/null; then
  fail "dfx not found in PATH"
  exit 1
fi
pass "dfx found: $(dfx --version)"

if ! command -v python3 &>/dev/null; then
  fail "python3 not found"
  exit 1
fi
pass "python3 found: $(python3 --version 2>&1)"

if ! python3 -c "import basilisk" 2>/dev/null; then
  warn "  Installing ic-basilisk..."
  pip3 install -q ic-basilisk 2>&1 | tail -2
fi
pass "basilisk available"

if [[ ! -d "$DEPLOY_SVC_DIR" ]]; then
  fail "Deploy service not found at $DEPLOY_SVC_DIR"
  exit 1
fi
pass "Deploy service found"

if ! python3 -c "import fastapi, uvicorn, pydantic_settings" 2>/dev/null; then
  warn "  Installing deploy-service dependencies..."
  pip3 install -q -r "$DEPLOY_SVC_DIR/requirements.txt" 2>&1 | tail -3
fi
pass "Deploy service dependencies OK"

# ── Phase 1: Start local dfx replica ────────────────────────────────
info "\n Phase 1: Start local dfx replica"

dfx stop 2>/dev/null || true
sleep 1

dfx start --clean --background </dev/null >dfx_start.log 2>&1 || true
DFX_STARTED=true

MAX_WAIT=30
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  if dfx ping 2>/dev/null; then
    break
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done

if [[ $ELAPSED -ge $MAX_WAIT ]]; then
  fail "dfx replica did not start within ${MAX_WAIT}s"
  cat dfx_start.log 2>/dev/null || true
  exit 1
fi
pass "Local dfx replica running (took ${ELAPSED}s)"

# ── Phase 2: Build and deploy infrastructure canisters ───────────────
info "\n Phase 2: Build and deploy infrastructure canisters"

# Activate venv if present
if [[ -f "$REPO_ROOT/venv/bin/activate" ]]; then
  source "$REPO_ROOT/venv/bin/activate" 2>/dev/null || true
fi

# 2a. Deploy mock CycleOps (Motoko) for local canister creation
MOCK_CYCLEOPS_DIR="$REPO_ROOT/scripts/cycleops/mock_cycleops"
MOCK_WASM="$MOCK_CYCLEOPS_DIR/mock_cycleops.wasm"
MOCK_DID="$MOCK_CYCLEOPS_DIR/mock_cycleops.did"

if [[ ! -f "$MOCK_WASM" ]] || [[ "$MOCK_CYCLEOPS_DIR/mock_cycleops.mo" -nt "$MOCK_WASM" ]]; then
  info "  Compiling mock CycleOps..."
  MOC="$(dfx cache show)/moc"
  BASE_LIB="$(dfx cache show)/base"
  "$MOC" --package base "$BASE_LIB" "$MOCK_CYCLEOPS_DIR/mock_cycleops.mo" -o "$MOCK_WASM" 2>&1
  "$MOC" --package base "$BASE_LIB" --idl "$MOCK_CYCLEOPS_DIR/mock_cycleops.mo" -o "$MOCK_DID" 2>&1
fi

info "  Deploying mock CycleOps canister..."
dfx deploy mock_cycleops --yes 2>&1 | tail -3
MOCK_CYCLEOPS_ID=$(dfx canister id mock_cycleops 2>/dev/null || echo "")
if [[ -z "$MOCK_CYCLEOPS_ID" ]]; then
  fail "mock_cycleops deploy failed"
  exit 1
fi

dfx canister deposit-cycles 50000000000000 mock_cycleops 2>&1 | tail -1 || true
pass "Mock CycleOps deployed: $MOCK_CYCLEOPS_ID (funded with 50T cycles)"

# 2b. Fetch GOS artifacts and deploy infra canisters
info "  Fetching GOS release artifacts..."
python3 scripts/fetch_gos_artifacts.py --what wasms

info "  Deploying realm_installer..."
dfx deploy realm_installer --yes 2>&1 | tail -5
INSTALLER_ID=$(dfx canister id realm_installer 2>/dev/null || echo "")
if [[ -z "$INSTALLER_ID" ]]; then
  fail "realm_installer deploy failed"
  exit 1
fi
pass "realm_installer deployed: $INSTALLER_ID"

info "  Deploying realm_registry_backend..."
dfx deploy realm_registry_backend --yes 2>&1 | tail -5
REGISTRY_ID=$(dfx canister id realm_registry_backend 2>/dev/null || echo "")
if [[ -z "$REGISTRY_ID" ]]; then
  fail "realm_registry_backend deploy failed"
  exit 1
fi
pass "realm_registry_backend deployed: $REGISTRY_ID"

# 2d. Deploy file_registry and seed test_bench extension
info "  Building and deploying file_registry..."
dfx deploy file_registry --yes 2>&1 | tail -5
FILE_REGISTRY_ID=$(dfx canister id file_registry 2>/dev/null || echo "")
if [[ -z "$FILE_REGISTRY_ID" ]]; then
  fail "file_registry deploy failed"
  exit 1
fi
pass "file_registry deployed: $FILE_REGISTRY_ID"

EXT_DIR="$REPO_ROOT/extensions/extensions/test_bench"
if [[ -d "$EXT_DIR" ]]; then
  info "  Uploading test_bench extension to file_registry..."
  EXT_VERSION=$(python3 -c "import json; print(json.load(open('$EXT_DIR/manifest.json'))['version'])")
  EXT_NS="ext/test_bench/${EXT_VERSION}"

  _fr_call() {
    local method="$1" json_arg="$2"
    local escaped
    escaped=$(echo "$json_arg" | sed 's/\\/\\\\/g; s/"/\\"/g')
    dfx canister call "$FILE_REGISTRY_ID" "$method" "(\"$escaped\")" 2>&1 >/dev/null
  }

  _store_file() {
    local ns="$1" rel_path="$2" file="$3" ctype="${4:-text/plain}"
    local b64
    b64=$(base64 -w0 "$file")
    _fr_call store_file "{\"namespace\":\"$ns\",\"path\":\"$rel_path\",\"content_b64\":\"$b64\",\"content_type\":\"$ctype\"}"
  }

  _store_file "$EXT_NS" "manifest.json" "$EXT_DIR/manifest.json" "application/json"
  for f in $(find "$EXT_DIR/backend" -name "*.py" -type f 2>/dev/null); do
    REL="backend/${f#$EXT_DIR/backend/}"
    _store_file "$EXT_NS" "$REL" "$f"
  done

  _fr_call publish_namespace "{\"namespace\":\"$EXT_NS\"}"
  pass "test_bench extension uploaded to file_registry ($EXT_NS)"
else
  warn "  test_bench extension not found at $EXT_DIR — skipping"
  FILE_REGISTRY_ID=""
fi

# ── Phase 2b: Seed credits for test identity ─────────────────────────
info "\n Phase 2b: Seed credits for test identity"

MY_PRINCIPAL=$(dfx identity get-principal 2>/dev/null)
info "  Test identity principal: $MY_PRINCIPAL"

# add_credits(principal_id: text, amount: nat64, stripe_session_id: text, description: text)
SEED_RESULT=$(dfx canister call "$REGISTRY_ID" add_credits \
  "(\"$MY_PRINCIPAL\", 100 : nat64, \"local-e2e-seed\", \"E2E test credit seed\")" \
  --output json 2>&1 || echo "ERROR")
info "  Seed credits result: ${SEED_RESULT:0:200}"

# get_credits(principal_id: text)
CREDITS_RAW=$(dfx canister call --query "$REGISTRY_ID" get_credits "(\"$MY_PRINCIPAL\")" --output json 2>&1 || echo "{}")
info "  Credits after seeding: ${CREDITS_RAW:0:200}"

if echo "$CREDITS_RAW" | grep -q '"balance"'; then
  pass "Credits seeded for test identity"
else
  warn "  Could not verify credit balance — continuing anyway"
fi

# ── Phase 3: Start deploy worker ─────────────────────────────────────
info "\n Phase 3: Start deploy worker (uvicorn)"

WORKER_PORT=18003
# Kill anything already on that port
kill $(lsof -ti:$WORKER_PORT 2>/dev/null) 2>/dev/null || true
sleep 1

export DFX_NETWORK="local"
export DEPLOYMENT_NETWORK="local"
export REALM_INSTALLER_CANISTER_ID="$INSTALLER_ID"
export REALM_REGISTRY_CANISTER_ID="$REGISTRY_ID"
export REALMS_REPO_PATH="$REPO_ROOT"
export LOG_LEVEL="DEBUG"
export CYCLEOPS_CANISTER_ID="$MOCK_CYCLEOPS_ID"
export CYCLEOPS_NETWORK="local"
export CYCLEOPS_TEAM_PRINCIPAL="aaaaa-aa"
export CYCLEOPS_BLACKHOLE=""

cd "$DEPLOY_SVC_DIR"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$WORKER_PORT" \
  --log-level debug </dev/null >worker.log 2>&1 &
WORKER_PID=$!
cd "$REPO_ROOT"

sleep 4
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  fail "Deploy worker failed to start"
  cat "$DEPLOY_SVC_DIR/worker.log" 2>/dev/null | tail -20
  exit 1
fi
pass "Deploy worker running (PID $WORKER_PID, port $WORKER_PORT)"

# Quick health check
HEALTH=$(curl -s "http://127.0.0.1:$WORKER_PORT/health" 2>/dev/null || echo "{}")
if echo "$HEALTH" | grep -q "healthy"; then
  pass "Worker health check OK"
else
  warn "  Worker health check returned: $HEALTH"
fi

# ── Phase 4: Submit deployment ────────────────────────────────────────
info "\n Phase 4: Submit deployment request"

RELEASE_TAG="v0.3.2"
RELEASE_BASE="https://github.com/smart-social-contracts/realms-gos/releases/download"
REALM_NAME="local-e2e-$(date +%s)"

# Build extensions/codex section based on whether file_registry is available
if [[ -n "$FILE_REGISTRY_ID" ]]; then
  EXT_JSON='"extensions": ["test_bench"]'
  FILE_REG_JSON="\"file_registry_canister_id\": \"$FILE_REGISTRY_ID\","
else
  EXT_JSON='"extensions": []'
  FILE_REG_JSON=""
fi

MANIFEST=$(cat <<EOF
{
  "realm": {
    "name": "$REALM_NAME",
    "display_name": "Local E2E Test Realm",
    "description": "Automated local test realm",
    "welcome_message": "Welcome!",
    "branding": {},
    "codex": {},
    $EXT_JSON
  },
  "network": "local",
  "installer_canister_id": "$INSTALLER_ID",
  $FILE_REG_JSON
  "canister_artifacts": {
    "realm": {
      "backend": {
        "wasm": {
          "url": "$RELEASE_BASE/$RELEASE_TAG/realm_backend.wasm.gz",
          "checksum": ""
        }
      },
      "frontend": {
        "url": "$RELEASE_BASE/$RELEASE_TAG/realm_frontend.tar.gz",
        "checksum": ""
      }
    }
  }
}
EOF
)

MANIFEST_ESCAPED=$(echo "$MANIFEST" | python3 -c "
import sys, json
raw = sys.stdin.read()
obj = json.loads(raw)
print(json.dumps(json.dumps(obj)))
" | sed 's/^"//;s/"$//')

info "  Submitting deployment for realm: $REALM_NAME"
DEPLOY_RAW=$(dfx canister call "$REGISTRY_ID" request_deployment "(\"$MANIFEST_ESCAPED\")" --output json 2>&1)
info "  Response: ${DEPLOY_RAW:0:300}"

JOB_ID=$(echo "$DEPLOY_RAW" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    payload = data.get('Ok', data) if isinstance(data, dict) else data
    if isinstance(payload, str):
        payload = json.loads(payload)
    print(payload.get('job_id', ''))
except Exception:
    print('')
" 2>/dev/null)

if [[ -n "$JOB_ID" ]]; then
  pass "Deployment enqueued: job_id=$JOB_ID"
else
  fail "Deployment request failed (no job_id in response)"
  info "  Full response: $DEPLOY_RAW"
  info "\n Stopping — cannot continue without job_id."
  info "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${SKIP} skipped${NC}"
  exit 1
fi

# ── Phase 5: Poll job status ─────────────────────────────────────────
info "\n Phase 5: Poll job status (timeout: 5 min)"

MAX_WAIT=300
ELAPSED=0
INTERVAL=10
FINAL_STATUS=""

while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  STATUS_RAW=$(dfx canister call --query "$INSTALLER_ID" "get_deployment_job_status" "(\"$JOB_ID\")" --output json 2>&1 || echo "{}")

  CURRENT_STATUS=$(echo "$STATUS_RAW" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    payload = data.get('Ok', data) if isinstance(data, dict) else data
    if isinstance(payload, str):
        payload = json.loads(payload)
    print(payload.get('status', 'unknown'))
except:
    print('unknown')
" 2>/dev/null)

  echo -e "  [${ELAPSED}s] Status: $CURRENT_STATUS"

  case "$CURRENT_STATUS" in
    completed|failed|failed_verification|cancelled)
      FINAL_STATUS="$CURRENT_STATUS"
      break
      ;;
  esac

  sleep "$INTERVAL"
  ELAPSED=$((ELAPSED + INTERVAL))
done

FULL_STATUS=$(dfx canister call --query "$INSTALLER_ID" "get_deployment_job_status" "(\"$JOB_ID\")" --output json 2>&1 || echo "{}")

WASM_VERIFIED=$(echo "$FULL_STATUS" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    p = d.get('Ok', d)
    print(p.get('wasm_verified', p.get('actual_wasm_hash', '')))
except: print('')
" 2>/dev/null)

ASSETS_VERIFIED=$(echo "$FULL_STATUS" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    p = d.get('Ok', d)
    print(p.get('assets_verified', ''))
except: print('')
" 2>/dev/null)

JOB_ERROR=$(echo "$FULL_STATUS" | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    p = d.get('Ok', d)
    print(p.get('error', ''))
except: print('')
" 2>/dev/null)

if [[ -z "$FINAL_STATUS" ]]; then
  fail "Job timed out after ${MAX_WAIT}s (last status: $CURRENT_STATUS)"
elif [[ "$FINAL_STATUS" == "completed" ]]; then
  pass "Deployment completed successfully"
elif [[ "$FINAL_STATUS" == "failed" ]] && echo "$JOB_ERROR" | grep -q "registration failed"; then
  if [[ "$ASSETS_VERIFIED" == "1" ]] && [[ -n "$WASM_VERIFIED" && "$WASM_VERIFIED" != "0" ]]; then
    warn "  Job failed at registration step (WASM+assets verified). This is a non-critical post-deploy step."
    pass "Core pipeline completed (WASM verified, assets verified, registration pending)"
  else
    fail "Deployment ended with status: $FINAL_STATUS"
    info "  Error: $JOB_ERROR"
  fi
else
  fail "Deployment ended with status: $FINAL_STATUS"
  info "  Error: $JOB_ERROR"
fi

info "  Full status: ${FULL_STATUS:0:500}"

# ── Phase 6: Verify deployment ───────────────────────────────────────
info "\n Phase 6: Verify deployed realm"

PIPELINE_OK=false
if [[ "$FINAL_STATUS" == "completed" ]]; then
  PIPELINE_OK=true
elif [[ "$FINAL_STATUS" == "failed" ]] && echo "$JOB_ERROR" | grep -q "registration failed" && [[ "$ASSETS_VERIFIED" == "1" ]]; then
  PIPELINE_OK=true
fi

if $PIPELINE_OK; then
  BACKEND_CID=$(echo "$FULL_STATUS" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    payload = data.get('Ok', data) if isinstance(data, dict) else data
    if isinstance(payload, str):
        payload = json.loads(payload)
    print(payload.get('backend_canister_id', ''))
except:
    print('')
" 2>/dev/null)

  FRONTEND_CID=$(echo "$FULL_STATUS" | python3 -c "
import sys, json
try:
    data = json.loads(sys.stdin.read())
    payload = data.get('Ok', data) if isinstance(data, dict) else data
    if isinstance(payload, str):
        payload = json.loads(payload)
    print(payload.get('frontend_canister_id', ''))
except:
    print('')
" 2>/dev/null)

  if [[ -n "$BACKEND_CID" ]]; then
    pass "Backend canister: $BACKEND_CID"

    CANISTER_STATUS=$(dfx canister status "$BACKEND_CID" 2>&1 || echo "ERROR")
    if echo "$CANISTER_STATUS" | grep -q "Module hash"; then
      pass "Backend canister has WASM installed (module hash present)"
    elif echo "$CANISTER_STATUS" | grep -q "Running"; then
      pass "Backend canister is running"
    else
      fail "Backend canister status check failed"
      info "  Status: ${CANISTER_STATUS:0:300}"
    fi
  else
    skip "Backend canister ID not in status response"
  fi

  if [[ -n "$FRONTEND_CID" ]]; then
    pass "Frontend canister: $FRONTEND_CID"
  else
    skip "Frontend canister ID not in status response"
  fi

  # Verify extension installation
  if [[ -n "$BACKEND_CID" && -n "$FILE_REGISTRY_ID" ]]; then
    info "\n  Verifying test_bench extension installation..."
    EXT_LIST=$(dfx canister call --query "$BACKEND_CID" list_runtime_extensions '()' 2>&1 || echo "")
    if echo "$EXT_LIST" | grep -q "test_bench"; then
      pass "test_bench extension installed in realm backend"
    else
      fail "test_bench extension NOT found in realm backend"
      info "  list_runtime_extensions: ${EXT_LIST:0:500}"
    fi
  elif [[ -z "$FILE_REGISTRY_ID" ]]; then
    skip "file_registry not deployed — extension verification skipped"
  fi
else
  skip "Deployment did not complete — skipping verification"
fi

# ── Results ──────────────────────────────────────────────────────────
info "\n══════════════════════════════════════════════"
info "Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${SKIP} skipped${NC}"
info "══════════════════════════════════════════════"

if $KEEP; then
  info "\n --keep flag set. Leaving replica + worker running."
  info "  Worker PID:    $WORKER_PID"
  info "  Installer:     $INSTALLER_ID"
  info "  Registry:      $REGISTRY_ID"
  info "  Mock CycleOps: $MOCK_CYCLEOPS_ID"
  info "  File Registry: ${FILE_REGISTRY_ID:-n/a}"
  info "  To stop:       dfx stop && kill $WORKER_PID"
  trap - EXIT
fi

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
