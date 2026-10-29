#!/bin/bash
# Fast infra deploy for active development — direct `dfx deploy` to test/staging/demo.
#
# Skips Casals publish + rollout (~20–30 min). Use while iterating; run the full
# Casals path (publish_build → rollout) before merge.
#
# Requires: deployer identity is a co-controller on the target canisters (true on
# test/staging infra during the Casals migration period).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

NETWORK="staging"
FAMILY=""
COMPONENT=""
CLEAN_BUILD=false
IDENTITY="${DFX_IDENTITY:-deployer}"

usage() {
    cat <<'EOF'
Usage: scripts/infra_dev_deploy.sh [OPTIONS]

Fast deploy of infra canisters via direct `dfx deploy` (dev only — not Casals).

Options:
  -e, --environment ENV   test | staging | demo (default: staging)
  -f, --family FAMILY     registry | installer | file-registry | marketplace | dashboard
  -c, --component COMP    backend | frontend | both (default: both)
  -i, --identity NAME     dfx identity (default: deployer)
      --clean             Clean frontend/.basilisk caches before build
  -h, --help              Show this help

Examples:
  scripts/infra_dev_deploy.sh -e staging -f registry -c backend
  scripts/infra_dev_deploy.sh -e staging -f registry -c frontend
  scripts/infra_dev_deploy.sh -e test -f installer -c backend
  scripts/infra_dev_deploy.sh -e staging -f registry -c both --clean

GaaS stack (registry, installer) uses prebuilt artifacts from gos-as-a-service
releases (see scripts/fetch_gos_artifacts.py). Before merge, align registry
frontends with Casals via publish + rollout for realm families only.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--environment) NETWORK="$2"; shift 2 ;;
        -f|--family) FAMILY="$2"; shift 2 ;;
        -c|--component) COMPONENT="$2"; shift 2 ;;
        -i|--identity) IDENTITY="$2"; shift 2 ;;
        --clean) CLEAN_BUILD=true; shift ;;
        -h|--help) usage ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; usage ;;
    esac
done

if [[ -z "$FAMILY" ]]; then
    echo -e "${RED}Error: --family is required${NC}"
    usage
fi

if [[ "$NETWORK" != "test" && "$NETWORK" != "staging" && "$NETWORK" != "demo" ]]; then
    echo -e "${RED}Error: environment must be test, staging, or demo${NC}"
    exit 1
fi

if [[ -z "$COMPONENT" ]]; then
    COMPONENT="both"
fi

if [[ "$COMPONENT" != "backend" && "$COMPONENT" != "frontend" && "$COMPONENT" != "both" ]]; then
    echo -e "${RED}Error: --component must be backend, frontend, or both${NC}"
    exit 1
fi

case "$FAMILY" in
    registry)
        BACKEND_CANISTER="realm_registry_backend"
        FRONTEND_CANISTER="realm_registry_frontend"
        FRONTEND_DIST="$REPO_ROOT/.external-assets/realm_registry_frontend/dist"
        ;;
    installer)
        BACKEND_CANISTER="realm_installer"
        FRONTEND_CANISTER=""
        FRONTEND_DIST=""
        ;;
    file-registry)
        BACKEND_CANISTER="file_registry"
        FRONTEND_CANISTER="file_registry_frontend"
        FRONTEND_DIST="$REPO_ROOT/.external-assets/file_registry_frontend/dist"
        ;;
    marketplace)
        BACKEND_CANISTER="marketplace_backend"
        FRONTEND_CANISTER="marketplace_frontend"
        FRONTEND_WORKSPACE="marketplace_frontend"
        ;;
    dashboard)
        BACKEND_CANISTER=""
        FRONTEND_CANISTER="platform_dashboard_frontend"
        FRONTEND_WORKSPACE="platform_dashboard_frontend"
        ;;
    *)
        echo -e "${RED}Error: unknown family '$FAMILY'${NC}"
        exit 1
        ;;
esac

if [[ "$COMPONENT" == "backend" && -z "$BACKEND_CANISTER" ]]; then
    echo -e "${RED}Error: family '$FAMILY' has no backend canister${NC}"
    exit 1
fi

if [[ "$COMPONENT" != "backend" && -z "$FRONTEND_CANISTER" ]]; then
    echo -e "${RED}Error: family '$FAMILY' has no frontend canister${NC}"
    exit 1
fi

export TERM="${TERM:-xterm}"
export DFX_WARNING=-mainnet_plaintext_identity
export DFX_NETWORK="$NETWORK"

cd "$REPO_ROOT"

# Basilisk builds need an isolated venv (see AGENTS.md / publish_build.py).
# The marketplace pins its own dependencies (ic-python-db 0.14 for its sorted
# rankings), so it builds in a venv of its own, as in publish_build.py.
PINNED="$REPO_ROOT/src/marketplace_backend/requirements.txt"
if [[ "$FAMILY" == "marketplace" && -f "$PINNED" ]]; then
    VENV="$REPO_ROOT/.venv-basilisk-marketplace_backend"
else
    VENV="$REPO_ROOT/.venv-basilisk"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
    echo -e "${YELLOW}Creating basilisk venv at $VENV${NC}"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    if [[ "$VENV" == *-marketplace_backend ]]; then
        "$VENV/bin/pip" install -q -r "$PINNED"
    else
        "$VENV/bin/pip" install -q \
            ic-basilisk==0.15.1 ic-basilisk-toolkit==0.5.3 \
            ic-python-db==0.12.0 ic-python-logging==0.3.4
    fi
fi
export PATH="$VENV/bin:$PATH"

dfx identity use "$IDENTITY" >/dev/null

echo -e "${BLUE}╭───────────────────────────────────────╮${NC}"
echo -e "${BLUE}│ ⚡ Fast infra deploy (dfx direct)      │${NC}"
echo -e "${BLUE}╰───────────────────────────────────────╯${NC}"
echo -e "${BLUE}Family:     $FAMILY${NC}"
echo -e "${BLUE}Component:  $COMPONENT${NC}"
echo -e "${BLUE}Network:    $NETWORK${NC}"
echo -e "${BLUE}Identity:   $(dfx identity get-principal)${NC}"
echo -e "${YELLOW}Note: Casals catalog is not updated — run publish + rollout before merge.${NC}"
echo ""

fetch_gos_artifacts() {
    local what="${1:-all}"
    echo -e "${GREEN}📥 Fetching GOS artifacts (--what ${what})${NC}"
    python3 "$REPO_ROOT/scripts/fetch_gos_artifacts.py" --what "$what"
}

deploy_backend_remote() {
    local canister_key="$1"
    echo -e "${GREEN}🚀 Deploying backend $canister_key (remote WASM from dfx.json) → $NETWORK${NC}"
    dfx deploy "$canister_key" --network "$NETWORK" --mode upgrade --yes
}

deploy_backend() {
    local canister="$1"
    local canister_key="$2"
    export CANISTER_CANDID_PATH="$REPO_ROOT/src/$canister/$canister.did"
    local cid
    cid=$(python3 -c "import json; print(json.load(open('$REPO_ROOT/canister_ids.json')).get('$canister_key', {}).get('$NETWORK', ''))")
    if [[ -z "$cid" ]]; then
        echo -e "${RED}No canister id for $canister_key on $NETWORK in canister_ids.json${NC}"
        exit 1
    fi
    echo -e "${GREEN}🔨 Building backend $canister${NC}"
    rm -rf "$REPO_ROOT/.basilisk/$canister"
    dfx build "$canister" --network "$NETWORK"
    local wasm="$REPO_ROOT/.basilisk/$canister/${canister}.wasm.gz"
    if [[ ! -f "$wasm" ]]; then
        local raw="$REPO_ROOT/.basilisk/$canister/${canister}.wasm"
        if [[ -f "$raw" ]]; then
            gzip -kf "$raw"
        fi
    fi
    if [[ ! -f "$wasm" ]]; then
        echo -e "${RED}WASM not found after build: $wasm${NC}"
        exit 1
    fi
    echo -e "${GREEN}🚀 Installing backend $canister ($cid) → $NETWORK${NC}"
    dfx canister install "$cid" --network "$NETWORK" --mode upgrade --wasm "$wasm"
}

deploy_external_frontend() {
    local canister_key="$1"
    local dist_dir="$2"
    local cid
    cid=$(python3 -c "import json; print(json.load(open('$REPO_ROOT/canister_ids.json')).get('$canister_key', {}).get('$NETWORK', ''))")
    if [[ -z "$cid" ]]; then
        echo -e "${RED}No canister id for $canister_key on $NETWORK in canister_ids.json${NC}"
        exit 1
    fi

    if [[ "$CLEAN_BUILD" == true ]]; then
        echo -e "${YELLOW}🧹 Cleaning fetched frontend dist${NC}"
        rm -rf "$dist_dir"
    fi

    fetch_gos_artifacts frontend

    if [[ ! -d "$dist_dir" ]]; then
        echo -e "${RED}Fetched frontend dist not found: $dist_dir${NC}"
        exit 1
    fi

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    cp -a "$dist_dir" "$tmpdir/dist"
    cat > "$tmpdir/dfx.json" <<EOF
{
  "version": 1,
  "canisters": {
    "frontend": { "type": "assets", "source": ["dist"] }
  },
  "networks": {
    "$NETWORK": { "providers": ["https://icp0.io"], "type": "persistent" }
  }
}
EOF
    echo "{\"frontend\": {\"$NETWORK\": \"$cid\"}}" > "$tmpdir/canister_ids.json"

    echo -e "${GREEN}🚀 Uploading frontend assets ($cid) → $NETWORK${NC}"
    local deployer_principal
    deployer_principal=$(dfx identity get-principal)
    for perm in Prepare Commit; do
        dfx canister call "$cid" grant_permission \
            "(record { to_principal = principal \"$deployer_principal\"; permission = variant { $perm } })" \
            --network "$NETWORK" --no-wallet 2>/dev/null || true
    done
    (cd "$tmpdir" && dfx deploy frontend --network "$NETWORK" --yes --no-wallet --no-asset-upgrade)
    rm -rf "$tmpdir"
    trap - EXIT
}

deploy_frontend() {
    local canister="$1"
    local workspace="$2"
    local canister_key="$3"
    local fe_dir="$REPO_ROOT/src/$workspace"
    local dist_dir="$fe_dir/dist"
    local cid
    cid=$(python3 -c "import json; print(json.load(open('$REPO_ROOT/canister_ids.json')).get('$canister_key', {}).get('$NETWORK', ''))")
    if [[ -z "$cid" ]]; then
        echo -e "${RED}No canister id for $canister_key on $NETWORK in canister_ids.json${NC}"
        exit 1
    fi

    if [[ "$CLEAN_BUILD" == true ]]; then
        echo -e "${YELLOW}🧹 Cleaning frontend build cache ($workspace)${NC}"
        rm -rf "$fe_dir/.svelte-kit" "$fe_dir/node_modules/.vite" "$dist_dir"
    fi

    echo -e "${GREEN}🔨 Building frontend ($workspace) for $NETWORK${NC}"
    # Test-mode VITE_* must not be baked into staging/demo/ic builds — flags come
    # from the backend at runtime via get_runtime_flags / set_canister_config_json.
    (cd "$REPO_ROOT" && export DFX_NETWORK="$NETWORK" && \
        unset VITE_TEST_MODE VITE_TEST_MODE_II_BYPASS VITE_TEST_MODE_USER_SELF_REGISTRATION \
              VITE_TEST_MODE_DEMO_DATA VITE_TEST_MODE_SKIP_TERMS VITE_TEST_MODE_SKIP_PASSPORT_ZKPROOF && \
        npm run build --workspace="$workspace")

    if [[ ! -d "$dist_dir" ]]; then
        echo -e "${RED}Build did not produce $dist_dir${NC}"
        exit 1
    fi

    # Remote asset canisters: repo-root `dfx deploy` does not sync files. Use an
    # isolated mini-project (same pattern as realms-management-service worker).
    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    cp -a "$dist_dir" "$tmpdir/dist"
    cat > "$tmpdir/dfx.json" <<EOF
{
  "version": 1,
  "canisters": {
    "frontend": { "type": "assets", "source": ["dist"] }
  },
  "networks": {
    "$NETWORK": { "providers": ["https://icp0.io"], "type": "persistent" }
  }
}
EOF
    echo "{\"frontend\": {\"$NETWORK\": \"$cid\"}}" > "$tmpdir/canister_ids.json"

    echo -e "${GREEN}🚀 Uploading frontend assets to $canister ($cid) → $NETWORK${NC}"
    # Casals-managed asset canisters restrict Prepare/Commit; controllers need explicit grants.
    local deployer_principal
    deployer_principal=$(dfx identity get-principal)
    for perm in Prepare Commit; do
        dfx canister call "$cid" grant_permission \
            "(record { to_principal = principal \"$deployer_principal\"; permission = variant { $perm } })" \
            --network "$NETWORK" --no-wallet 2>/dev/null || true
    done
    (cd "$tmpdir" && dfx deploy frontend --network "$NETWORK" --yes --no-wallet --no-asset-upgrade)
    rm -rf "$tmpdir"
    trap - EXIT
}

if [[ "$COMPONENT" == "backend" || "$COMPONENT" == "both" ]]; then
    case "$FAMILY" in
        registry)
            fetch_gos_artifacts wasms
            deploy_backend_remote "realm_registry_backend"
            ;;
        installer)
            fetch_gos_artifacts wasms
            deploy_backend_remote "realm_installer"
            ;;
        file-registry)
            fetch_gos_artifacts wasms
            deploy_backend_remote "file_registry"
            ;;
        marketplace) deploy_backend "marketplace_backend" "marketplace_backend" ;;
    esac
fi

if [[ "$COMPONENT" == "frontend" || "$COMPONENT" == "both" ]]; then
    case "$FAMILY" in
        registry) deploy_external_frontend "realm_registry_frontend" "$FRONTEND_DIST" ;;
        file-registry) deploy_external_frontend "file_registry_frontend" "$FRONTEND_DIST" ;;
        marketplace) deploy_frontend "marketplace_frontend" "marketplace_frontend" "marketplace_frontend" ;;
        dashboard) deploy_frontend "platform_dashboard_frontend" "platform_dashboard_frontend" "platform_dashboard_frontend" ;;
    esac
fi

echo ""
echo -e "${GREEN}╭───────────────────────────────────────╮${NC}"
echo -e "${GREEN}│ ✅ Fast infra deploy complete          │${NC}"
echo -e "${GREEN}╰───────────────────────────────────────╯${NC}"
