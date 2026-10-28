# Agents Guide — Realms

## Model Selection

Preferred models for agents working in this repo:

| Model | Role |
|---|---|
| **Composer 2.5** | Default worker — medium-to-low complexity tasks, implementation, exploration, routine changes |
| **Kimi K3** | Orchestrator — high-level planning, complex reasoning, critical tasks, and verification. **Must spin up Composer 2.5 workers** for delegated implementation, exploration, and routine work. |

**Strategy:** Maximize use of **Composer 2.5** — it is cheap but smart enough for most day-to-day work. Reserve **Kimi K3** for complex tasks and for verifying the most critical work delegated to Composer 2.5.

When running as **Kimi K3**, spin up **Composer 2.5** subagents (Task tool / workers) for anything that does not require Kimi K3's reasoning — implementation, file search, bulk exploration, test runs, and medium-to-low complexity edits. Do not do that work inline in Kimi K3 when a Composer 2.5 worker can handle it.

---

## Toolchain: prefer `icp` over `dfx`

Realms still has **`dfx.json` and no `icp.yaml` yet** — be honest about that. When you
touch deploy or tooling, **prefer `icp`** and plan migration to `icp.yaml`; do not
paper over the gap indefinitely.

Until Realms has `icp.yaml`, many scripts and examples still need dfx. Prefer `icp`
when it can do the job; use `dfx` for unmigrated workflows and **plan/do the
migration** when you work those paths.

Install/upgrade icp-cli: `npm i -g @icp-sdk/icp-cli` (latest on srv1: **1.3.0**;
verify with `icp --version`).

When invoking dfx, also export `TERM=xterm` and
`DFX_WARNING=-mainnet_plaintext_identity` (see [dfx in this environment](#dfx-in-this-environment)).

---

## Project Structure

```
realms/
├── extensions/ (submodule → realms-extensions)
│   └── extensions/<ext_id>/
│       ├── manifest.json
│       ├── backend/entry.py          # Python RPC handler (optional)
│       └── frontend-rt/              # Svelte 5 ESM bundle
│           ├── src/
│           └── vite.config.ts        # Lib-mode build
├── src/realm_frontend/               # Main SvelteKit frontend
├── src/realm_backend/                # Python canister backend
├── .github/workflows/
│   ├── ci-pr.yml / ci-main.yml       # Lint, unit, `casals up` e2e on a local replica (realms-e2e.yml)
│   └── release.yml                   # Version bump, GitHub release, PyPI; optional publish to the production file_registry
├── (casals is NOT a submodule — clone smart-social-contracts/casals separately for its CLI; platform provisioner of gos-as-a-service)
├── casals.json                       # The product orchestra (marketplace, file registry, token, NFT, demo realm) — `casals up`
├── canister_ids.json                 # Operator inventory of live ids, written by `casals export` (never a source of truth in code)
├── scripts/
│   └── publish_build.py              # Build+publish engine used by release.yml
├── docs/OPERATIONS.md                # How to build and converge the orchestra
└── docs/reference/
```

`extensions/` is a git submodule. Commit/push there first, then update the ref in `realms`.

---

## Deploying Code Changes

Every environment is declared in [`casals.json`](casals.json) and converged by
`casals up` (see [`docs/OPERATIONS.md`](docs/OPERATIONS.md)). There is no
per-network table anywhere in this repo: canister ids, controllers, hosts and
test flags live in the sheet's `environments` block, and the running canisters
read theirs at runtime (`set_canister_config_json`, `get_setup_state().shared_tokens`).
CI (`ci-pr.yml`) refuses a principal/canister-id literal in `src/`, `scripts/`,
`cli/realms` or the workflows; read live ids with `casals export`.

**One command:** `scripts/up.sh -e <env>` = build → `casals pin` → `casals up`
→ `casals export` → `realms domains apply` → `realms files publish` +
`realms marketplace publish` → verify. Locally `scripts/local_up.sh` wraps it
(replica, identity, URLs; `--gaas` adds the GaaS orchestra). Production:
`scripts/up.sh -e production --identity <session> --yes` where `<session>` is
a short-lived `icp identity delegation` from `prod-identity` (one HSM touch;
`Casals/docs/OPERATIONS.md`, "Hardware keys"), with `DFX_HSM_PIN`,
`CLOUDFLARE_API_TOKEN` and `CASALS_HOME` set. The table
below is what the phases do, for a single step.

| What changed | Path |
|---|---|
| Anything in the orchestra (code, sheet, packages) | `scripts/up.sh -e production …` (docs/OPERATIONS.md, "One command") |
| Product canister code (realm backend/frontend, marketplace, token, NFT) | Build (`scripts/up.sh --build-only`; recipes also in `release.yml`), `casals pin`, then `casals up -e production` from the Casals repo |
| The conductor / file registry / multisig themselves | Same `casals up`; the sheet's `registry.wasms` pins them |
| Extensions / codices bundles only | `scripts/up.sh -e production --identity prod-identity --publish-only`, or `realms files publish --network ic --registry <fleet-file-registry>` + `realms marketplace publish` |
| A realm artifact the installer serves to new portal realms | `scripts/publish_build.py --environment production` (the release workflow does this too) |
| Existing portal-created realms | `realms mundus deploy <descriptor>` — the descriptor's `infra` block names the environment's registry / installer / file registry ids (`realms mundus deploy --help`) |
| A new realm on a live GOS | `realms new spec.json --gaas-config <gaas new --output-file>` |
| Registry / wizard UI (`gos.earth`) | [gos-as-a-service](https://github.com/smart-social-contracts/gos-as-a-service): its own `casals.json` + `casals up` |

`realms deploy --folder <realm>` is the dfx path for a generated realm on a local
replica only.

### Release candidates

A `vX.Y.Z-rcN` release is a build you expect to ship as `vX.Y.Z` unchanged.
It is not how a fix reaches staging: try changes there with a local build
(a `local:` row in a working copy of `casals.staging.json`, then
`casals upgrade --wasm` / `--content`) or a `scripts/publish_build.py`
build from main, and cut the RC once the change works. Before the next RC, the previous
one is signed and published, or deleted. An RC carries the release summary,
is marked as a pre-release, and is never Latest.

### Environments

`local` (a fresh replica, `build_variant: test`, test flags on) and `production`
(`build_variant: production`, test flags off) — both in `casals.json`. The demo
stand's `converged_when` asserts the build variant, so a wrong-variant deploy
never converges.

---

## Build variants: production vs test WASM

Test-mode code paths are **compiled out** of a production build, not merely
switched off at runtime. A stale `test_mode` row in the database, a compromised
admin session, or an extension writing to the `Realm` entity all have nothing to
switch on, because the code is not in the artifact.

**Production is the default.** Every build is production unless you explicitly
ask for the test variant.

```bash
python3 scripts/pack_realm_backend.py                    # production (default)
python3 scripts/pack_realm_backend.py --variant test     # test
```

### How it works

`basilisk` packs the whole `src/realm_backend` tree, so anything left in that
tree ships. The variants therefore live **outside** it, in
`scripts/build_variants/{production,test}.py`, and the packer copies the selected
one over `src/realm_backend/core/build_variant.py` before packing and restores
production afterwards. Whatever was not selected is *absent*, not disabled.

After packing, the WASM is byte-scanned for `REALMS_TEST_BUILD_AUTH_BYPASS`. A
production build containing it fails; a test build missing it fails too — a
one-sided check would pass vacuously.

| | Production | Test |
|---|---|---|
| `is_test_mode()` | always `False` | reads the realm flag |
| `is_ii_bypass_active()` | always `False` | reads the realm flag |
| `test_flags_allowed(...)` | always `False` | network-gated |
| Sentinel in WASM | absent | present |

`status()` reports `build_variant` and `network`, so you can tell a production
artifact from a test one without trusting the deploy log.

### Frontend

The realm frontend has the matching split, via a Vite compile-time constant.
Unset means production; only the exact value `test` opts in.

```bash
npm run build                                # production bundle
REALMS_BUILD_VARIANT=test npm run build      # test bundle
python3 scripts/check_frontend_variant.py src/realm_frontend/dist production
```

`npm run dev` keeps test identities available. `publish_build.py --variant test`
builds both halves as test and labels the artifact `main-test.*` so a test build
is never picked up by a production `main` rollout.

**Never deploy a test variant to a production network.** Test builds exist for
CI and for demo environments that need deterministic identities.

---

## Realm config (branding, registration, runtime flags)

Canisters converge from the sheet; their *state* is configured at runtime. The
sheet's `config_call` items (see the demo stand in `casals.json`) drive
`set_canister_config_json` / `set_test_flags`; a portal realm gets the same
through the installer (`configure` on the gaas side). Branding is uploaded by the
founder from the setup wizard (or `realms files publish-branding`). Registration
in the realm registry is done by the installer when it finishes a job.

---

## Local Development

```bash
# Frontend-only (fast)
scripts/deploy_local_dev.sh -s .realms/realm_* -f

# Clean frontend rebuild (CSS/style changes)
scripts/deploy_local_dev.sh -s .realms/realm_* -f -c

# Backend
scripts/deploy_local_dev.sh -s .realms/realm_* -b
```

Runtime extension bundles load dynamically — no full frontend redeploy needed, only file_registry upload + install.

### Local Extension Dev Server (instant feedback)

For visual iteration on any runtime extension UI without deploying. Works for all 28+ extensions with zero per-extension setup.

**How it works:** A shared dev harness (`extensions/dev-server/`) runs a local Vite server that mounts any extension's Svelte component with real data from the test canister. It provides the same wrapper the canister provides: nav bar, layout shell, Tailwind CSS, and a full `RealmExtensionContext` (backend actor, callSync/callAsync, stores). Images are proxied from the real canister. API calls go through `/api` proxy to `icp0.io`, matching the standard ICP dev workflow. None of the dev harness files affect production builds.

**Setup (once):**

```bash
cd extensions/dev-server && npm install
```

**Run (from any extension):**

```bash
cd extensions/extensions/<ext_id>/frontend-rt
npm run dev
```

Or directly:

```bash
cd extensions/dev-server
node bin/dev.js <ext_id>
```

Opens on `http://localhost:5555`. Edit the extension's Svelte source, save, see changes instantly via HMR. When happy, deploy with the fast remote deploy below.

**Configuration:** Edit `extensions/dev-server/dev-config.json` to point at a different canister or network.

**Workflow:** `edit → see locally → happy? → deploy to IC`

---

### Fast Remote Extension Deploy (~26s)

For iterating on runtime extension bundles against test canisters without CI. No git commit required — edit, deploy, check, repeat. Commit only when the result is correct.

All commands run from the **`realms/` project root**.

**Setup (once per session):**

```bash
export TERM=xterm
export DFX_WARNING=-mainnet_plaintext_identity
dfx identity use my_dev_identity_1
```

`my_dev_identity_1` (principal `ah6ac-cc73l-...`) is a controller of the test canisters.

**Deploy cycle (edit → live in ~26s):**

```bash
realms files build --extensions <ext_id> \
  && realms files publish --network ic --extensions-only --extensions <ext_id> \
  && dfx canister call <backend_canister_id> install_extension_from_registry \
     '("{\"registry_canister_id\": \"<file_registry_id>\", \"ext_id\": \"<ext_id>\", \"version\": \"<version>\"}")' \
     --network ic
```

| Step | Command | Time | What it does |
|------|---------|------|-------------|
| Build | `realms files build --extensions <ext_id>` | ~4s | Runs `npm install && npm run build` in `frontend-rt/` |
| Publish | `realms files publish --network ic ...` | ~8s | Uploads bundle to the file_registry canister |
| Install | `dfx canister call ... install_extension_from_registry` | ~14s | Backend pulls bundle from registry and installs it |

**Concrete example** — `public_dashboard` on Agora (test):

```bash
realms files build --extensions public_dashboard \
  && realms files publish --network ic --extensions-only --extensions public_dashboard \
  && dfx canister call <realm-backend> install_extension_from_registry \
     '("{\"registry_canister_id\": \"<file-registry>\", \"ext_id\": \"public_dashboard\", \"version\": \"1.3.0\"}")' \
     --network ic
```

The `install_extension_from_registry` call goes directly to the realm backend, bypassing the installer service. It fetches the bundle from the file_registry and copies the frontend files to the asset canister in one call.

Same thing via CLI: `realms extension registry-install --canister <backend> --registry <file_registry> --extension-id <ext_id> --version <ver> --network ic`.
Use this (not `runtime-install`) for **frontend-only** extensions such as `public_dashboard` — they have no `entry.py`.

For a wizard realm, look up IDs first (do not use the Agora/Dominion/Syntropia rows below):

```bash
# test registry backend — resolve_slug is an UPDATE (do not pass --query)
dfx canister call <realm-registry> resolve_slug '("<slug>")' --network ic --output json
```

**Canister IDs for the install call** (from the Known Canister IDs table and the descriptor):

Resolve the call target and the environment's file registry with `casals export`
(the realm's ids) and `resolve_slug` on the realm registry (a portal realm's ids).

**Realm frontend/backend redeploy** (changes to `src/realm_frontend/` or
`src/realm_backend/`, not just a runtime extension bundle): the demo stand
converges from `casals up` after a rebuild; a portal realm uses the
[direct path](#direct-runtime-install-no-file-registry-no-casals-installer) or
`realms mundus deploy <descriptor> --version build` with the environment's ids in
the descriptor's `infra` block.

**Constraints:**
- The extension bundle must stay under ~200KB for `files publish` to succeed (file_registry instruction limit). Keep heavy libraries (leaflet, h3-js) loaded at runtime via `fetch()` + `eval()` instead of bundling them.
- The extension `version` in `manifest.json` must match the version in the `dfx canister call`. Bump the version when you need to force cache invalidation.
- After finishing iteration, commit the changes to the submodule and update the ref in `realms` (see "Deploying Extension Changes" above).

### Direct runtime install (no file registry, no Casals installer)

Push packages straight to the realm canisters with dfx.
**No `request_deployment` job, no realm-installer polling.**
Use this for wizard `/r/<slug>/` realms, when the installer is slow/stuck, or when
the user said **fast off-chain**.

| What changed | Direct command | Notes |
|---|---|---|
| **Realm backend** (`src/realm_backend/`) | Build WASM locally → `dfx canister install --mode upgrade` | ~25s; preserves canister state |
| **Extension** (has `backend/entry.py`) | `realms extension runtime-install` | Backend via `install_extension`; frontend via asset-canister `store` |
| **Extension** (frontend-only, e.g. `public_dashboard`) | `realms extension registry-install` (or `install_extension_from_registry`) | `runtime-install` errors: `entry.py not found` |
| **Codex** (`kind: codex` packages) | `realms codex runtime-install --run-init` | Backend via `install_extension`; runs codex `init` hook |

> **Not the same as `mundus deploy`.** `realms mundus deploy --version build`
> builds locally, then calls `request_deployment` on the **realm registry** and
> polls the **realm installer** (`fltjm-…` on test) — that is GOS `realm_installer`,
> not Casals. Use mundus only for sheet realms when that installer is healthy (~90s).

**Setup (once per session):**

```bash
export TERM=xterm-256color DFX_WARNING=-mainnet_plaintext_identity
export PATH="$PWD/.venv-basilisk/bin:$PATH"   # basilisk refuses to build outside this venv
export CANISTER_CANDID_PATH=src/realm_backend/realm_backend.did
dfx identity use deployer   # or my_dev_identity_1 on test
```

**Wizard / portal realm on test** (e.g. `https://test.gos.earth/r/realmtest6/`):

```bash
# 1. Resolve canister IDs (test registry)
# resolve_slug is an UPDATE — do not pass --query
dfx canister call <realm-registry> resolve_slug \
  '("realmtest6")' --network ic --output json
# → backend_canister_id, frontend_canister_id

# 2. Backend WASM — leftover-free pack with Cedar template only (~25s)
python3 scripts/pack_realm_backend.py
gzip -c .basilisk/realm_backend/realm_backend.wasm > /tmp/realm_backend.wasm.gz
dfx canister install <backend-id> \
  --wasm /tmp/realm_backend.wasm.gz --mode upgrade --network ic

# 3. Extensions (one at a time; full resync-frontends can IC0506)
realms files build --extensions <ext_id>
realms files publish --network ic --extensions-only --extensions <ext_id>
realms extension registry-install \
  --canister <backend-id> \
  --registry <file-registry> \
  --extension-id <ext_id> --version <ver> --network ic
```

Do **not** wrap those IDs in a homemade mundus YAML.

**`resolve_slug` is an update**, not a query. Do not pass `--query`.

**Wizard frontend redeploy** (required if you touch `src/realm_frontend/` or are
asked to redeploy a `/r/<slug>/` realm, not just the backend):

1. Save live `/custom/logo.png` (and background) **before** any asset sync.
2. Build the extension-bridge workspace first, then the realm frontend:
   `npm run build --workspace=@realmsgos/extension-bridge`, then
   `npm run build` in `src/realm_frontend`.
3. Write `dist/canister_ids.js` **before** deploy (text candid `store` of JS
   with quotes is painful; hex-blob `store` works). Include `realm_backend`,
   `file_registry`, `derivation_origin` (`https://test.realmsgos.org` on test),
   `portal_url`, and `test_mode_ii_bypass`.
4. Copy branding into `dist/custom/`. Deploy the asset canister from a
   **temp dfx project** whose `dfx.json` `networks.test` points at the live
   frontend id — `cd` into that project or dfx reports `Network not found: test`.
5. Asset sync **wipes `/ext/`**. Re-grant the backend `Commit`. Then
   `resync_extension_frontends` (expect ~5–7 min; it can time out after most
   extensions are restored). `pin_directory` is **not** on this asset canister.
6. For any installed version **missing from file_registry**, build
   `realms-extensions/extensions/<id>/frontend-rt` and `store` the full
   `dist/` (not just `index.js`). Sandboxed UIs such as `member_dashboard`
   load `index.html` + hashed `assets/*`; the shared postbuild `index.js`
   stub is 19 bytes and is not the UI.
7. `HEAD` every `/ext/{id}/{ver}/frontend/dist/index.js` and the hashed
   assets referenced by `index.html`. Then hard-refresh the portal URL.

**Staging canister IDs:**

> Live ids are not written down here: `casals export` (sheet stands) or the realm
> registry's `resolve_slug` (portal realms) give the current ones.

**Full example — justice courts on a portal realm (verified Aug 2026):**

```bash
cd /path/to/realms

# 1. Backend WASM — leftover-free pack with Cedar template only (~25s)
python3 scripts/pack_realm_backend.py
gzip -c .basilisk/realm_backend/realm_backend.wasm > /tmp/realm_backend.wasm.gz
dfx canister install <realm-backend> \
  --wasm /tmp/realm_backend.wasm.gz --mode upgrade --network ic

# 2. Codex (seeds court hierarchy via init → seed_justice)
realms codex runtime-install \
  --canister <realm-backend> \
  --source-dir codices/codices/syntropia \
  --network ic --run-init

# 3. Extension (backend + frontend bundle + initialize hook)
realms files build --extensions justice_litigation   # build frontend-rt/dist first
realms extension runtime-install \
  --canister <realm-backend> \
  --source-dir extensions/extensions/justice_litigation \
  --frontend-canister <realm-frontend> \
  --network ic
```

Repeat steps 2–3 on TestSyntropia1 or DemoSyntropia1 using the backend/frontend IDs
from the table above (same `--source-dir codices/codices/syntropia`).

**What the pure dfx backend upgrade skips (usually fine on staging):**

- Casals protective snapshot / automatic rollback
- `set_canister_config` post-deploy (frontend ID, version string, test flags — these
  persist from prior deploys; `--mode upgrade` keeps state)
- Branding upload + `/custom/` pinning on the frontend canister

**When to use which path:**

| Situation | Use |
|---|---|
| Wizard / portal realm (`/r/<slug>/`), or user said **fast off-chain** | **Direct runtime install** (this section) — look up IDs with `resolve_slug` |
| Single-realm dev iteration, registry or installer down | **Direct runtime install** (this section) |
| The demo stand of `casals.json` | rebuild, then `casals up` (docs/OPERATIONS.md) |
| Extension-only change, registry healthy | [Fast Remote Extension Deploy](#fast-remote-extension-deploy-26s) above |
| Quarters / multi-realm / pre-merge authoritative deploy | `realms files publish` → registry install → `casals up` |

`realms extension resync-frontends` can hit `IC0506` (no reply) when many bundles copy at once. Restore the missing extension with `registry-install` instead.

Direct install only affects the canister you target. It does **not** update
the file registry catalog — run `realms files publish` before merge so other realms and
quarter bootstrap can pull the new packages.

**Gotchas (learned staging Jul 2026):**

1. **Deploy to the realm you're actually viewing.** The Casals-managed staging stands
   (table above) are *not* manual-test or quarter realms created
   via the registry wizard. If the UI shows e.g. `ManualTest410Syntropia`, look
   up its backend/frontend IDs first:

   ```bash
   # Staging realm_registry_backend canister (authoritative ID table: gos-as-a-service)
   dfx canister call <realm-registry> list_realms '()' \
     --network ic --query | grep -i manualtest
   ```

   Then pass those IDs to `--canister` / `--frontend-canister`.

2. **Manifest version ≠ UI version after direct extension install.** The info
   tooltip reads `manifest.json` (e.g. `0.4.0`) but the tab bar comes from the
   **frontend JS bundle**. The loader resolves the bundle path via
   `get_extension_frontend_info`, which historically read stale `_source.json`
   from an earlier registry install (e.g. still pointing at `0.3.8`). Symptom:
   new backend works, tooltip shows new version, UI looks unchanged.

   **Fix:** bump `version` in `manifest.json`, upload the bundle to
   `/ext/{id}/{new-version}/frontend/dist/index.js` on the realm **frontend**
   asset canister, then ensure `_source.json` matches (backend now syncs this
   on `install_extension`; older backends may need a one-off patch):

   ```bash
   # Verify what the loader will use:
   dfx canister call <backend-id> get_extension_frontend_info \
     '("{\"extension_id\": \"justice_litigation\"}")' --network ic --query

   # Should return the new version. If not, patch _source.json:
   dfx canister call <backend-id> install_extension \
     '("{\"extension_id\":\"justice_litigation\",\"files\":{\"_source.json\":\"{\\\"registry_canister_id\\\":\\\"<file-registry>\\\",\\\"version\\\":\\\"0.4.0\\\"}\"}}")' \
     --network ic
   ```

   Hard-refresh the browser (Ctrl+Shift+R). In DevTools → Network, confirm the
   request is `/ext/justice_litigation/0.4.0/frontend/dist/index.js`, not an
   older version.

3. **Codex direct install and emoji.** IC Python's narrow unicode build rejects
   emoji in extension/codex sources during `open(..., 'w')`. The CLI strips
   astral characters on upload; registry installs are unaffected.

4. **Manual-test realms need all three steps.** Backend WASM (if ggg/codex hooks
   changed), codex `runtime-install --run-init`, and extension
   `runtime-install --frontend-canister` — same as capital, just different
   canister IDs.

---

## Environment Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pip install -e cli
```

Verify: `realms --help` and `icp --version` (require **≥ 1.3.0**;
`npm i -g @icp-sdk/icp-cli`). For unmigrated dfx-only paths, `dfx --version`
is the escape hatch — prefer migrating to `icp` instead.

---

## dfx in this environment

See [Toolchain: prefer `icp` over `dfx`](#toolchain-prefer-icp-over-dfx) above.

dfx 0.30.2 crashes with `ColorOutOfRange`
(`panicked at src/dfx/src/main.rs: Failed to set stderr output color`) unless the
terminal is set up correctly. Always export these before any dfx command:

```bash
export TERM=xterm
export DFX_WARNING=-mainnet_plaintext_identity
```

The first fixes the color panic, the second suppresses the plaintext identity warning
that blocks execution on `test`/`staging` networks.

**If the panic still occurs** (including inside wrappers like `realms files publish`,
which shells out to dfx): the shell has `NO_COLOR` and/or `FORCE_COLOR` set, and dfx
panics on the conflict. **Unset both** and use a color-capable TERM:

```bash
env -u NO_COLOR -u FORCE_COLOR TERM=xterm-256color DFX_WARNING=-mainnet_plaintext_identity \
  realms files publish --network ic --extensions-only --extensions <ext_id>
```

Counter-intuitively, `NO_COLOR=1` / `TERM=dumb` does **not** reliably avoid the panic
— dfx needs a real color terminal, not a colorless one.

---

## Cursor Cloud specific instructions

This VM has no `icp.yaml` and no working OS keyring.

### Launching a cloud agent on a **named** environment (Environment2)

Live IC work (deployer PEM, `casals up`, `realms new`) lives on the named
Cloud Environment **Environment2**
([dashboard](https://cursor.com/dashboard/cloud-agents/environments/e/a79bd4f2-a059-11f1-b532-320a589b8025)).
Its secrets (`IC_IDENTITY_PEM_B64`, optional `DEMO_IDENTITY1_*`) are **not**
injected unless that environment is the one attached to the run.

**Do not** treat `Task(environment: "cloud")` as “run on Environment2”. That
flag only means “Cursor-hosted VM”. It uses the **repo’s saved default**
Cloud Environment (personal, then team). There is **no** Task-tool parameter
for environment name. Writing “Environment2” in the prompt text does **not**
select it.

| How you launch | Selects Environment2? |
|---|---|
| `Task` tool with `environment: "cloud"` | **No** — repo default only |
| Prompt text saying “use Environment2” | **No** |
| Cloud Agents API `env.name` (below) | **Yes** |
| User starts the agent from the Cloud Agents UI with Environment2 chosen | **Yes** |

To target Environment2 from an agent (or any script), use the
[Cloud Agents API](https://cursor.com/docs/cloud-agent/api/endpoints)
`POST /v1/agents` with a named environment. `env` is mutually exclusive with
explicit `repos` when selecting a named Cursor-hosted environment.

The API does **not** accept the Cursor IDE login session. A **user API key**
is required (`crsr_…` from [Cursor Dashboard → API Keys](https://cursor.com/dashboard/integrations)).
Creating the key is free; Cloud Agent **runs** bill at API model pricing (same
as the Agents window).

**Operator laptop:** store the key **only** in
`~/.config/cursor/secrets.env` (`chmod 600`), one line:

```
CURSOR_API_KEY=crsr_…
```

Never commit that file, never paste the key into the repo, `AGENTS.md`, or chat.
Agent shells are often non-interactive and **will not** load `~/.bashrc` — source
the file before every API call:

```bash
set -a
# shellcheck disable=SC1090
. "$HOME/.config/cursor/secrets.env"
set +a
test -n "${CURSOR_API_KEY:-}" || { echo "missing ~/.config/cursor/secrets.env" >&2; exit 1; }

curl -sS -X POST https://api.cursor.com/v1/agents \
  -u "$CURSOR_API_KEY:" \
  -H 'Content-Type: application/json' \
  -d '{
    "prompt": { "text": "<task>" },
    "name": "Demo gaas+realms new",
    "env": { "type": "cloud", "name": "Environment2" }
  }'
```

(`env.type` is `cloud` for Cursor-hosted VMs; `env.name` is the dashboard
environment name, e.g. `Environment2`. The response `url` is
`https://cursor.com/agents/bc-…`.)

**Before any IC write**, confirm the attached environment:

1. Agent page → hover the repo name → it must say **Environment2**.
2. In the VM: `test -n "$IC_IDENTITY_PEM_B64"`. If empty, you are on the
   **wrong** environment — stop. Do not invent a PEM, do not use
   `my_dev_identity_1`, do not destroy demo canisters.

If `IC_IDENTITY_PEM_B64` is empty on a run the user expected on Environment2,
the launch path was the Task default, not the named env. Re-launch via the
API (or ask the user to start the agent from the Cloud Agents UI with
Environment2 selected).

**Git.** Do **not** create a branch, commit, push, or open a PR unless the user
explicitly asks. Stay on the current checkout. Treat this as a standing user
instruction for this repo, including Cloud agent runs.

**Identity.** Import `IC_IDENTITY_PEM_B64` as a plaintext icp (and dfx) identity
named `deployer`. Do not use the baked `my_dev_identity_1` keyring identity.

```bash
printf '%s' "$IC_IDENTITY_PEM_B64" | base64 -d > /tmp/deployer.pem
icp identity import deployer /tmp/deployer.pem --storage plaintext -f
# same PEM as dfx identity `deployer` if a dfx asset-sync is required
```

**`realms new` founder (`demo_identity1`).** `deployer` is the wrong principal
for browser login. Cloud agents cannot click Internet Identity. Put a
**plaintext II session** in Environment-scoped secrets (not the git repo):

| Secret | Value |
|---|---|
| `DEMO_IDENTITY1_PEM_B64` | `base64` of `icp identity export demo_identity1` |
| `DEMO_IDENTITY1_DELEGATION_B64` | `base64` of `~/.local/share/icp-cli/identity/delegations/demo_identity1.json` |

Create/refresh that identity on a machine **with a browser** (session expires;
re-link and update the secrets when `icp identity principal --identity demo_identity1` fails):

```bash
# Derivation origin for demo realms — not demo.gos.earth (that is the portal host).
icp identity link web demo_identity1 --app https://demo.realmsgos.org --storage plaintext
icp identity principal --identity demo_identity1
# must match the principal you see logged in on https://demo.gos.earth
```

Cloud VM bootstrap (install script / agent prompt):

```bash
if [ -n "${DEMO_IDENTITY1_PEM_B64:-}" ]; then
  printf '%s' "$DEMO_IDENTITY1_PEM_B64" | base64 -d > /tmp/demo_identity1.pem
  extra=()
  if [ -n "${DEMO_IDENTITY1_DELEGATION_B64:-}" ]; then
    printf '%s' "$DEMO_IDENTITY1_DELEGATION_B64" | base64 -d > /tmp/demo_identity1.delegation.json
    extra=(--delegation /tmp/demo_identity1.delegation.json)
  fi
  icp identity import demo_identity1 --from-pem /tmp/demo_identity1.pem \
    --storage plaintext -f "${extra[@]}"
fi
# then, from the realms checkout that has `realms new`:
# realms new spec.json --identity demo_identity1 --network ic --yes
```

PEM without the delegation JSON is a **different principal** than II. The
founder would not match browser login. That principal also needs **≥ 5 credits**
on the demo registry (`realms registry billing add_credits`).

**icp network.** Every `icp` call against IC mainnet / test / staging canisters
needs an explicit replica (there is no project `icp.yaml`):

```bash
icp canister call <id> <method> ... \
  -n https://icp0.io --root-key mainnet --identity deployer
```

**Basilisk.** Use a clean venv (`~/.venv-basilisk` is fine) with only
`ic-basilisk`, `ic-basilisk-toolkit`, `ic-python-db`, `ic-python-logging`.
`$PWD/.venv-basilisk` may not exist on this image.

**Cycles.** The deployer's cycles wallet may be nearly empty (~40B). Backend
upgrades spend the **target canister's** cycles. Do not block on minting.

**Playwright.** Do not run `playwright install chromium` (needs sudo). Launch
with `executable_path="/usr/local/bin/google-chrome"`. Walk `page.frames`;
the portal page's top-level `body` text is empty. Use
`https://test.gos.earth/r/<slug>/?skip_ii=true&test_mode=true`.

Wizard `/r/<slug>/` frontend redeploys: see
[Direct runtime install](#direct-runtime-install-no-file-registry-no-casals-installer)
("Wizard frontend redeploy").

---

## Debugging Python canisters (`__browse__` / `__shell__`)

Realm backends (and other Basilisk canisters built with
[`basilisk`](https://github.com/smart-social-contracts/basilisk) /
[`ic-basilisk-toolkit`](https://github.com/smart-social-contracts/ic-basilisk-toolkit))
can expose two agent-oriented endpoints. **Use them liberally** for
investigating live canister state — they are the fastest way to answer “what is
actually on-chain?” without redeploying or adding one-off query methods.

Enable at build time (realm backend already has both):

```python
__basilisk_features__ = ["shell", "browse"]
```

Full reference: [`../basilisk/AGENTS.md`](../basilisk/AGENTS.md).

### `__browse__` — read-only inspection (query, cheap)

Structured JSON access to stable maps/sets/vecs. Good first step when you need
counts, keys, or individual records without writing Python.

```bash
export TERM=xterm DFX_WARNING=-mainnet_plaintext_identity
dfx identity use my_dev_identity_1

# Schema of stable structures
dfx canister call <canister_id> __browse__ \
  '("{\"action\": \"schema\"}")' --query --network ic

# List keys / fetch one item (see basilisk AGENTS.md for actions)
dfx canister call <canister_id> __browse__ \
  '("{\"action\": \"len\", \"map\": \"User\"}")' --query --network ic
```

No special permission beyond ordinary query access (subject to any endpoint
guard you added). Prefer `__browse__` over ad-hoc `status()` fields when you
need entity-level detail.

### `__shell__` — Python REPL inside the canister (update)

Executes arbitrary Python against the live `ggg` entity model (`User`,
`Quarter`, `Realm`, …). Persistent namespace **per caller principal** — variables
from one call are visible on the next.

```bash
dfx canister call <canister_id> __shell__ \
  '("from ggg import User; print(len(list(User.instances())))")' \
  --network ic --identity my_dev_identity_1
```

Gated by `@require(Operations.SHELL_EXECUTE)`, but **IC controllers bypass** all
permission checks (`core/access.py`). So `__shell__` works when your dfx
principal is in that canister's **controller list**.

| Canister | Typical controllers | `__shell__` with `my_dev_identity_1` |
|---|---|---|
| Capital realm backend | Casals + CycleOps + deployer (varies by env) | Usually **yes** |
| Auto-provisioned quarter | Casals + capital + inherited capital controllers | Usually **yes** (after Casals provision) |
| Casals-managed orchestra stand | Casals + CycleOps only | **no** (use Casals relay below) |

When you are not a controller, relay through Casals (Casals *is* a quarter
controller):

```bash
dfx canister call <casals_id> canister_exec \
  '("{\"canister\":\"agora-quarter-2\",\"code\":\"from ggg import User; print(len(list(User.instances())))\"}")' \
  --network ic --identity my_dev_identity_1
```

**Casals relay signs as Casals**, not as the end user. Use it only when you need
controller-level access to a stand you cannot call directly. To debug as a
specific Internet Identity principal, use the section below — do not relay.

### `__shell__` as a specific Internet Identity principal

**Do this whenever you need to run Python as a given II user** (their `User`
record, Cedar owner checks, per-principal REPL namespace). Do **not** build a
REPL extension for it — `__shell__` already exists; the only requirement is
signing the update as that principal.

Prefer **`icp`** over dfx. Interactive wrapper: `basilisk shell`.

Internet Identity is pairwise per frontend origin. Realms pins a canonical
`derivationOrigin` (see [`docs/reference/IDENTITY_AND_ASSISTANT.md`](docs/reference/IDENTITY_AND_ASSISTANT.md)).
`icp identity link web` must use `--app` matching that origin, or the linked
principal will **not** match `User.id` in the realm.

| Environment | `--app` (derivation origin) |
|---|---|
| staging | `https://staging.realmsgos.org` |
| demo | `https://demo.realmsgos.org` |
| test | `https://test.realmsgos.org` |

```bash
# 1. Link II (browser auth). Repeat if the session expired.
icp identity link web alice --app https://staging.realmsgos.org

# 2. Confirm the principal is the realm User, not a cli.id.ai pair
icp identity principal --identity alice
# Compare with User.id on chain (via __browse__ or a known UI login).

# 3. Run Python as that principal
basilisk shell --canister <canister_id> --network ic --identity alice \
  -c 'from ggg import User; print(len(list(User.instances())))'

# Same call without basilisk:
icp canister call <canister_id> __shell__ \
  '("from ggg import User; print(len(list(User.instances())))")' \
  --identity alice
```

That principal still needs **`shell.execute`** (developer profile) **or** to be
an IC controller of the canister. A deploy PEM (`my_dev_identity_1`, `deployer`)
is a **different** principal from the II user — controller god-mode is fine for
raw state, but Cedar ownership and the REPL namespace will not match that user.

| Goal | Approach | Works? |
|---|---|---|
| `__shell__` as II user X | `icp identity link web` + `--app` + `--identity` | **Yes**, if X has `shell.execute` or is a controller |
| `__shell__` as X via deploy PEM | `--identity my_dev_identity_1` | Different principal than X |
| `__shell__` as X via Realms PoA (`on_behalf_of`) | `grant_delegation_json` | **No** — `__shell__` ignores PoA |
| `__shell__` as X via Casals `canister_exec` | Casals relay | Runs as **Casals**, not X |
| Default `link web` without `--app` | `cli.id.ai` derivation | **Wrong principal** vs realm `User.id` |

`icp identity delegation request/sign/use` is IC-level session delegation (same
as linking a key). It does **not** let you act as someone else's II without
their signature, and it is not Realms PoA
([`docs/reference/DELEGATION.md`](docs/reference/DELEGATION.md)).

Do **not** add a REPL extension for CLI debugging or impersonation. An in-realm
REPL UI would only be UX (browser session already has the correct II
delegation); it would still be `ic.caller()` and could not impersonate.

### Quarter scaling and controllers

Casals **inherits the stand commander's full IC controller list** when minting a
canister (`lifecycle._resolve_provision_controllers`): new quarters get Casals,
CycleOps (when enabled), the capital backend as commander, plus every controller
the capital already has (deploy key, etc.). No separate post-provision step.

Existing quarters provisioned before this change keep their old controller set
until updated manually or re-provisioned.

---

## Gotchas

1. **`realms files publish` must target the environment you install into.** Publishing to the `staging` registry won't be visible to `demo` or `test` realms.
2. **Any change inside `extensions/` (including manifest.json edits) requires `realms files publish` before installing.** Extensions are pulled from the file registry — if you skip `realms files publish`, the registry still has stale manifests/bundles and the install uses old data. Always: `realms files publish` → install / rollout.
3. **Agora is prone to timeouts.** Retry a single failed realm: `-f targets=agora`.
4. **`reinstall` wipes all state.** Use `upgrade` unless you want a clean slate.
5. **Frontend bundles are built from source.** Run `realms files build` (`scripts/up.sh` does) to compile `frontend-rt/` before publishing. No need to commit `dist/index.js`.
6. **Mundus is not “skip the installer.”** It calls `request_deployment` on the realm registry and waits on **realm_installer**. If you meant the direct dfx path, do not write a temp mundus descriptor for a wizard slug.
7. **A frontend (re)install wipes `/ext/`.** A pending mundus job *or* a direct wizard frontend asset sync will 404 every extension until you `registry-install` / `resync_extension_frontends` / store the missing bundles. `pin_directory` is not on this asset canister.
8. **`runtime-install` needs `entry.py`.** Frontend-only extensions (`public_dashboard`) use `registry-install`.
9. **Installed extension versions can be ahead of test `file_registry`.** `registry-install` cannot restore a version the registry does not have. Build from `realms-extensions` and `store` the full `dist/` (including hashed `assets/*` and `index.html`). A 19-byte postbuild `index.js` stub is not the UI for sandboxed extensions such as `member_dashboard`.
10. **`resolve_slug` is an update.** Do not pass `--query`.

---

## Rules

- **Default deploy path**: the sheet. Rebuild, then `casals up` (docs/OPERATIONS.md);
  never an imperative `dfx canister install` on a canister the conductor controls.
  For a wizard/portal realm (`/r/<slug>/`) or when the user says **fast off-chain**,
  use [direct runtime install](#direct-runtime-install-no-file-registry-no-casals-installer)
  — never a homemade mundus YAML. **Registry/installer:** fetch prebuilt artifacts from
  gos-as-a-service (`scripts/fetch_gos_artifacts.py`) — **building registry from
  source in this repo is no longer possible**. **Other infra:** `scripts/infra_dev_deploy.sh`
  or `publish_build.py` for file_registry, marketplace, dashboard. **Extensions:**
  `realms files publish` to publish bundles into the file registry.
- **Visually verify every UI change before reporting back.** After deploying a frontend or extension change, open the page in the browser and confirm the result matches the requirements. Do not report completion until you have checked the deployed page yourself. If the visual check reveals issues, fix and redeploy in a loop until the result is correct.
- **Do not commit, create a branch, or open a PR unless the user explicitly
  asks.** Operational work (redeploys, `__shell__`, live checks) stays on the
  current checkout. Docs or code edits wait for an explicit “commit / branch /
  PR” instruction. A Cloud agent’s default git policy does **not** override this.
- Always use `mode=upgrade` for production/test deploys (`reinstall` wipes state).
- Prefer **`icp identity default <name>`** for identity selection. Realms still has
  unmigrated dfx paths: use **`dfx identity use <name>`** there. The
  deployer identity is `deployer`.
- To run `__shell__` as a **specific Internet Identity principal**, use
  `icp identity link web` with `--app` matching the env derivation origin (see
  **Debugging Python canisters**). Do not build a REPL extension for this;
  Realms PoA and Casals `canister_exec` do not impersonate that user.
- **Monitor every workflow you trigger.** After launching a workflow, watch it until it goes green. If it fails, diagnose the error, fix it, re-push, and re-trigger — repeat until the run succeeds. Never leave a red workflow behind.

---

## Browser Testing

### Cursor built-in browser

The `@Browser` tool (Cursor IDE browser tab) works with ICP canister frontends. Use `browser_navigate` to open a canister URL and `browser_snapshot` to inspect the accessibility tree.

**Gotcha — test mode identity is stateful**: the test environment uses `TEST_MODE_II_BYPASS=true`, which auto-logs-in with a deterministic hardcoded identity (seed `0xED, 0x57` → principal `2eqns-rmzes-...`). This identity is the **same across all browser sessions**. If a previous test activated, modified, or consumed resources for that principal, subsequent sessions will see the post-modification state. Before concluding a feature is broken, check whether the test identity's on-chain state already reflects a previous test run:

```bash
dfx canister call <canister_id> is_principal_activated '("<principal>")' --network ic
```

**Gotcha — extension UIs live in an iframe**: runtime extensions render inside
`<iframe class="realm-frame">`, and `@Browser` cannot reach inside one. Anything under
`/extensions/<ext_id>` — tabs, forms, panels — is therefore unclickable with that tool, which
looks like a broken page rather than a tool limit. Use Playwright instead (below); it can
enter frames. Do not "fix" this by moving extension UIs out of the iframe: that frame is the
isolation boundary for third-party extension code.

### Playwright (headless Chromium)

For automated, repeatable, screenshot-based testing — or when you need to capture console output, intercept network calls, or interact with forms programmatically — use Playwright.

#### Setup (one-time)

```bash
pip install playwright        # if not already installed
playwright install chromium   # downloads ~110 MB headless shell
```

`playwright install --with-deps chromium` requires sudo and will fail in most agent environments. The `--with-deps` flag is not needed if system libraries are already present (they usually are).

**Cursor Cloud:** `playwright install chromium` fails without sudo. Use system Chrome
instead (`executable_path="/usr/local/bin/google-chrome"`). See
[Cursor Cloud specific instructions](#cursor-cloud-specific-instructions).

#### Inline test script pattern

```python
import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Capture console output — must be set up BEFORE page.goto()
        console_msgs = []
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))

        await page.goto("https://<canister_id>.icp0.io/some-page", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(3000)  # extra wait for async canister calls

        await page.screenshot(path="/tmp/test_screenshot.png")
        body = await page.inner_text("body")
        print(body[:500])

        # Interact with form elements
        input_el = await page.query_selector('input[type="text"]')
        if input_el:
            await input_el.fill("some value")
            btn = await page.query_selector('button:has-text("Submit")')
            if btn:
                await btn.click()
                await page.wait_for_timeout(5000)
                await page.screenshot(path="/tmp/test_after_submit.png")

        for msg in console_msgs:
            if "Permission" not in msg:
                print(msg)

        await browser.close()

asyncio.run(test())
```

#### Key tips

- **Screenshots** are saved to `/tmp/` and can be viewed with the `Read` tool (it supports PNG).
- **Reaching extension UIs**: they render in a nested frame, so `page.click(...)` on the top-level
  page will miss. Walk `page.frames` and pick the one containing your target:

```python
target = next(f for f in page.frames if await f.locator("text=Advanced").count() > 0)
await target.locator("text=Advanced").first.click()
```
- **`wait_for_timeout(3000–8000)`** after navigation or clicks — canister calls are async and can take several seconds on the IC boundary nodes.
- **Same test mode identity caveat** applies — Playwright also gets the hardcoded `2eqns-rmzes-...` principal in test environments.
- **Intercepting responses**: use `page.route("**/*", handler)` to log or modify HTTP requests/responses.
- **Network failures**: use `page.on("requestfailed", ...)` to catch failed API calls.

### When to use what

| Scenario | Tool |
|---|---|
| Quick visual check of a page | Cursor `@Browser` |
| Verify canister logic (queries, updates, guards) | `dfx canister call` |
| Automated UI test with screenshots and form interaction | Playwright |
| Full user flow (UI → canister → UI update) | Playwright |
| Admin operations (create codes, toggle modes) | `dfx canister call` with controller identity |

---

## Further Reading

- [`.AGENTS/realms-deployment-paths.svg`](.AGENTS/realms-deployment-paths.svg) — Deployment decision tree (Casals, mundus, extensions, release)
- `AGENTS.md` — Agent/operator guide (deploy paths, fast iteration)
- [Cursor Cloud specific instructions](#cursor-cloud-specific-instructions) — named env (Environment2) vs Task `environment: cloud`, plaintext PEM, `icp` replica flags, Playwright Chrome path
- `docs/OPERATIONS.md` — Product orchestra (`casals up` on `casals.json`, `*.realmsgos.org`)
- `docs/reference/RUNTIME_EXTENSION_STAGING_DEPLOY.md` — Layered deploy runbook
- `docs/reference/EXTENSION_ARCHITECTURE.md` — Extension lifecycle
- `docs/reference/PRIVATE_DATA_SHARING.md` — End-to-end encrypted, consent-based data sharing for extensions (own entity + scope kind + `ctx.crypto`)
- `cli/README.md` — CLI command reference
