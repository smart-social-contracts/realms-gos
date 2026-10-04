# Realms CLI

A powerful command-line tool for managing Realms project lifecycle on the Internet Computer.

## Features

- **🏗️ Project Scaffolding**: Initialize complete Realms projects with proper structure
- **🚀 Automated Deployment**: Deploy backend + frontend + extensions with single command
- **📦 Extension Management**: Phased rollouts (q1-q4) with dependency management
- **⚙️ Post-Deployment Actions**: Automated setup and data population
- **🔧 Configuration Validation**: Schema validation with helpful error messages
- **📊 Project Status**: Check project health and deployment status

## Prerequisites

Before installing realms-gos, ensure you have the following dependencies:

### 1. DFX (DFINITY Canister SDK)
```bash
sh -ci "$(curl -fsSL https://internetcomputer.org/install.sh)"
```
- **Required for**: Canister deployment, local replica management
- **Used by**: `realms realm deploy` command for backend/frontend deployment
- **Verify installation**: `dfx --version`

### 2. Node.js (v16 or later)
```bash
# Option 1: Download from https://nodejs.org
# Option 2: Via package manager (Ubuntu/Debian)
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# Option 3: Via nvm (recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
nvm install 18
nvm use 18
```
- **Required for**: Frontend building, npm package management
- **Used by**: Frontend canister builds during deployment
- **Verify installation**: `node --version && npm --version`

## Installation

```bash
pip install realms-gos
```

### From Source

```bash
git clone https://github.com/smart-social-contracts/realms-gos
cd realms/cli
pip install -e .
```

## Prerequisites

- Python 3.8+
- [dfx](https://internetcomputer.org/docs/current/developer-docs/setup/install/) (Internet Computer SDK)
- Node.js 16+
- Git

## Quick Start

### 1. Create a New Realm

```bash
realms realm create --realm-name "My Government Realm" --random --deploy
```

This creates a complete project structure (under `.realms/realm_*/`) with:
- Backend canister (Python/Basilisk)
- Frontend canister (SvelteKit)
- Extension system
- Configuration files
- Deployment scripts

Your realm will be available at `http://<canister_id>.localhost:8000`

## Commands

### `realms realm create`

Create (and optionally deploy) a new realm.

```bash
realms realm create [OPTIONS]

Common options:
  --realm-name TEXT         Realm name
  --random                  Populate with random demo data
  --citizens INTEGER        Number of citizens to generate
  --organizations INTEGER   Number of organizations to generate
  --transactions INTEGER    Number of transactions to generate
  --seed INTEGER            Deterministic seed for random data
  -n, --network TEXT        Target network [local|staging|ic]
  --deploy                  Deploy after creation
  -m, --mode TEXT           Deploy mode: 'auto', 'upgrade' or 'reinstall'
```

### `realms realm deploy`

Deploy a previously-created realm folder.

```bash
realms realm deploy [OPTIONS]

Options:
  --folder TEXT             Path to generated realm folder
  -n, --network TEXT        Target network (default: local)
  --clean                   Clean deployment (restart dfx)
  --identity TEXT           Identity PEM file or dfx identity name
  -m, --mode TEXT           Deploy mode: 'auto', 'upgrade' or 'reinstall'
  --plain-logs              Show full verbose output instead of progress UI
```

**Examples:**
```bash
# Basic deployment (auto-detects single realm folder under .realms/)
realms realm deploy

# Deploy to IC mainnet with a specific identity
realms realm deploy --network ic --identity ~/.config/dfx/identity/production/identity.pem
```

### `realms registry create`

Create and optionally deploy a registry instance.

```bash
realms registry create [OPTIONS]

Options:
  --name TEXT              Registry name (optional)
  -o, --output-dir TEXT    Base output directory (default: .realms)
  -n, --network TEXT       Network to deploy to [local|staging|ic] (default: local)
  --deploy                 Deploy the registry after creation
  --identity TEXT          Identity PEM file or dfx identity name
  -m, --mode TEXT          Deploy mode: 'upgrade' or 'reinstall' (default: upgrade)
```

**Examples:**
```bash
# Create and deploy to local
realms registry create --deploy

# Create and deploy to staging (uses existing canister IDs from canister_ids.json)
realms registry create --deploy --network staging

# Create and deploy with reinstall (wipes stable memory)
realms registry create --deploy --network staging --mode reinstall
```

**Note:** For non-local networks (staging, ic), the CLI automatically copies registry canister IDs from the root `canister_ids.json` file, allowing deployment to existing canisters.

### `realms status`

Show the current status of your Realms project.

```bash
realms status
```

## Configuration

Each generated realm folder contains a `manifest.json` describing the realm:

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

For the fleet itself (marketplace, file registry, token, NFT, demo realm) see
`casals.json` and `docs/OPERATIONS.md`; realm manifests live under `examples/demo/`.

### Configuration Schema

- **type**: Always `realm` for a single-realm manifest
- **name**: Display name for the realm
- **options.random**: Optional knobs for generated demo data

## Installation

### Recommended: Using pipx (Isolated Environment)

The easiest way to install the Realms CLI is using `pipx`, which automatically manages isolated Python environments:

```bash
# Install pipx if you don't have it
pip install pipx

# Install realms-gos in an isolated environment
pipx install realms-gos

# Use the CLI tool (no venv activation needed)
realms --help
realms realm create --citizens 50
```

### Alternative: Using pip with venv

If you prefer manual environment management:

```bash
python -m venv realms-env
source realms-env/bin/activate  # On Windows: realms-env\Scripts\activate
pip install realms-gos
```

### Development Setup

For contributing to the CLI tool:

```bash
git clone https://github.com/smart-social-contracts/realms-gos
cd realms/cli
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black realms/
isort realms/
```

## Examples

### Government Services Platform

```bash
realms realm create \
  --realm-name "Digital Government Services" \
  --random --citizens 50 --organizations 5 --deploy
```

### Multi-Realm (Mundus) Deployment

```bash
# Create a multi-realm ecosystem with a shared registry
realms mundus create --deploy

# Or use a custom manifest
realms mundus create --manifest examples/demo/manifest.json --deploy
```

## Troubleshooting

### Common Issues

**dfx not found**
```bash
# Install dfx
sh -ci "$(curl -fsSL https://internetcomputer.org/install.sh)"
```

**Port already in use**
```bash
# Kill existing dfx processes
dfx stop
pkill dfx
```

**Extension deployment fails**
```bash
# Check extension directory exists
ls extensions/
# Reinstall extensions
./scripts/install_extensions.sh
```

### Getting Help

- Show available commands: `realms --help`
- Show subcommand help: `realms realm --help`, `realms mundus --help`, etc.
- Check project status: `realms status`

### Provisioning is not a CLI command

The Realms product orchestra (marketplace, fleet file registry, token, NFT,
demo realm) is declared in the repo-root `casals.json` and deployed with
`casals up` from the Casals repo — see `docs/OPERATIONS.md`. The `realms` CLI
operates realms that exist; it does not mint or destroy canisters.

## GUI alternative: the Package Manager extension

Most extension and codex management actions exposed by this CLI
(`realms extension registry-install`, `realms extension runtime-install`,
`realms extension runtime-uninstall`, `realms codex install`, ...) are
also available to realm administrators directly from the realm's
frontend, via the `package_manager` extension.

The extension provides three tabs (Installed / Browse / Upload) wired
to the same `install_extension*` / `install_codex*` /
`uninstall_extension` / `uninstall_codex` backend endpoints used by this
CLI. Once a realm has the package_manager extension installed, an admin
no longer needs CLI access to the host machine to add or remove
packages — they can do everything through the browser.

See `docs/reference/EXTENSION_ARCHITECTURE.md → Package Manager Extension`
for the full feature list, permissions model and caveats.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Support

- 🐛 [Issue Tracker](https://github.com/smart-social-contracts/realms-gos/issues)
