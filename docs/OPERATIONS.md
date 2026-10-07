# Operating the Realms product orchestra

The marketplace, fleet file registry, token, NFT, and the demo realm stand are
declared in [`casals.json`](../casals.json). The Casals runbook
(`Casals/docs/OPERATIONS.md`) is the command reference; this page is what is
specific to this repo.

## One command

[`scripts/up.sh`](../scripts/up.sh) takes a clean checkout to a converged,
populated, DNS-mapped orchestra and is safe to re-run:

```sh
# production, from realms/ with Casals and file-registry as sibling checkouts
export DFX_HSM_PIN=…                 # the hardware key (needed for the one delegation signature)
export CLOUDFLARE_API_TOKEN=…        # Zone:Read + DNS:Edit on realmsgos.org; never commit it
export CASALS_HOME=…                 # where the first `up` wrote realms-product.production.json (default ~/.casals)
# prod-session: a short-lived `icp identity delegation` from prod-identity — one touch for the run
# (recipe: Casals/docs/OPERATIONS.md, "Hardware keys")
scripts/up.sh -e production --identity prod-session --yes

scripts/up.sh -e local --yes         # a laptop: same phases on a local replica (scripts/local_up.sh wraps this)
scripts/up.sh -e production --identity prod-identity --publish-only   # only the catalog changed
```

Phases, each printed with a header and each idempotent:

| phase | what it runs | skip with |
|---|---|---|
| preflight | tools, python deps, identity, `DFX_HSM_PIN` / `CLOUDFLARE_API_TOKEN` / bindings present (production) | — |
| build | every `local:` source of the sheet, as `environments.<env>.build_variant` (below) | `--skip-build`; `--build-only` stops here |
| pin | `casals pin casals.json`; production stops to have you commit changed pins unless `--yes` | — |
| up | `casals up` (locally through the Casals e2e harness: replica, funding, fresh + idempotent grading); builds what the sheet declares, a no-op once built | — |
| export | `casals export` → the live ids (never a table in the repo) | — |
| domains | `realms domains apply` when `dns.provider` is not `none` | `--no-domains` |
| publish | `realms files publish` → fleet registry, `realms marketplace publish` → listings | `--skip-publish`; `--publish-only` runs just this |
| verify | `casals plan` empty, registry lists namespaces, one listing `verified`, URLs printed | — |

`--extensions a,b` / `--codices x` / `--extensions-only` / `--codices-only`
narrow what is published (CI publishes `hello_world` only). A second run with
no changes rebuilds (or `--skip-build`), reports the pins clean, an empty plan,
0 changed files and listings, and exits 0.

The rest of this page is what those phases do, one at a time, for when you
need to run or debug a single step.

## Build, then `up`

The sheet references product wasms and frontend dists as `local:` paths.
Build them first (`scripts/up.sh -e <env> --build-only`; the recipes are its
build phase, which CI runs too). That includes the sibling `file-registry`
checkout (`make build`: the fleet registry wasm and the registry UI dist the
sheet publishes to `fleet-file-registry-frontend`). basilisk bundles whatever
Python packages are installed, so install the pinned
[`requirements.txt`](../requirements.txt) before building: a stale
`ic-python-db` produces a wasm that traps at init (the conductor then
re-downloads and re-installs it every tick without ever converging).

Build the realm halves as the variant the environment declares
(`environments.<env>.build_variant`): `local` is `test`
(`scripts/pack_realm_backend.py --variant test`, `REALMS_BUILD_VARIANT=test npm
run build`), `production` is `production` (the defaults). The demo stand's
`converged_when` asserts `build_variant` from `get_runtime_flags`, so the wrong
variant never converges instead of quietly refusing (or quietly accepting) test
flags. Then, from the Casals repo:

```sh
python -m casals_cli.main -e local --identity local-dev up ../realms/casals.json --yes
```

The same command with `-e production` and the environment's deployer identity
is the production procedure. Environment differences (principals, DNS, flags)
live in the sheet's `environments` block. Two things production needs on top:

- **Pins.** `up -e production` requires a `sha256` on every `registry.wasms`
  row *and* every `registry.bundles` row (the frontend bundles: their hash is
  the *bundle hash* of `Casals/docs/BUNDLES.md`). After building, `casals pin
  casals.json` writes both; `casals pin --check` in CI catches drift.
- **Bindings and the key.** Every command after the first `up` reads the
  conductor id from `$CASALS_HOME/<orchestra>.production.json`; point
  `CASALS_HOME` at the directory that holds it, or `up` will think there is no
  conductor and stop (it refuses to bootstrap a second production conductor
  without `--bootstrap`). With a touch-policy hardware key, do not run the
  hundreds of calls of an `up` against the key itself: make a short-lived
  session identity with `icp identity delegation` (one touch), and pass that
  as `--identity` — the IC still sees the hardware key's principal. Recipe in
  `Casals/docs/OPERATIONS.md`, "Hardware keys". The CLI prints `signing
  <method> as <identity>` before each call it signs with a PIN-backed
  identity, so with the key itself as `--identity` you know when to touch.

## Updating a frontend (marketplace, demo realm)

A frontend canister serves exactly the bundle its `content` names, so a new
build is a new bundle, not a new canister:

```sh
# realms/: build, then pack the dist into a canonical hashed bundle
casals bundle src/marketplace_frontend/dist -o marketplace-<version>.tgz   # prints the bundle sha256
```

Then point the sheet's `registry.bundles` row at it (`local:marketplace-<version>.tgz`,
or a release asset URL), `casals pin casals.json`, commit, and ship it:

```sh
casals -e <env> --identity <id> upgrade casals.json --content frontend/marketplace-assets/main
```

`casals upgrade --content` uploads the bundle to the store when it is missing
and makes every frontend whose `content` is that namespace serve exactly it
(one `sync_content` slice per call, repeated until no file remains). A
commander with `wasm.upload` may do the upload from the browser instead
(Casals frontend → **Files → Upload bundle**: hashing, diff against the store
and the one-batch upload happen there); the pin and the `upgrade --content`
still come from the CLI. The same page shows, per bundle, whether the store
matches the pin and which canisters serve it.

`casals up` does not roll a new bundle out: once built, the orchestra is
changed by operations, not by re-applying the sheet (Casals #52). So a
marketplace fix never drags `Realms/demo` (the demo realm) along — it moves
only when you run `casals upgrade --content <its namespace>` or
`--wasm realm-backend --stand demo`.

Realm *instances* that users deploy through the GaaS portal are stands of the
**GaaS** orchestra, not this one. See `gos-as-a-service/docs/OPERATIONS.md`.

## Custom domain, after `up`

`casals up` does not touch DNS (`domains` reports `unverifiable`). The sheet's
`domains` block (`realmsgos.org` → `marketplace-frontend`) is applied by the
product CLI, which reads the canister id from the conductor. It reads as the
selected icp identity (the operator; a hardware key needs `DFX_HSM_PIN`): a
conductor answers only its commanders and controllers unless the sheet sets
`public_read`.

```sh
# from realms/, with CASALS_HOME pointing at the bindings `casals up` wrote
realms domains check casals.json -e production            # read-only, exit 1 on drift
export CLOUDFLARE_API_TOKEN=…                             # Zone:Read + DNS:Edit on the zone; never commit it
realms domains apply casals.json -e production            # Cloudflare records → IC gateway registration → HTTP 200
```

`apply` is idempotent and is what a re-minted frontend needs: it rewrites the
`_canister-id` TXT record and re-points (`PATCH`) the existing gateway
registration, then waits until `https://<host>/` answers. Run it **before**
destroying the previous frontend — the gateway validates the new canister's
`/.well-known/ic-domains`, and the old canister's disappearance is what takes
the site down. The same command serves the GaaS sheet
(`realms domains apply ../gos-as-a-service/casals.json -e production`).

## Content, after `up`

`casals up` provisions canisters; it does not put packages in them. The
controllers of every product canister are the conductor and the multisig,
so the sheet grants the **operator** (`environments.<env>.principals.operator`)
what the product CLI needs, through config rows the conductor applies:

| canister | row | gives the operator |
|---|---|---|
| `fleet-file-registry` | `grant_publish {namespace: "*"}` | upload to every namespace (`realms files publish`) |
| `fleet-file-registry` | `grant_publish {namespace: "_approvers"}` → marketplace | lets `review_listing` stamp approvals on the registry |
| `marketplace-backend` | `admin_grant_publisher {reviewer: true}` | developer license (to submit) + reviewer seat (to approve) |

Then, with the operator identity (a hardware key: `export DFX_HSM_PIN=…`
first; the CLI passes it to every `icp` call):

```sh
# ids from the live conductor, never from a table in the repo
casals -e production export casals.json          # bindings: fleet-file-registry, marketplace-backend

# packages the marketplace lists (its listings name this registry)
realms files publish -n ic --registry <fleet-file-registry> --identity prod-identity
realms marketplace publish -n ic --marketplace <marketplace-backend> --registry <fleet-file-registry> --identity prod-identity
```

`marketplace publish` creates or updates a listing for every first-party
extension and codex (the same set `files publish` uploads) and approves it as
reviewer; a listing ends `verified` only when the registry accepted the
approval stamp. Re-running either command is idempotent (unchanged files are
skipped, listings are upserted).

Packages that realms **install** through the GaaS portal are fetched from the
GaaS orchestra's own `file-registry` (its installer's `file_registry_id`), so
they are published there too — see `gos-as-a-service/docs/OPERATIONS.md`.

This is the publish phase of `scripts/up.sh` (`--publish-only` runs just it).
CI (`realms-e2e.yml`) runs the script on a local replica and fails when a
listing does not come back `verified`; `scripts/local_up.sh` does the same on
a laptop, so a fresh local orchestra comes up with a populated registry and
marketplace.

## Checks

```sh
scripts/up.sh -e local --skip-build --yes        # up + publish + verify again: must report no changes
# from Casals/
KEEP=1 SCENARIOS=fresh,idempotent python tests/e2e/run_e2e.py ../realms/casals.json
casals -e local oracle ../realms/casals.json
```

The `realms` CLI (db, files publish, registry, billing, quarter commands)
is a product CLI. It does not provision canisters — that is `casals up`.
