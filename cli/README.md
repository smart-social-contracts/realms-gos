# Realms CLI

Command-line tool for Realms GOS: build extensions and codices, run a realm
locally, and deploy realms to the Internet Computer.

## Installation

The package is **`realms-gos`** (the `realms` package on PyPI is unrelated).
Install it with [pipx](https://pipx.pypa.io/), which gives the CLI its own
environment; on Ubuntu 24.04 and Debian 12, plain `pip install` into the
system Python is refused ("externally-managed-environment").

```bash
sudo apt install pipx            # or see pipx.pypa.io for other systems
pipx ensurepath                  # then open a new terminal
pipx install --pip-args=--pre realms-gos
realms --version
```

`--pre` is needed until 0.7.0 is final: the `realms new extension|codex` and
`realms local` commands below ship in the 0.7.0 release candidates.
No clone of the repository is needed.

Requirements: Python 3.8+ and **Node.js 20+ with npm** (extension frontends
are built with npm; `realms local` installs icp-cli through npm on first use).

## Quick Start: an Extension and a Codex on a Local Realm

```bash
realms local start                                   # local network + realm from the matching release
realms new extension my-hello-world                  # scaffold, npm install, build
realms new codex my-hello-codex --extension my_hello_world
realms local publish my-hello-world my-hello-codex   # into the local file registry
realms local setup --codex my_hello_codex            # install codex + extensions, launch
realms local call my_hello_world greet '{}'          # "Hello, World!"
realms local status                                  # realm URL and canister IDs
```

Open the realm URL, sign in with the local Internet Identity, join the realm,
and open **My Hello World**: its **Say hello** button shows `Hello, World!`.

After a change: bump `version` in the extension's `manifest.json`, run
`npm run build` in it, then `realms local publish my-hello-world --install`.
`realms local add-admin <principal>` makes a browser user an admin;
`realms local stop` stops the network.

## Deploying Realms (dfx)

The `realms realm …` and `realms registry …` commands below deploy with
[dfx](https://internetcomputer.org/docs/current/developer-docs/setup/install/)
and need it installed (`dfx --version`).

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
`casals.json` and `docs/deployment/README.md`; realm manifests live under `examples/demo/`.

### Configuration Schema

- **type**: Always `realm` for a single-realm manifest
- **name**: Display name for the realm
- **options.random**: Optional knobs for generated demo data

## Development Setup

For contributing to the CLI tool:

```bash
git clone https://github.com/smart-social-contracts/realms-gos
cd realms-gos/cli
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
`casals up` from the Casals repo — see `docs/deployment/README.md`. The `realms` CLI
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

See `docs/extensions/README.md` for how extensions are installed and what they may do.

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
