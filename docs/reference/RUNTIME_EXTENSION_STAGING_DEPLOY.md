# Deploying runtime-loaded extensions to staging — Path A (Dominion)

This runbook deploys the **Layer 2 (runtime extension frontends)** work
from [#168](https://github.com/smart-social-contracts/realms-gos/issues/168)
onto a single staging realm. It is the "fast proof" path: the
realm_backend / realm_frontend code goes through the existing
GitHub Actions pipeline, and the file_registry deploy + bundle
publication + runtime install of the extension are wrapped by
`scripts/deploy_runtime_extension_to_staging.sh` (runnable either
locally or from CI via the **Runtime Extension Deploy** workflow).

The plan targets **Dominion** on staging using the canister-ID-pinned
descriptors `deployments/staging-dominion-{backend,frontend}.yml`.

> ⚠️ **Important — realm numbering on staging is NOT 1→Dominion.**
>
> The pre-existing `staging-realm{1,2,3}-*.yml` descriptors use
> `id_in_registry: 1..3`, which is a **1-based INDEX into
> `realm_registry_backend.list_realms()`**. On the current staging
> registry the ordering is:
>
> | id_in_registry | Realm | realm_backend canister id |
> |---|---|---|
> | 1 | Syntropia | `jnope-2yaaa-aaaac-beh4a-cai` |
> | 2 | Agora | `ihbn6-yiaaa-aaaac-beh3a-cai` |
> | 3 | Dominion | `ijdaw-dyaaa-aaaac-beh2a-cai` |
>
> i.e. `staging-realm1-backend.yml` actually upgrades **Syntropia**,
> not Dominion (contrary to its header comment). The new
> `staging-dominion-*.yml` descriptors pin by canister ID and are
> unambiguous.

## Prerequisites

You can run the rollout two ways:

1. **Locally** — from your own dfx identity that has controller rights
   and a funded cycles wallet on staging. Use
   `scripts/deploy_runtime_extension_to_staging.sh` directly.
2. **From CI** — via the new **Runtime Extension Deploy** workflow,
   which executes the same script inside
   `ghcr.io/smart-social-contracts/icp-dev-env:b26e7e9` using the
   `secrets.IC_IDENTITY_PEM` identity (same one used by the main
   **Deploy** workflow). This is preferred because it matches the
   production rollout flow and reuses existing secrets.

### Identity + cycles

> ❗ **`file_registry` is a brand-new canister on staging; creating it
> costs ~0.5 T cycles from the deploying identity's wallet.** Plain
> `dfx canister upgrade` calls (what the main Deploy workflow does)
> do not require cycles on the deployer, but `dfx deploy file_registry`
> does.
>
> The CI identity behind `secrets.IC_IDENTITY_PEM` currently has
> `0.000 TC` on staging (verified 2026-04-16, see
> [run 24530392518](https://github.com/smart-social-contracts/realms-gos/actions/runs/24530392518)
> which failed with `Insufficient cycles balance to create the canister`).
>
> **Before re-running step 2 (the Runtime Extension Deploy workflow),
> you must either:**
>
> - Top up the CI identity's cycles wallet on staging (~1 T cycles
>   is plenty), **or**
> - Deploy `file_registry` once manually from a funded identity and
>   pass its canister id to the workflow via the `file_registry`
>   input (skips the `dfx deploy file_registry` step).

To run **locally** you additionally need:

- A `dfx` identity with controller rights on the staging canisters and
  a funded wallet:
  ```
  dfx identity use my-staging-identity
  dfx identity get-principal
  DFX_WARNING=-mainnet_plaintext_identity dfx cycles balance --network staging
  ```
- A local checkout of `realms-extensions` next to `realms`
  (or pass `--extensions-repo <path>`).
- `node`, `npm`, `python3`, `curl`, `dfx` available.

## Step 1 — Deploy `realm_backend` code to Dominion ✅ done

The new query method `get_extension_frontend_info` and the `_source.json`
write logic must be live on Dominion before the script's install step
can succeed.

This step was completed in
[run 24530259620](https://github.com/smart-social-contracts/realms-gos/actions/runs/24530259620)
(commit `435596fd`). To re-run for a newer commit:

```
gh workflow run Deploy --repo smart-social-contracts/realms-gos \
  --ref feat/layered-deployment \
  -f descriptor=deployments/staging-dominion-backend.yml \
  -f source=checkout \
  -f commit=<full-sha> \
  -f mode_override=upgrade
```

> ℹ️ The descriptor `staging-dominion-backend.yml` pins the target by
> canister id (`ijdaw-dyaaa-aaaac-beh2a-cai`). Do **not** use
> `staging-realm1-backend.yml`; that one resolves to Syntropia.
>
> **Mode override must be `upgrade`** — the descriptor default is
> `reinstall`, which would wipe stable storage.

Sanity-check:
```
DFX_WARNING=-mainnet_plaintext_identity \
  dfx canister call --network staging --query \
    ijdaw-dyaaa-aaaac-beh2a-cai \
    get_extension_frontend_info '("{\"extension_id\":\"test_bench\"}")'
```
It should return `success:false, error:"No registry source recorded…"`
until step 2 completes.

## Step 2 — Publish the extension bundle and install it  🚫 blocked on cycles

Two ways to run it:

### 2a — From CI (preferred, once cycles are available)

```
gh workflow run "Runtime Extension Deploy" \
  --repo smart-social-contracts/realms-gos \
  --ref feat/layered-deployment \
  -f network=staging \
  -f realm_backend=ijdaw-dyaaa-aaaac-beh2a-cai \
  -f extension_id=test_bench \
  -f extensions_branch=feat/runtime-frontend-bundle \
  -f commit=<realms-sha>
```

See [run 24530392518](https://github.com/smart-social-contracts/realms-gos/actions/runs/24530392518)
for the shape of this workflow's logs. That run failed with
`Insufficient cycles balance` because the CI wallet on staging is
empty — see the Prerequisites section.

If you pre-deployed `file_registry` from a funded identity, add
`-f file_registry=<its-canister-id>` to skip canister creation.

### 2b — Locally (requires a funded staging identity)

```
cd realms
dfx identity use my-staging-identity

bash scripts/deploy_runtime_extension_to_staging.sh \
    --realm-backend ijdaw-dyaaa-aaaac-beh2a-cai \
    --network staging
```

What the script does (idempotently):

1. Locates `realms-extensions/extensions/test_bench/` and reads the
   version from its manifest.
2. Resolves `file_registry`'s canister id on staging via
   `dfx canister id file_registry --network staging`. If absent, runs
   `dfx deploy file_registry --network staging` to create one and prints
   the new id (record it!).
3. Builds `realms-extensions/extensions/test_bench/frontend-rt/` via
   `vite build --lib` → `dist/index.js`.
4. Uploads `manifest.json`, `backend/*.py`, and `frontend/dist/index.js`
   to `file_registry` under `ext/test_bench/<version>/...`, then
   `publish_namespace`.
5. Verifies the bundle is reachable over HTTP at
   `https://<file_registry>.icp0.io/ext/test_bench/<version>/frontend/dist/index.js`
   with correct MIME and CORS.
6. Calls
   `realm_backend.install_extension_from_registry(...)` so the realm
   records `_source.json`, then calls `get_extension_frontend_info` to
   confirm runtime discovery returns the right registry id + version.

If `file_registry` was newly created in step 2, add its canister id to
`dfx.json` so future deploys reuse it instead of creating duplicates:

```jsonc
"file_registry": {
  ...
  "remote": {
    "id": {
      "staging": "<paste_the_canister_id_here>"
    }
  },
  ...
}
```

(That edit is not done automatically — the script just prints a
reminder.)

## Step 3 — Deploy `realm_frontend` code to Dominion

1. Open the same `Deploy` workflow.
2. Run with:
   - Descriptor: `deployments/staging-realm1-frontend.yml`
   - Source: `checkout`
   - Commit: HEAD of `feat/layered-deployment`
   - Mode override: leave blank (frontend deploys are always reinstall
     of asset storage; the descriptor default is correct).
3. Wait for it to succeed.

## Step 4 — Smoke test in a real browser

Open Dominion's frontend (replace with the actual canister id, available
in the realm registry or in the deployment logs):

```
https://<dominion_realm_frontend>.icp0.io/extensions/test_bench
```

You should see:

- A green card titled **`test_bench (runtime-loaded)`** with a
  **`v0.1.3`** badge (matching the version you published).
- Two buttons:
  `extension_sync_call → test_bench.get_data` and
  `list_runtime_extensions`.

In devtools (Network tab), confirm:

- One request to
  `https://<file_registry>.icp0.io/ext/test_bench/0.1.3/frontend/dist/index.js`
  returns 200 with `Content-Type: application/javascript`.
- No CSP violations in the Console — the realm_frontend CSP allows
  `script-src` from `https://*.icp0.io`.

Click `extension_sync_call → test_bench.get_data`:

- **Anonymous browser:** response should be
  `Access denied: user 2vxsx-fae lacks permission 'extension.sync_call'`.
  This is the expected proof that the dynamic-imported bundle made a real
  inter-canister call to `realm_backend`.
- **Logged in via Internet Identity** with an admin or member profile:
  response should be the actual `get_data` payload.

## Re-running for new versions

Bumping `test_bench`'s version is now a one-liner:

```
# Edit realms-extensions/extensions/test_bench/manifest.json → bump version
bash scripts/deploy_runtime_extension_to_staging.sh \
    --realm-backend <dominion_realm_backend> \
    --network staging
# (the script reads --version from manifest.json by default)
```

No `realm_frontend` redeploy is needed — that's the whole point of
Layer 2.

## Re-running for Agora (realm2) and Syntropia (realm3)

Once the path is validated on Dominion, run the same script against the
other realms (after their `realm_backend` is also upgraded with the new
code via the corresponding `staging-realm{2,3}-backend.yml` descriptor):

```
bash scripts/deploy_runtime_extension_to_staging.sh \
    --realm-backend <agora_realm_backend>      --network staging
bash scripts/deploy_runtime_extension_to_staging.sh \
    --realm-backend <syntropia_realm_backend>  --network staging
```

Each call is idempotent — the bundle on `file_registry` is shared
across all three realms, only `install_extension_from_registry` is
realm-specific.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `realm_backend at <id> does not expose install_extension_from_registry` | Step 1 didn't run, or used `mode: reinstall` and lost state | Re-run Step 1 with `mode_override: upgrade` |
| `get_extension_frontend_info` returns `success:false, error:"No registry source recorded"` | Extension was installed before the `_source.json` code landed | Re-run the script (re-installs and writes `_source.json`) |
| Browser shows blank route or `Refused to load the script ... Content Security Policy` | `realm_frontend` CSP missing `https://*.icp0.io` in `script-src` | Confirm `static/.ic-assets.json5` was deployed in Step 3; rebuild + redeploy |
| HTTP 404 for `index.js` | `publish_namespace` not called, or version mismatch | Re-run script (publish is idempotent) |
| Loader works but `extension_sync_call` returns nothing | Loader still using anonymous actor | Confirm logged in via II; check `/+page.svelte` is using the authenticated `backend` store |
| New canister created on every run | `file_registry` canister id not recorded | Already present in `canister_ids.json` under `file_registry.staging` — check it wasn't stripped |
| `install_extension_from_registry` returns `AccessDenied: user <p> lacks permission 'extension.install'` | Your identity is not the one captured as `_controller_principal` at the realm_backend's last `@init`/`@post_upgrade`, and it's not in `trusted_principals` or a profile with `extension.install` | Re-run the install step manually as the identity that deployed the realm_backend (usually a canister-controller), e.g. `dfx identity use my_dev_identity_1 && dfx canister call <realm_backend> install_extension_from_registry '("…")' --network staging`. Step 4 (upload) can still run as the deploy identity. |
