<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="src/realm_frontend/static/images/logo_horizontal_white.svg">
    <img alt="Realms" src="src/realm_frontend/static/images/logo_horizontal.svg" width="280">
  </picture>
</p>

# Realms GOS

Realms GOS (Governance Operating System) runs communities on the
[Internet Computer](https://internetcomputer.org). A **realm** is a canister that holds a
community's members, departments, votes, treasury, land, licences and courts. Its rules are code
(a **codex**), its features are **extensions**, and every action is checked by one
[Cedar](https://www.cedarpolicy.com) policy set.

## Try it

```bash
pipx install --pip-args=--pre realms-gos
realms local start        # a realm, a file registry and the web UI, on your machine
realms local status       # the URLs
```

[Getting started](docs/getting-started.md) continues from there: publish a codex and extensions,
launch the realm and call its operations.

## Documentation

All documentation is in [`docs/`](docs/README.md):

| | |
|---|---|
| [Architecture](docs/architecture/README.md) | Canisters, operations, Cedar, sandboxing, governance, federation, trust |
| [Design choices](docs/architecture/design-choices.md) | The criteria behind the design |
| [GGG](docs/ggg/README.md) | The standard: every entity, operation and rule |
| [Extensions](docs/extensions/README.md) | Build, test and publish an extension |
| [Codices](docs/codices/README.md) | Build, test and publish a codex |
| [Deployment](docs/deployment/README.md) | Releases and the product orchestra |
| [Contributing](docs/development/contributing.md) | Rules, layout and [testing](docs/development/testing.md) |

## Repository

| Path | What |
|---|---|
| [`src/realm_backend`](src/realm_backend) | The realm (Python on [Basilisk](https://github.com/smart-social-contracts/basilisk)) |
| [`src/realm_frontend`](src/realm_frontend) | The realm's web app (SvelteKit) |
| [`src/marketplace_*`](src) | The marketplace canisters |
| [`cli/`](cli) | The `realms` CLI (`realms-gos` on PyPI) |
| [`packages/`](packages) | npm packages for extension UIs |
| [`extensions/`](https://github.com/smart-social-contracts/realms-extensions), [`codices/`](https://github.com/smart-social-contracts/realms-codices) | First-party extensions and codices (submodules) |
| [`tests/ggg`](tests/ggg) | The backend's tests |

Realms that people create are deployed by
[gos-as-a-service](https://github.com/smart-social-contracts/gos-as-a-service); the product
canisters by [Casals](https://github.com/smart-social-contracts/Casals).

## License

[MIT](LICENSE)
