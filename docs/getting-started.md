# Getting started

Run a realm on your machine, install a codex and an extension, and call operations. This takes
about five minutes once the tools are installed.

## Prerequisites

- Python 3.8+ and [pipx](https://pipx.pypa.io/)
- Node.js 20+ with npm (it builds extension UIs, and `realms local` installs
  [icp-cli](https://github.com/dfinity/icp-cli) through npm the first time)

Install the CLI. The PyPI package is `realms-gos`; the `realms` package is unrelated.

```bash
pipx install --pip-args=--pre realms-gos
realms --version
```

To work on the CLI itself, use `pip install -e cli` from a clone instead.

## 1. Start a local realm

```bash
realms local start
```

This starts a local Internet Computer network (icp-cli with PocketIC) and deploys three canisters
from the GitHub release that matches your CLI:

| Canister | Role |
|---|---|
| `realm_backend` | The realm: every operation, all data |
| `realm_frontend` | The web UI (an asset canister) |
| `file_registry` | Where extensions and codices are published before a realm installs them |

It also publishes the core extensions (dashboards, settings, voting, …) to the local registry. The
realm starts in `setup` status, with you (the local identity) as founder. State lives in
`~/.realms/local`.

Useful flags: `--version <tag>` picks another release, `--fresh` wipes the old realm first, and
`--port` changes the gateway port (default 8000).

> `realms local` runs **released** canisters. It needs a release with the current backend API
> (`read` and `run`, described in [operations](architecture/operations.md)); releases up to
> `v0.8.0-rc2` predate it. To try backend changes from your checkout, run the backend tests
> ([testing](development/testing.md)).

## 2. Publish and set up

Publish packages (directories with a `manifest.json`) to the local registry, then run founder
setup with a codex. The [extension guide](extensions/build-test-publish.md) and the
[codex guide](codices/build-test-publish.md) each build a minimal package you can use here.

```bash
realms local publish ./hello ./my_codex            # extensions first, then codices
realms local setup --codex my_codex
```

A codex lists the extensions it needs in `dependencies`; publish those too. `start` already
published the core extensions.

`setup` installs the codex's extensions and the core extensions, runs `codex_install`, sets the
treasury token and launches the realm (status `alpha`). The codex's `init` hook then seeds
departments, positions and courts.

## 3. Call operations

Everything a realm does is an [operation](architecture/operations.md), called by name with JSON
arguments.

```bash
realms local call me                                  # who am I here
realms local call ops_list                            # every operation, its permission and flags
realms local call proposal_list '{"status": "voting"}'
realms local call hello greet '{"name": "Ana"}'       # an extension function (extension_call)
```

The CLI picks `read` (query) or `run` (update) for you, from `ops_list`.

## 4. Open the UI

```bash
realms local status          # prints the realm URL and canister ids
```

Open the URL and sign in with the local Internet Identity. To make your browser login an admin,
copy its principal and run:

```bash
realms local add-admin <principal>
```

## 5. Iterate

After changing an extension, bump `version` in its `manifest.json`, rebuild its UI if it has one,
and publish again with `--install`:

```bash
realms local publish ./hello --install
```

Stop the network with `realms local stop` (its state is discarded).

## Next

- [Architecture](architecture/README.md): what you just deployed
- [Write an extension](extensions/build-test-publish.md)
- [Write a codex](codices/build-test-publish.md)
