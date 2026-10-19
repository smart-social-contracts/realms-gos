# Getting Started

Welcome to Realms GOS! This guide will help you get up and running quickly.

## What You'll Need

- **Python 3.8+** - For the Realms CLI (`pipx` recommended)
- **Node.js 20+** - Extension frontends are built with npm; `realms local` also installs icp-cli through npm on first use

## Installation

### 1. Install the Realms CLI

```bash
pipx install realms-gos
```

Verify the installation:

```bash
realms --version
```

## Quick Start: a Local Realm with Your Own Extension and Codex

```bash
realms local start                                  # local network + realm from the matching release
realms new extension my-hello-world                 # scaffold + npm install + build
realms new codex my-hello-codex --extension my_hello_world
realms local publish my-hello-world my-hello-codex  # into the local file registry
realms local setup --codex my_hello_codex           # install codex + extensions, launch the realm
realms local call my_hello_world greet '{}'         # → "Hello, World!"
realms local status                                 # realm URL and canister IDs
```

Open the realm URL from `realms local status`, sign in with the local
Internet Identity, and go to `/extensions/my_hello_world`. The **Say hello**
button calls the extension backend's `greet` and shows `Hello, World!`.

After editing the extension, run `npm run build` in its directory and then
`realms local publish my-hello-world --install`. Bump `version` in
`manifest.json` first so the realm picks up the new build.
`realms local stop` stops the network. `realms local start --fresh` deploys a new realm.

The same journey runs end to end in a fresh Ubuntu + GNOME VM with
[`devenv/devenv.sh run`](https://github.com/smart-social-contracts/realms-gos/tree/main/devenv).

## Generated Demo Realm (dfx)

These steps need **dfx** ([install guide](https://internetcomputer.org/docs/current/developer-docs/setup/install/)).

### 2. Create Your First Realm

Create a new realm with demo data:

```bash
realms realm create --random --citizens 50 --organizations 10
```

This will:
- Generate a new realm with 50 citizens and 10 organizations
- Create all necessary configuration files
- Set up the project structure

### 3. Deploy Locally

Navigate to your realm directory and deploy:

```bash
cd generated_realm
./scripts/2-deploy-canisters.sh
```

### 4. Upload Data

Upload the demo data to your realm:

```bash
./scripts/3-upload-data.sh
```

### 5. Access Your Realm

Open your browser and navigate to:

```
http://<canister_id>.localhost:8000
```

The canister ID will be displayed in your terminal after deployment.

## Next Steps

Now that you have a realm running, you can:

- **[Explore the CLI](/reference/CLI_REFERENCE)** - Learn all available commands
- **[Understand Core Entities](/reference/CORE_ENTITIES)** - Learn about the data model
- **[Operations](/OPERATIONS)** — Casals publish-build → rollout (see `AGENTS.md`)
- **[Build Extensions](/reference/EXTENSION_ARCHITECTURE)** - Extend your realm's functionality
- **[Join the Registry](/reference/REALM_REGISTRATION_GUIDE)** - Register your realm in the Mundus

## Common Tasks

### Create a Proposal

```python
from ggg import Proposal, User

proposal = Proposal(
    proposal_id="tax_reform_2024",
    title="Reduce Tax Rate to 10%",
    description="Proposal to reduce realm tax rate",
    proposer=User["admin"],
    status="voting",
    required_threshold=0.51
)
```

See the [Governance Tutorial](/reference/GOVERNANCE_TUTORIAL) for more details.

### Install an Extension

```bash
realms extension list
realms extension install <extension_name>
```

### View Running Tasks

Task management is handled via `basilisk-toolkit`. See the basilisk-toolkit documentation for listing and monitoring tasks.

## Getting Help

- **[Troubleshooting Guide](/reference/TROUBLESHOOTING)** - Common issues and solutions
- **[GitHub Issues](https://github.com/smart-social-contracts/realms-gos/issues)** - Report bugs or request features
- **[Examples](https://github.com/smart-social-contracts/realms-gos/tree/main/examples)** - Code examples

## Architecture Overview

Realms consists of several key components:

- **Backend** - Python/Basilisk canisters on the Internet Computer
- **Frontend** - SvelteKit web application
- **CLI** - Command-line tools for development and deployment
- **Extensions** - Modular plugins for additional functionality

Learn more in the [Technical Introduction](/reference/TECHNICAL_INTRO).
