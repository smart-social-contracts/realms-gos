#!/usr/bin/env bash
# Cloud Agent environment bootstrap for Realms.
#
# Idempotent: safe to run repeatedly and on cached/partial state. Prepares the
# full local development toolchain used by CI and the `realms` CLI:
#   - Python 3.10 (required by the realms CLI project venv and matched by CI)
#   - dfx 0.32.0 (local Internet Computer replica + canister build/deploy)
#   - git submodules (extensions, codices)
#   - Python venv with backend deps, dev deps, the realms CLI, and test tooling
#   - npm workspace deps + the prerequisite @realmsgos/extension-bridge build
set -euo pipefail

cd "$(dirname "$0")/.."

DFX_VERSION=0.32.0

echo "==> System toolchain (Python 3.10 + dfx ${DFX_VERSION})"
if ! command -v python3.10 >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -qq
  sudo apt-get install -y python3.10 python3.10-venv python3.10-dev build-essential
fi

if [ "$(dfx --version 2>/dev/null | awk '{print $2}')" != "${DFX_VERSION}" ]; then
  curl -fsSL "https://github.com/dfinity/sdk/releases/download/${DFX_VERSION}/dfx-${DFX_VERSION}-x86_64-linux.tar.gz" -o /tmp/dfx.tar.gz
  sudo tar -xzf /tmp/dfx.tar.gz -C /usr/local/bin
  rm -f /tmp/dfx.tar.gz
fi

echo "==> Git submodules (extensions, codices)"
git submodule update --init --recursive

echo "==> Python virtualenv (3.10) + dependencies + realms CLI"
if [ ! -x venv/bin/python ] || \
   [ "$(venv/bin/python -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" != "3.10" ]; then
  rm -rf venv
  python3.10 -m venv venv
fi
# shellcheck disable=SC1091
. venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
pip install -e cli
pip install pytest playwright
# Headless Chromium for the repo's Playwright-based UI tests (see AGENTS.md).
python -m playwright install chromium

echo "==> Node workspace dependencies + prerequisite package build"
npm ci
# The SvelteKit frontends import the workspace package @realmsgos/extension-bridge,
# which must be compiled (tsc) before any `vite build`.
npm run build --workspace=packages/extension-bridge

echo "==> Realms environment ready"
