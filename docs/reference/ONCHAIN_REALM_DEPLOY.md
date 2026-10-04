# On-chain `realm_installer.deploy_realm` (removed)

The end-to-end on-chain `deploy_realm` API (async timer-driven install of
WASM, frontend, extensions, and codices in one canister) has been **removed**
from `realm_installer`.

**Use instead:**

- **Queue + dfx path:** `enqueue_deployment` on `realm_installer` (called by
  `realm_registry_backend`), the off-chain deploy service installs WASM, calls
  `report_canister_ready` for on-chain verification, then pushes the Svelte
  `dist` and manifest branding files to the realm frontend asset canister, then
  the installer runs extensions/codex and registry registration.
- **No direct installer deploy endpoints:** queue deployment is now the
  only supported path. `install_realm_backend`, `deploy_frontend`, and
  `fetch_module_hash` were removed from `realm_installer`.

Historical issue reference: [GitHub #192](https://github.com/smart-social-contracts/realms-gos/issues/192).
