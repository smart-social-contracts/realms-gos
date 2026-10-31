# Deployment

Three things get deployed, by three different tools:

| What | Where it runs | Tool |
|---|---|---|
| A realm for development | Your machine (PocketIC) | `realms local`: see [Getting started](../getting-started.md) |
| The **product orchestra**: marketplace, fleet file registry, token, NFT | The Internet Computer | [Casals](https://github.com/smart-social-contracts/Casals), from [`casals.json`](../../casals.json), driven by [`scripts/up.sh`](../../scripts/up.sh) |
| Realms that people create | The Internet Computer | The GaaS portal ([gos-as-a-service](https://github.com/smart-social-contracts/gos-as-a-service)); each realm is a stand of the GaaS orchestra |

All three install **release artifacts**, not the checkout.

## Releases

The *Create Release* workflow ([`.github/workflows/release.yml`](../../.github/workflows/release.yml),
run by hand: patch, minor or major) builds and attaches to a GitHub release:

| Asset | Built by |
|---|---|
| `realm-backend-<tag>.wasm.gz` | `scripts/build_base_wasm.py`, the previous backend's recipe (see the note below) |
| `realm-frontend-<tag>.tar.gz` | `npm run build` in `src/realm_frontend`, packed with `casals bundle` |
| `marketplace-backend-…`, `marketplace-frontend-…` | Basilisk, `npm run build` |
| `token-backend-…`, `nft-backend-…` | Copied from [ic-tokens](https://github.com/smart-social-contracts/ic-tokens) |
| `checksums-<tag>.txt` | SHA-256 of the above; `realms local` checks downloads against it |

To build the realm by hand:

```bash
pip install -r requirements.txt                     # pinned: a stale ic-python-db builds a wasm that traps at init
python scripts/pack_realm_backend.py --version 0.9.0
cd src/realm_frontend && npm run build              # REALMS_BUILD_VARIANT=test for a build with test flags
```

`pack_realm_backend.py` builds the backend on the Cedar template and fails unless the wasm
exports `read` and `run` ([Operations](../architecture/operations.md)).

> **Note.** The release workflow still builds with `build_base_wasm.py`, written for the previous
> backend (it stubs an `extension_packages/` folder this backend no longer has).
> `pack_realm_backend.py` is the recipe for this backend; switch the workflow to it before the
> first release from this branch.

## The product orchestra

[`casals.json`](../../casals.json) is the *sheet*: what should exist, per environment (`local`,
`production`). Its stands:

| Stand | Canisters |
|---|---|
| `marketplace` | `marketplace-backend`, `marketplace-frontend` (served at the marketplace host) |
| `file-registry` | `fleet-file-registry`, `fleet-file-registry-frontend` |
| `token` | `token-backend` |
| `nft` | `nft-backend` |

Each artifact is a row of the sheet's `registry`, sourced from a release (for example
`release:smart-social-contracts/realms-gos@v0.7.0:marketplace-backend-v0.7.0.wasm.gz`) and
optionally pinned by `sha256`. Ship a new version by changing the row. The Casals runbook
(`Casals/docs/OPERATIONS.md`) is the reference for every `casals` command.

### One command

[`scripts/up.sh`](../../scripts/up.sh) takes a clean checkout to a converged, populated orchestra.
Every phase is idempotent, so it is safe to re-run.

```bash
scripts/up.sh -e local --yes                                   # a local replica (CI does this)
scripts/up.sh -e production --identity prod-session --yes      # production
scripts/up.sh -e production --identity prod-session --publish-only   # only the catalog changed
```

| Phase | Does | Skip or narrow |
|---|---|---|
| preflight | Tools, the CLI from this checkout (`pip install -e cli`), the identity and secrets | |
| build | Pinned Python deps, the file registry, the backends and frontends | `--skip-build`, `--build-only` |
| up | `casals up` (locally, through the Casals e2e harness: fresh and idempotent scenarios) | `--publish-only` |
| export | `casals export`: the live canister ids. Ids are never kept in the repo. | |
| domains | `realms domains apply`, when the environment has a DNS provider | `--no-domains` |
| publish | `realms files publish` (packages to the fleet registry), `realms marketplace publish` (listings) | `--skip-publish`, `--extensions a,b`, `--codices x` |
| verify | The registry lists namespaces; one marketplace listing is `verified` | |

It needs sibling checkouts of Casals and file-registry (`CASALS_DIR`, `FILE_REGISTRY_DIR`, default
`../Casals`, `../file-registry`). For production also:

| Variable | Why |
|---|---|
| `CASALS_HOME` | Where the first `casals up` wrote `realms-product.production.json` (default `~/.casals`). Without it, `up` sees no conductor. |
| `DFX_HSM_PIN` | The hardware key behind the identity |
| `INFOMANIAK_API_TOKEN` | DNS token for the domains phase on the Infomaniak zones (see [Domains](#domains)). Never commit it. |
| `CLOUDFLARE_API_TOKEN` | Only for an environment whose `dns.provider` is `cloudflare`: Zone:Read and DNS:Edit. Never commit it. |

With a touch-policy hardware key, pass a short-lived session identity made with
`icp identity delegation` (one touch) as `--identity`, not the key itself (runbook: "Hardware
keys").

### Content

`casals up` creates canisters; it does not fill them. The sheet grants the **operator**
(`environments.<env>.principals.operator`) what publishing needs:

| Canister | Grant | So the operator can |
|---|---|---|
| `fleet-file-registry` | `grant_publish {namespace: "*"}` | Upload every package |
| `fleet-file-registry` | `grant_publish {namespace: "_approvers"}` to the marketplace | Let `review_listing` stamp approvals on the registry |
| `marketplace-backend` | `admin_grant_publisher {reviewer: true}` | Hold a developer licence and a reviewer seat |

```bash
realms files publish -n ic --registry <fleet-file-registry> --identity <operator>
realms marketplace publish -n ic --marketplace <marketplace-backend> --registry <fleet-file-registry> --identity <operator>
```

`files publish` uploads the first-party extensions and codices from the `extensions/` and
`codices/` submodules; unchanged files are skipped. `marketplace publish` creates or updates a
listing for each and approves it as reviewer. A listing is `verified` only once the registry
accepted the approval stamp. Realms check that stamp before installing
([Trust](../architecture/trust-and-registry.md)).

Realms created through GaaS install from the GaaS orchestra's own file registry, so first-party
packages are published there too.

### Updating

Once built, the orchestra changes by operations, not by re-applying the sheet:

```bash
casals -e production --identity <id> upgrade casals.json --wasm <family>          # a backend
casals -e production --identity <id> upgrade casals.json --content <namespace>    # a frontend bundle
```

### Domains

```bash
realms domains check casals.json -e production      # read-only; exit 1 on drift
realms domains apply casals.json -e production      # DNS records → IC gateway registration → HTTP 200
realms domains apply casals.json -e production --dry-run   # Infomaniak: print the DNS changes, write nothing
```

`apply` is idempotent. For a re-created frontend, run it **before** removing the old canister: the
gateway checks the new canister's `/.well-known/ic-domains`, and the old one going away is what
takes the site down.

#### DNS at Infomaniak

`gos.earth`, `realmsgos.org` and `ic-casals.tech` are hosted at Infomaniak; an environment on them has
`"dns": {"provider": "infomaniak", "zone": "<zone>", "token_env": "INFOMANIAK_API_TOKEN", "ttl": 60}`
(`zone` is required; the TTL cannot be below 60). `realmsgos.dev` stays on Cloudflare for its tunnels.

For each host, `apply` makes sure the zone holds:

| Record | Value |
|---|---|
| `CNAME <host>` | `<host>.icp1.io` |
| `TXT _canister-id.<host>` | the frontend canister id |
| `CNAME _acme-challenge.<host>` | `_acme-challenge.<host>.icp2.io` |

An apex host (the host equals the zone) cannot be a CNAME at Infomaniak, so it gets `A 195.5.171.242` and
`A 212.71.124.188` instead. A record that holds a stale value (a re-minted canister id, say) is updated in
place. Nothing is ever deleted: Infomaniak records carry no owner, so any other record on the same name is
left alone and reported, and a stray second `_canister-id` TXT has to be removed by hand in the manager
(the IC rejects a domain with two).

**The token.** In the Infomaniak manager open *API tokens*
(<https://manager.infomaniak.com/v3/ng/profile/user/token/list>) → *Create a token*, and give it the
scopes `dns:read` and `dns:write` (the "DNS" permission of the account's domains) and nothing else. Name it
after its use (e.g. `realms-domains`). Export it in the shell that runs `scripts/up.sh` or
`realms domains apply`:

```bash
export INFOMANIAK_API_TOKEN=...   # never commit it, never put it in a sheet
```

The sheet's `token_env` names the variable, not the token. Run `--dry-run` first: it reads the zone and
prints `created` / `updated` / `unchanged` per record without writing or registering anything.
