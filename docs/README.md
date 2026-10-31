# Realms GOS documentation

Realms GOS is a governance operating system on the [Internet Computer](https://internetcomputer.org).
A **realm** is one canister (or a federation of canisters) that holds a community's members,
departments, votes, treasury, land, licences and courts. Its rules are written as code
(**codices**), and its features come as **extensions**.

Everything about the platform is documented here. If a page and the code disagree, the code is
right and the page is a bug.

## Start here

| If you want to… | Read |
|---|---|
| Run a realm on your machine in 5 minutes | [Getting started](getting-started.md) |
| Understand how a realm works | [Architecture](architecture/README.md) |
| Know *why* it is built this way | [Design choices](architecture/design-choices.md) |
| Look up an entity, an operation or a rule | [GGG spec](ggg/spec.md) |
| Write an extension | [Extensions](extensions/README.md) |
| Write a codex (laws as code) | [Codices](codices/README.md) |
| Deploy or operate the product canisters | [Deployment](deployment/README.md) |
| Change this repository | [Contributing](development/contributing.md) and [Testing](development/testing.md) |
| Decode a term | [Glossary](glossary.md) |

## Map

```text
docs/
├── getting-started.md            realms local: start, publish, set up, call
├── glossary.md
├── architecture/
│   ├── README.md                 canisters, layers, the life of a call
│   ├── design-choices.md         the criteria behind the design
│   ├── operations.md             @op, read/run, flags, errors, pagination, audit
│   ├── authorization.md          Cedar: schema, policies, guardrails, permissions
│   ├── sandboxing.md             how codices and extensions are contained
│   ├── governance-and-delegation.md
│   ├── quarters-and-federation.md
│   ├── trust-and-registry.md     file registry, marketplace, trust policy
│   └── frontend.md               the realm UI and the extension bridge
├── ggg/
│   ├── README.md                 what GGG is and how to read it
│   └── spec.md                   the standard: entities, operations, invariants
├── extensions/
│   ├── README.md                 what an extension is
│   ├── manifest.md
│   ├── backend.md
│   ├── frontend.md
│   └── build-test-publish.md
├── codices/
│   ├── README.md                 what a codex is
│   ├── sdk.md                    ggg_sdk reference
│   └── build-test-publish.md
├── deployment/README.md          Casals, environments, domains, publishing
├── development/
│   ├── contributing.md           style rules and conventions
│   └── testing.md
└── project/
    ├── roadmap.md
    ├── refactor-plan.md          the plan behind the current backend
    ├── quarters-design.md        the original design of quarters
    └── specs/                    feature specs not yet built
```

## Related repositories

| Repository | What it holds |
|---|---|
| [realms-extensions](https://github.com/smart-social-contracts/realms-extensions) | First-party extensions (the `extensions/` submodule) |
| [realms-codices](https://github.com/smart-social-contracts/realms-codices) | First-party codices (the `codices/` submodule) |
| [Casals](https://github.com/smart-social-contracts/Casals) | Declarative deployment of canister fleets |
| [basilisk](https://github.com/smart-social-contracts/basilisk) | The Python CDK the backend runs on (`ic-basilisk` on PyPI) |
| [ic-basilisk-toolkit](https://github.com/smart-social-contracts/ic-basilisk-toolkit) | Cedar engine, tasks and other canister helpers |
| [file-registry](https://github.com/smart-social-contracts/file-registry) | The canister that stores published extension and codex files |
| [ic-python-db](https://github.com/smart-social-contracts/ic-python-db) | The entity database over stable memory |
