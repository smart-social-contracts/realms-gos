# Realms

Realms GOS (Governance Operating System) is a platform for building and deploying governance systems on the [Internet Computer](https://internetcomputer.org).

The **registry / wizard / installer** stack (GaaS platform layer) lives in [gos-as-a-service](https://github.com/smart-social-contracts/gos-as-a-service). Realms consumes prebuilt artifacts from pinned GOS releases; it does not build `realm_registry_*` or `realm_installer` from source.

**Canister provisioning** is handled by [Casals](https://github.com/smart-social-contracts/casals), the platform's on-chain lifecycle provisioner (external repo, serving the gos-as-a-service platform). Realms is the fleet **operator**: it runs Casals conductor instances per network and keeps fleet config in `casals-config/`.

**Related repos:** basilisk · ic-basilisk-toolkit · ic-python-db · ic-python-logging · **realms** (this repo) · [**gos-as-a-service**](https://github.com/smart-social-contracts/gos-as-a-service) (GaaS platform — registry, wizard, installer) · extensions · codices · [**casals**](https://github.com/smart-social-contracts/casals) (platform provisioner — Realms operates conductors)

## Table of Contents

- [Quick Start](#quick-start)
- [Extension Development](#extension-development)
- [Running Python Code in Realms](#running-python-code-in-realms)
- [Creating a New Realm](#creating-a-new-realm)
- [Layered Deployment Architecture](#layered-deployment-architecture)
- [Multi-Realm Deployment (Mundus)](#multi-realm-deployment-mundus)
- [Sandbox](#sandbox)

---

## Quick Start

```bash
# Install Realms CLI
pip install realms-gos

# or alternatively
pip install -e cli/

# Create a single realm with demo data
realms realm create --random --citizens 50 --deploy

# Or create a multi-realm ecosystem
realms mundus create --deploy

# Your realm is now running!
# Frontend: http://<canister_id>.localhost:8000
```

---

## Extension Development

Realms supports a powerful extension system that allows developers to add custom functionality to realm backends. Extensions are self-contained packages that can be installed into any realm.

### Extension Architecture

Extensions run directly inside the `realm_backend` canister, providing:
- **No inter-canister overhead** - Direct function calls
- **Atomic operations** - Share the same stable memory with realm entities
- **Simple installation** - Copy files, no separate deployment
- **CLI-first workflow** - Easy to develop, test, and distribute

### Creating an Extension

#### 1. Extension Structure

```
my-extension/
├── backend/
│   ├── __init__.py
│   └── entry.py              # Required: Extension entry points
├── frontend/
│   ├── lib/extensions/my_extension/
│   ├── routes/(sidebar)/extensions/my_extension/
│   └── i18n/locales/extensions/my_extension/
├── manifest.json             # Required: Extension metadata
├── requirements.txt          # Python dependencies
├── README.md
├── CHANGELOG.md
├── LICENSE
└── tests/
    └── test_my_extension.py
```

#### 2. Create manifest.json

```json
{
  "name": "my_extension",
  "version": "1.0.0",
  "description": "My awesome extension",
  "author": "Your Name",
  "repository": "https://github.com/username/my-extension",
  "license": "MIT",
  "realms_compatibility": {
    "min_version": "0.1.0",
    "max_version": "0.2.x"
  },
  "entry_points": ["my_function", "another_function"],
  "profiles": ["admin"],
  "categories": ["governance"],
  "icon": "settings",
  "show_in_sidebar": true,
  "python_dependencies": [
    "basilisk>=0.10.0"
  ]
}
```

#### 3. Create backend/entry.py

```python
"""My Extension Entry Point"""
import json
from basilisk import Async
from ic_python_logging import get_logger

logger = get_logger("extensions.my_extension")

def my_function(args: str) -> str:
    """
    Extension function callable from realm_backend.
    
    Args:
        args: JSON string with parameters
    
    Returns:
        JSON string with result
    """
    logger.info(f"my_function called with: {args}")
    
    try:
        params = json.loads(args) if isinstance(args, str) else args
        
        # Your logic here
        result = {"message": "Hello from my extension!"}
        
        return json.dumps({"success": True, "data": result})
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})


def another_function(args: str) -> Async[str]:
    """Async function example"""
    logger.info("another_function called")
    
    try:
        # Async operations
        result = yield some_async_call()
        
        return json.dumps({"success": True, "data": result})
        
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return json.dumps({"success": False, "error": str(e)})
```

### Installing Extensions

#### For Extension Developers (Local Testing)

```bash
# Clone realms repository
git clone --recurse-submodules https://github.com/smart-social-contracts/realms-gos.git
cd realms

# Install realms CLI in development mode
pip install -e cli/

# Install extensions from source
./scripts/install_extensions.sh

# Deploy for testing
dfx start --clean --background
realms realm create --random --deploy

# Test your extension via frontend or CLI
dfx canister call realm_backend extension_call '("my_extension", "my_function", "{\"param\": \"value\"}")'
```

#### For Realm Operators (Production)

```bash
# Install realms CLI
pip install -e cli/  # (pip install realms-gos when published)

# Create a realm
realms realm create --realm-name "My Realm" --deploy

# Extensions are installed automatically during realm creation
# To update extensions, re-run:
./scripts/install_extensions.sh
dfx deploy realm_frontend

# Deploy
realms realm deploy --network ic --identity prod
```

### Managing Extensions

```bash
# List installed extensions
realms extension list

# Install extensions from source
./scripts/install_extensions.sh

# Uninstall an extension
realms extension uninstall my_extension

# Package an extension for distribution
realms extension package --extension-id my_extension
```

### Testing Extensions

Create tests in `tests/` directory:

```python
# tests/test_my_extension.py
import pytest
import json

def test_my_function():
    """Test extension function"""
    # This requires realms_test_utils (future)
    # from realms_test_utils import RealmTestClient
    
    # client = RealmTestClient()
    # client.install_extension("my_extension")
    # result = client.call_extension("my_extension", "my_function", {})
    # assert result["success"] == True
    pass
```

Run tests:
```bash
pytest tests/ -v
```

### Packaging for Distribution

```bash
# Package extension into distributable zip
realms extension package --extension-id my_extension
# Creates: my_extension-1.0.0.zip in current directory
```

### Publishing Extensions

Extensions are developed in the `extensions/` directory and installed via the install script. For external distribution:

```bash
# Package your extension
realms extension package --extension-id my_extension

# Distribute via GitHub releases or other channels
# Users install by copying to their extensions/ directory
# and running ./scripts/install_extensions.sh
```

### Extension Examples

Available extensions in the repository:

- **admin_dashboard** - Administrative dashboard for realm management
- **citizen_dashboard** - Member-facing dashboard  
- **vault** - Treasury and ICRC-1 token management
- **public_dashboard** - Public-facing statistics and information
- **land_registry** - Property and land management
- **llm_chat** - AI-powered chat assistant
- **notifications** - Notification system
- **passport_verification** - Identity verification
- **justice_litigation** - Legal system management
- **market_place** - Extension marketplace

See `extensions/README.md` for complete documentation

### Extension Best Practices

1. **Use Logging** - Always log important operations
2. **Error Handling** - Catch all exceptions and return JSON errors
3. **JSON API** - All extension functions should accept/return JSON strings
4. **Documentation** - Write comprehensive README with usage examples
5. **Versioning** - Follow semantic versioning
6. **Testing** - Write tests for all functionality
7. **Dependencies** - List all Python dependencies in requirements.txt
8. **Compatibility** - Specify realms version compatibility in manifest.json

### Calling Extensions from Realm Code

```python
# From realm backend code
from core.extensions import extension_async_call

# Call extension function
result = yield extension_async_call("my_extension", "my_function", json.dumps({"key": "value"}))
```

### Extension API Reference

Extensions can access:
- **ggg entities** - User, Member, Treasury, etc.
- **basilisk** - IC SDK functions
- **ic_python_db** - Database entities
- **ic_python_logging** - Logging utilities
- **realm_backend core** - Core realm functionality

---

## Running Python Code in Realms

Python code can be executed inside the realm canister using `basilisk shell` (interactive) or `basilisk-toolkit exec` (file-based). These commands use the unified `execute_code()` function, which automatically detects and handles both synchronous and asynchronous code via the TaskManager.

### Basic Usage

```bash
# Sync code - executes immediately, returns result
basilisk-toolkit exec -f examples/sync_example.py

# Async code - schedules task, returns task ID
basilisk-toolkit exec -f examples/async_example.py

# Interactive shell
basilisk shell
```

### Response Format

**Sync execution:**
```json
{
  "type": "sync",
  "status": "completed",
  "task_id": "abc123",
  "result": {"count": 5, "names": ["Alice", "Bob"]}
}
```

**Async execution:**
```json
{
  "type": "async",
  "task_id": "def456",
  "status": "pending",
  "message": "Async task scheduled. Check logs or poll status."
}
```

### Code Requirements

**Sync code** - Just write normal Python:
```python
from ggg import Member
members = Member.instances()
result = len(members)  # Optional: set result variable
```

**Async code** - Define an `async_task` function with `yield`:
```python
from basilisk import ic

def async_task():
    result = yield some_async_call()
    ic.print(result)
    return result
```

### Implementation Details

- **TaskManager Integration**: Both sync and async code run through the Call → TaskStep → Task → TaskSchedule pipeline
- **Auto-detection**: The presence of `yield` or `async_task` triggers async mode
- **Logging**: Execution logs appear in `dfx.log` or `dfx2.log`, not in CLI output
- **Result Storage**: Results are stored in the `Call._result` attribute for retrieval
- **Status Polling**: Use `dfx canister call realm_backend get_task_status '("task_id")'` to check async task completion

### Examples

See `examples/sync_example.py` and `examples/async_example.py` for working examples.

---

## Scheduled Tasks

Task scheduling and management is handled via `basilisk-toolkit`. See the basilisk-toolkit documentation for details on creating, monitoring, and managing scheduled tasks.

📖 **Full documentation**: [Scheduled Tasks Reference](./docs/reference/SCHEDULED_TASKS.md)

---

## Creating a New Realm

### Quick Start

```bash
# Install Realms CLI
pip install -e cli/

# Create a new realm with demo data
realms realm create --random --citizens 100 --organizations 10 --deploy

# Or create with custom configuration
realms realm create \
  --realm-name "My Government Realm" \
  --citizens 50 \
  --organizations 5 \
  --transactions 200 \
  --seed 12345 \
  --deploy
```

### Import Existing Data

```bash
# Create realm structure
realms realm create --realm-name "My Realm"

# Import data
cd generated_realm
realms db import ../my_realm_data.json

# Deploy
realms realm deploy
```

### Administration

Manage realm data:
- **Via CLI**: `realms db import <data.json>` or `realms db import <codex.py> --type codex`
- **Via UI**: Use the Admin Dashboard extension (admin-only access)
- **Via Extensions**: Extensions can provide custom data management interfaces

---

## Layered Deployment Architecture

Realms supports two deployment models that produce **the same end-user experience**:

- **Bundled (default, used by `realms realm create --deploy` and `realms mundus create`):** The `realm_backend` WASM ships with every extension and codex baked in. One `dfx deploy` and you're done. Best for local dev and quick demos.
- **Layered (used in production for long-lived realms like Dominion):** The base WASM, every extension, every codex, every i18n bundle, and every sidebar manifest are stored in a separate `file_registry` canister and pulled in at install time. Best for upgrading large fleets of realms without rebuilding/redeploying each one.

Both modes coexist. The runtime loader inside `realm_backend` falls back to bundled artifacts when nothing is registered in stable storage, so existing realms keep working unchanged.

### The three layers

```
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 3 — Realm data                                                 │
│   Members, organizations, treasuries, votes, …                       │
│   Lives in realm_backend stable memory. Created by `upload_data`.    │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 2 — Extensions, codices, i18n, sidebar manifests               │
│   Per-extension ESM frontend bundle (self-contained Tailwind +       │
│   Flowbite + svelte-i18n), backend Python modules, manifest.json,    │
│   per-locale i18n JSON, codex .py files.                             │
│   Published to file_registry, installed into realm_backend stable    │
│   storage at registry-install time, dynamically mounted by           │
│   realm_frontend at runtime.                                         │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Base realm_backend WASM                                    │
│   No bundled extensions, no bundled codices. Just the runtime        │
│   loader, ggg entities, TaskManager, and the file_registry client.   │
│   Published to file_registry (chunked), installed via realm_installer│
│   calling ic.management_canister.install_chunked_code.               │
└──────────────────────────────────────────────────────────────────────┘
```

### Canisters involved

| Canister | Role |
|---|---|
| `realm_backend` | The realm itself. In layered mode this is just the base WASM; everything else is loaded from registry into stable storage. |
| `file_registry` | Versioned artifact store. HTTP gateway for read, inter-canister calls for write. Holds WASM, ESM bundles, Python modules, manifests, i18n JSON, codices. |
| `file_registry_frontend` | Static asset canister. Admin UI for browsing, uploading (with auto-chunking), and deleting registry contents. Authenticates via Internet Identity for write calls. |
| `realm_installer` | Small Basilisk bootstrapper canister (released from **gos-as-a-service**). Reads chunked WASM out of `file_registry` and calls `ic.management_canister.install_chunked_code` on the target realm. Must be a controller of the target realm. |
| `realm_frontend` | SvelteKit app. In layered mode it dynamically `import()`s extension bundles from `file_registry` over the HTTP gateway, fetches per-extension i18n on demand, and renders the sidebar from `get_sidebar_manifests`. |
| `marketplace_backend` + `marketplace_frontend` | Standalone marketplace canisters (Basilisk + SvelteKit) at `src/marketplace_*`. Hold listings (extensions and codices), likes, ranking, purchases, audit/verification status, and developer licenses. The actual artifact files live in a connected `file_registry` canister; the marketplace stores only metadata + a pointer. Deploy via `realms marketplace deploy`. See [marketplace/MARKETPLACE.md](docs/reference/MARKETPLACE.md). |

### File-registry namespaces

```
wasm/realm-base-<version>.wasm.gz
ext/<extension_id>/<version>/manifest.json
ext/<extension_id>/<version>/backend/*.py
ext/<extension_id>/<version>/frontend/dist/index.js     # ESM bundle, exports mount(target, props)
ext/<extension_id>/<version>/frontend/i18n/<locale>.json
codex/<codex_id>/<version>/manifest.json
codex/<codex_id>/<version>/*.py
```

Every artifact is content-addressed via SHA-256 and chunked when it exceeds the IC ingress limit (~2 MB after base64).

### Publishing artifacts (writer side)

```bash
# Publish an extension to file_registry (manifest + backend Python
# + ESM frontend bundle + i18n JSON)
realms extension publish \
    --extension-id voting \
    --network ic \
    --registry <FILE_REGISTRY_CANISTER_ID>

# Publish a codex to file_registry
realms codex publish \
    --codex-id basic_governance \
    --network ic \
    --registry <FILE_REGISTRY_CANISTER_ID>

# Build and publish the base WASM (Layer 1)
python scripts/build_base_wasm.py            # produces a stripped .wasm.gz
realms wasm publish --wasm <path> --version <semver> --network ic
```

For a full repository-wide publish (every extension + every codex + base WASM), use the orchestrator:

```bash
python scripts/publish_layered.py \
    --network ic \
    --registry <FILE_REGISTRY_CANISTER_ID> \
    --base-wasm-version 0.5.0
```

Helper scripts that the publish flow relies on:

- `scripts/scaffold_runtime_bundles.py` — generates the per-extension `frontend-rt/` directory (Vite + Tailwind + Flowbite + `svelte-i18n`, all self-contained) for any extension that doesn't have one yet.
- `scripts/build_runtime_bundles.py` — runs `npm install` + `vite build` over every `frontend-rt/`.
- `scripts/build_base_wasm.py` — strips bundled extensions/codices, rebuilds and gzips the WASM.
- `scripts/add_sidebar_labels.py` — idempotent injection of multilingual `sidebar_label` entries into extension manifests.

### Installing artifacts on a realm (reader side)

```bash
# Install (or upgrade) the realm_backend WASM by pulling it out of
# file_registry in chunks via realm_installer.
realms wasm install \
    --canister <REALM_BACKEND_CANISTER_ID> \
    --version 0.5.0 \
    --installer <REALM_INSTALLER_CANISTER_ID> \
    --registry <FILE_REGISTRY_CANISTER_ID> \
    --mode upgrade \
    --network ic

# Pull an extension out of file_registry into the realm's stable storage
realms extension registry-install \
    --extension-id voting \
    --version 1.0.3 \
    --canister <REALM_BACKEND_CANISTER_ID> \
    --registry <FILE_REGISTRY_CANISTER_ID> \
    --network ic

# Same for a codex
realms codex registry-install \
    --codex-id basic_governance \
    --version 1.0.0 \
    --canister <REALM_BACKEND_CANISTER_ID> \
    --registry <FILE_REGISTRY_CANISTER_ID> \
    --network ic
```

For an end-to-end layered deploy of a single realm, write a deployment descriptor:

```yaml
# deployments/staging-dominion-layered.yml
name: dominion-staging
network: staging
install_strategy: layered
realm_backend: <REALM_BACKEND_CANISTER_ID>
realm_installer: <REALM_INSTALLER_CANISTER_ID>
file_registry: <FILE_REGISTRY_CANISTER_ID>
base_wasm_version: 0.5.0
extensions: [voting, vault, admin_dashboard, …]
codices:    [basic_governance, …]
```

Then install it: `realms wasm install` for the base, `realms extension registry-install`
for each extension, `realms codex registry-install` for each codex (the
`package_manager` extension does the same from the realm UI).

### How `realm_frontend` consumes Layer 2

At runtime, on every page load:

1. Frontend asks the realm: `realm_backend.get_sidebar_manifests()` → returns the merged set of bundled + runtime extension manifests with multilingual labels.
2. For each navigated extension, frontend resolves `realm_backend.get_extension_frontend_info(<id>)` → returns the registry URL of the ESM bundle.
3. Frontend dynamically imports the bundle: `await import(/* @vite-ignore */ bundleUrl)` and calls its exported `mount(target, props)`.
4. The bundle, being self-contained, fetches its own i18n JSON from `file_registry` via `loadExtensionTranslationsFromRegistry`.

The plumbing lives in `src/realm_frontend/src/lib/extension-loader.ts`, `src/realm_frontend/src/lib/i18n/index.ts`, and `src/realm_frontend/src/routes/extensions/[id]/+page.svelte`.

### `file_registry_frontend` admin UI

A standalone asset canister ships a vanilla-JS dashboard for the registry: namespace browser, file list, drag-and-drop upload with auto-chunking above 400 KB, delete (controller-only), and Internet Identity login. Source and releases live in [gos-as-a-service](https://github.com/smart-social-contracts/gos-as-a-service) (`file_registry_frontend.tar.gz`); realms deploys the fetched dist from `.external-assets/file_registry_frontend/dist`.

### CI / GitHub Actions

Two operator workflows under `.github/workflows/` automate the layered flow. All are manual (`workflow_dispatch`).

| Workflow | Purpose |
|---|---|
| `Publish Base WASM` | Builds Layer 1 and uploads it to `file_registry` at `wasm/realm-base-<version>.wasm.gz` (chunked). |
| `Runtime Extension Deploy` | Tight inner loop: publish + registry-install **one** extension on a chosen realm. Useful while iterating on a single extension. |

Required repository configuration:

```
vars.FILE_REGISTRY_CANISTER_ID        # file_registry canister id (per network)
vars.REALM_INSTALLER_CANISTER_ID      # realm_installer canister id (per network)
                                       # — must already be a controller of the
                                       #   target realm canister
vars.DOMINION_REALM_BACKEND_CANISTER  # target realm canister id (Layered Deploy
                                       #   Dominion only)
secrets.DFX_IDENTITY_PEM              # PEM-encoded identity used to publish
                                       #   artifacts and to call realm_installer
                                       #   / realm_backend
```

### When to use which mode

| Situation | Mode |
|---|---|
| Local development (`dfx start --clean`) | Bundled (`realms realm create --deploy`) |
| Multi-realm local demo (`realms mundus create`) | Bundled |
| Single-realm staging or prod deploy you'll redeploy frequently | Either; bundled is simpler |
| Long-lived realm (e.g. Dominion) where you want to roll out new extensions/codices without rebuilding the WASM | Layered |
| Fleet of realms sharing the same WASM and extension set | Layered (publish once, install many) |

Both modes pass the same Playwright snapshot tests (`src/realm_frontend/tests/e2e/specs/layered-parity.spec.ts`) — visual parity with the bundled build is part of the contract.

### Reference

- Issue tracker: [#168 — Layered realm deployment](https://github.com/smart-social-contracts/realms-gos/issues/168)
- Detailed runtime-extension staging walkthrough: [`docs/reference/RUNTIME_EXTENSION_STAGING_DEPLOY.md`](./docs/reference/RUNTIME_EXTENSION_STAGING_DEPLOY.md)

---

## Multi-Realm Deployment (Mundus)

Mundus allows you to deploy multiple realm instances with a shared registry on a single dfx instance.

### Quick Start

```bash
# Create mundus with 3 realms + registry
realms mundus create --deploy

# Access realms at different URLs
# Realm 1: http://<realm1_frontend_id>.localhost:8000
# Realm 2: http://<realm2_frontend_id>.localhost:8000
# Realm 3: http://<realm3_frontend_id>.localhost:8000
# Registry: http://<registry_frontend_id>.localhost:8000
```

### Features

- **Single dfx Instance**: All realms share one dfx process
- **Unique Canister Names**: Each realm has unique canister IDs
- **Shared Registry**: Central registry tracks all realms
- **Independent Data**: Each realm has its own entities and state
- **Customizable**: Configure each realm independently via manifests

### Use Cases

- **Development**: Test multi-realm interactions locally
- **Demos**: Showcase multiple realm configurations
- **Testing**: Integration testing across realms
- **Staging**: Multi-tenant staging environment

### Customization

Edit realm manifests in `examples/demo/realm{N}/manifest.json`:

```json
{
  "type": "realm",
  "name": "My Custom Realm",
  "options": {
    "random": {
      "members": 100,
      "organizations": 10,
      "transactions": 200,
      "disputes": 15,
      "seed": 42
    }
  }
}
```

Then create with your custom manifest:
```bash
realms mundus create --manifest examples/demo/manifest.json --deploy
```

See [Operations](./docs/OPERATIONS.md) and `AGENTS.md` for deploy workflows.

---

## Sandbox

[Sandbox Realm](https://demo.realmsgos.org).
Decent amount of members (10k) and other objects simulating a production-like environment.
Runs scheduled tasks for payments, tax claculations etc.
Voting of new codex and deployment of it.
Registry in the [Realm Registry](https://realmsgos.org).