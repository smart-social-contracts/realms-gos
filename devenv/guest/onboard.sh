#!/usr/bin/env bash
# The developer journey on a fresh machine, run as the `dev` user in the VM:
# install the realms CLI, start a local realm, scaffold a hello-world extension
# and a codex that depends on it, publish both, launch the realm with the
# codex, then check the extension from the CLI and in a real browser.
#
# Env:
#   REALMS_SRC     realms-gos source tree copied into the VM (default ~/realms-gos)
#   CLI_SOURCE     checkout (pipx install $REALMS_SRC/cli, default) | pypi
#   CLI_VERSION    realms-gos version on PyPI when CLI_SOURCE=pypi (default: latest, pre-releases included)
#   REALM_PORT     local network port (default 8000)
#   ARTIFACTS      output directory (default ~/devenv-artifacts)
set -euo pipefail

REALMS_SRC="${REALMS_SRC:-$HOME/realms-gos}"
CLI_SOURCE="${CLI_SOURCE:-checkout}"
CLI_VERSION="${CLI_VERSION:-}"
CLI_VERSION="${CLI_VERSION#v}"
REALM_PORT="${REALM_PORT:-8000}"
ARTIFACTS="${ARTIFACTS:-$HOME/devenv-artifacts}"
EXT_DIR=my-hello-world
EXT_ID=my_hello_world
CODEX_DIR=my-hello-codex
CODEX_ID=my_hello_codex
EXPECTED="Hello, World!"

mkdir -p "$ARTIFACTS"
exec > >(tee -a "$ARTIFACTS/onboard.log") 2>&1
export PATH="$HOME/.local/bin:$PATH"
STARTED=$(date +%s)
step() { printf '\n=== [%4ss] %s\n' "$(( $(date +%s) - STARTED ))" "$*"; }
trap 'rc=$?; step "FAILED (exit $rc)"; cp -r "$HOME/.realms/local/state.json" "$ARTIFACTS/" 2>/dev/null || true; exit $rc' ERR

step "machine"
lsb_release -ds; uname -r; nproc; free -h | head -2
node --version; npm --version; python3 --version; pipx --version
systemctl is-active gdm && echo "GNOME display manager: active"

step "install the realms CLI ($CLI_SOURCE)"
if [ "$CLI_SOURCE" = "pypi" ]; then
  # --pre: until 0.7.0 is final, the only releases with `realms local` are rcs.
  # Retries: right after a release, PyPI can take a few minutes to serve it.
  for attempt in 1 2 3 4 5 6; do
    pipx install --force --pip-args=--pre "realms-gos${CLI_VERSION:+==$CLI_VERSION}" && break
    [ "$attempt" = 6 ] && exit 1
    sleep 60
  done
else
  pipx install --force "$REALMS_SRC/cli"
fi
realms local --help > /dev/null
echo "realms CLI: $(command -v realms)"

step "realms local start (installs icp-cli on first use)"
realms local start --port "$REALM_PORT" --yes

mkdir -p "$HOME/work"
cd "$HOME/work"
rm -rf "$EXT_DIR" "$CODEX_DIR"

step "realms new extension $EXT_DIR"
realms new extension "$EXT_DIR"

step "realms new codex $CODEX_DIR --extension $EXT_ID"
realms new codex "$CODEX_DIR" --extension "$EXT_ID"

step "realms local publish"
realms local publish "$EXT_DIR" "$CODEX_DIR"

step "realms local setup --codex $CODEX_ID"
realms local setup --codex "$CODEX_ID"

step "realms local status"
realms local status --json | tee "$ARTIFACTS/status.json"
python3 - "$ARTIFACTS/status.json" "$CODEX_ID" <<'EOF'
import json, sys
status = json.load(open(sys.argv[1]))
assert status["launch"] == "completed", f"launch: {status['launch']}"
assert status.get("codex") == sys.argv[2], f"codex: {status.get('codex')}"
print("realm launched with codex", status["codex"])
EOF

step "backend: realms local call $EXT_ID greet"
realms local call "$EXT_ID" greet '{}' --json | tee "$ARTIFACTS/greet.json"
python3 - "$ARTIFACTS/greet.json" "$EXPECTED" <<'EOF'
import json, sys
result = json.load(open(sys.argv[1]))
assert result == {"success": True, "response": sys.argv[2]}, result
print("greet ->", result["response"])
EOF

step "browser: press the hello button"
cd "$REALMS_SRC/devenv/browser-check"
npm install --no-audit --no-fund
node check.mjs --extension "$EXT_ID" --expect "$EXPECTED" --out "$ARTIFACTS"

step "collect"
cp "$HOME/.realms/local/state.json" "$ARTIFACTS/state.json"
cp -r "$HOME/work/$CODEX_DIR/manifest.json" "$ARTIFACTS/codex-manifest.json"
cp -r "$HOME/work/$EXT_DIR/manifest.json" "$ARTIFACTS/extension-manifest.json"

# Leave Firefox on the realm for whoever opens the desktop.
REALM_URL=$(python3 -c "import json;print(json.load(open('$ARTIFACTS/status.json'))['url'])")
echo "$REALM_URL/extensions/$EXT_ID" > "$ARTIFACTS/realm-url.txt"
if [ -S "/run/user/$(id -u)/wayland-0" ]; then
  XDG_RUNTIME_DIR="/run/user/$(id -u)" WAYLAND_DISPLAY=wayland-0 \
    setsid firefox --new-window "$REALM_URL/" >/dev/null 2>&1 < /dev/null &
  sleep 15
fi

step "PASS: realm launched with $CODEX_ID; $EXT_ID's hello button shows \"$EXPECTED\""
