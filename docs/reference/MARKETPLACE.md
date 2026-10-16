# Marketplace v2 (Basilisk, file_registry-backed)

The realms marketplace is a pair of standalone canisters that lets users
discover, publish, like, and purchase **extensions** and **codices**
without rebuilding any realm. It sits on top of the existing
`file_registry` canister: the marketplace itself stores only metadata
and a pointer; the actual artifact files (extension bundles, codex `*.py`
modules) live in the registry.

## Canisters

| Canister | Path | Purpose |
|---|---|---|
| `marketplace_backend` | `src/marketplace_backend/` (Basilisk Python) | Listings, likes, rankings, purchases, audit/verification status, developer licenses, off-chain billing-service integration. |
| `marketplace_frontend` | `src/marketplace_frontend/` (SvelteKit static) | Top Charts homepage, browse/detail/upload pages, My Purchases, Developer dashboard, Admin / curator queue. |
| `file_registry` | `src/file_registry/` | Holds the actual extension/codex files. Marketplace listings carry `(file_registry_canister_id, file_registry_namespace)` pointers into it. |

The marketplace runs on the same dfx replica as a realms mundus. Deploy it via:

```bash
realms marketplace deploy --network local
# or
realms marketplace deploy --network ic --identity my-pem \
    --file-registry-canister-id <FR_ID> \
    --billing-service-principal <BILLING_PRINCIPAL>
```

## What you get

### Three kinds of listings

- **Extensions** — keyed by `extension_id`, namespace `ext/<id>/<version>`.
- **Codices** — keyed by `codex_id` (may contain a `/` for `realm_type/codex_id`),
  namespace `codex/<id>/<version>`.
- **AI Assistants** — keyed by `assistant_id` (may contain a `/` for
  `author/assistant_id`), namespace `assistant/<id>/<version>`. See
  the [Assistants section](#ai-assistants) below for the full field set.

Both kinds share:

- `name`, `description`, `version`, `icon`, `categories`, `price_e8s` (display only).
- `installs` and `likes` counters (denormalised; see ranking endpoints).
- `verification_status` (`unverified` / `pending_audit` / `verified` / `rejected`)
  and `verification_notes`.

### Likes

Authenticated users can `like_item("ext"|"codex", item_id)` and
`unlike_item(...)` (idempotent). The listing's `likes` counter mirrors the
row count in `LikeEntity`. `my_likes()` returns the caller's likes.

### Rankings (Top Charts)

Six query endpoints, each returns top-N by the relevant metric and
respects an optional `verified_only` flag:

- `top_extensions_by_downloads(n: nat64, verified_only: bool)`
- `top_extensions_by_likes(n: nat64, verified_only: bool)`
- `top_codices_by_downloads(n: nat64, verified_only: bool)`
- `top_codices_by_likes(n: nat64, verified_only: bool)`
- `top_assistants_by_downloads(n: nat64, verified_only: bool)`
- `top_assistants_by_likes(n: nat64, verified_only: bool)`

The frontend's homepage exposes them via two toggles
(extensions ⇄ codices ⇄ assistants, downloads ⇄ likes) plus the
verified filter.

### Uploading a submission (`/upload`)

A developer with an active license who is not suspended submits an
extension, codex or assistant from the browser:

1. `check_upload(kind, id, version)` (query) validates the id and semver
   version, the license and suspension, and that nobody else owns the
   listing. The form shows the reason before any file is picked.
2. `prepare_upload(kind, id, version)` repeats those checks and asks the
   file registry to make the caller a publisher of exactly
   `<kind>/<id>/<version>`. The marketplace may do this because it is on
   the registry's `_granters` ACL; the registry refuses a package root
   (`<kind>/<id>`) someone else already publishes under, so a newcomer
   cannot add a "latest" version to another developer's package.
3. The browser uploads the folder straight to the registry with
   `store_file_chunk` and `finalize_chunked_file_step` (or
   `finalize_chunked_file` on registries without the step method), then
   `publish_namespace` where the registry has it.
4. The browser calls `create_extension` / `create_codex` /
   `create_assistant` with the namespace and metadata. The listing starts
   in `pending_review`; the Developer page shows it as *In review* until a
   reviewer approves or rejects it from the Admin section.

The registry has to list the marketplace as a granter once, by a registry
controller:

```bash
dfx canister call file_registry grant_publish \
  '("{\"namespace\": \"_granters\", \"principal\": \"<marketplace_backend id>\"}")'
```

### Paid developer license + audit/verification

Publishing and audits require a paid monthly developer license. The
flow:

1. Developer page POSTs to
   `${BILLING_SERVICE_URL}/marketplace/license/checkout` with their
   principal and a `return_url`. The off-chain billing service
   returns a Stripe checkout URL.
2. After Stripe completes, the billing service webhook calls
   `marketplace_backend.record_license_payment(LicensePaymentInput)`
   from a principal stored in `config.billing_service_principal`. The
   marketplace creates or extends the developer's license.
3. Developer can then `request_audit("ext"|"codex", item_id)` on any
   listing they own. The listing's status flips to `pending_audit`.
4. A reviewer or admin approves or rejects it from the Admin section
   (`review_listing`), which stamps the decision onto the file registry
   and flips the listing's status with the curator notes.
5. `verified_only=true` filters limit Top Charts and browse pages to
   approved items.

For local development the off-chain billing service is usually not
running. Admins can call `grant_manual_license(principal,
duration_seconds, note)` directly via `dfx canister call` or from the
Admin section's Users tab.

## Roles

| Role | Who decides | Can do |
|---|---|---|
| Controller | the canister's controller list | everything, including canister settings and the admin list |
| Admin | controllers (`add_admin` / `remove_admin`) | every listing, every user and every license; appoint reviewers; review |
| Reviewer | admins (`add_reviewer` / `remove_reviewer`) | approve and reject submissions |

Controllers are admins, and admins are reviewers, without being listed.
Admins use the **Admin** section of the marketplace's left panel
(`/admin`). It has three tabs:

- **Listings**: every extension, codex and assistant from every developer,
  delisted ones included. Approve or reject (stamped onto the file
  registry), send back to the review queue, delist or relist, transfer
  to another owner.
- **Users**: every principal the marketplace knows (listing owners,
  license holders, reviewers, admins). Grant, extend or revoke a license;
  suspend or reinstate; make or remove a reviewer; set the display name.
  A suspended principal cannot publish, update or request audits; its
  listings stay up until delisted.
- **Roles & settings**: the admin list (read-only), the reviewer list,
  and the canister settings (read-only).

Only a controller changes the admin list:

```bash
dfx canister call marketplace_backend add_admin '("<principal>")'
dfx canister call marketplace_backend remove_admin '("<principal>")'
dfx canister call marketplace_backend list_admins
```

## Authorisation matrix

| Endpoint | Caller |
|---|---|
| All `*_q` queries, browse / search / status / get_*_details / has_purchased / has_liked / my_likes / my_purchases / get_my_* / get_my_roles / list_admins / list_reviewers | open |
| `create_*`, `update_*` | active license, not suspended (controllers exempt); updates also need ownership or controller |
| `check_upload`, `prepare_upload` | active license, not suspended, owner of the listing or a new id (controllers exempt) |
| `buy_*`, `like_*`, `unlike_*` | any II principal |
| `delist_*`, `transfer_listing` | listing owner OR admin |
| `request_audit` | active license + listing owner, not suspended |
| `record_license_payment` | controller OR `config.billing_service_principal` |
| `review_listing`, `set_verification_status`, `list_pending_audits` | reviewer (admins and controllers included) |
| `admin_list_listings`, `admin_set_listing_active`, `admin_list_users`, `admin_set_user_suspended`, `admin_set_developer_name`, `admin_grant_publisher`, `grant_manual_license`, `revoke_license`, `add_reviewer`, `remove_reviewer` | admin (controllers included) |
| `add_admin`, `remove_admin`, `admin_approve_namespace`, `set_*_canister_id`, `set_billing_service_principal`, `set_license_pricing`, `recount_listing_likes` | controller-only |

`request_audit` requires both ownership and a license — the license is
the gate to even enter the audit queue.

## Data model

Stable storage uses ic_python_db Entities:

- `ExtensionListingEntity` — keyed by `extension_id`.
- `CodexListingEntity` — keyed by a slash-safe alias (`syntropia/membership` ↔ `syntropia__membership`). Original id is preserved as a separate field.
- `AssistantListingEntity` — keyed by a slash-safe alias (`smart-social-contracts/ashoka` ↔ `smart-social-contracts__ashoka`). Carries AI-specific fields (runtime, endpoint_url, base_model, requested_role, requested_permissions, domains, languages, training_data_summary, eval_report_url, pricing_summary) on top of the standard listing fields.
- `PurchaseEntity` — one row per `buy_*` call. `item_kind = "ext" | "codex" | "assistant"`.
- `LikeEntity` — one row per `(principal, item_kind, item_id)` tuple, composite key `"{principal}|{kind}|{id}"`.
- `DeveloperLicenseEntity` — keyed by principal.
- `MarketplaceConfigEntity` — singleton (`alias = "config"`) holding the file_registry canister id, billing service principal, and license pricing.

## AI Assistants

A third item kind that lets developers publish realm-hireable AI
governance agents — Ashoka, Geister, and the like. The marketplace
catalogs **what the assistant is** (where the model lives, what role
and permissions it requests, what specialties it claims, what
training data it discloses); the runtime that actually loads a hired
assistant lives on the realm side and is the subject of a separate
follow-up PR (`assistant_runner` extension + `hire_assistant` codex).

### Lifecycle

1. **Author publishes** the assistant via `/upload` (or
   `dfx canister call marketplace_backend create_assistant`). Files
   (system prompt, MCP tool manifest, eval transcripts) get uploaded
   to the file_registry namespace `assistant/<id>/<version>`. The
   listing carries a pointer to that namespace plus the AI-specific
   metadata.
2. **A realm hires the assistant** via a normal governance proposal
   (drafted from the marketplace UI's Hire button → goes through the
   realm's `hire_assistant_codex`). On approval the realm's
   `assistant_runner` extension allocates a service principal,
   creates a `User` entity for the assistant with the requested role
   and permissions, and starts subscribing to the realm's event feed.
3. **Consumer extensions** (`llm_chat`, `codex_viewer`, future
   surfaces) query `realm_backend.list_active_assistants()` and route
   their calls to the right one — directly via the assistant's
   declared `endpoint_url`/`runtime`.

### Listing fields

Standard marketplace fields (id, name, description, version, price,
icon, categories, file_registry pointer, likes, installs,
verification status) plus AI-specific ones:

| Field | Purpose |
|---|---|
| `runtime` | `runpod` / `openai` / `anthropic` / `on_chain_llm` / `self_hosted` — host needs to know how to call the model. |
| `endpoint_url` | HTTPS URL or canister id for inference. |
| `base_model` | e.g. `"gpt-4o"`, `"llama-3.1-70b"`, `"geister-mistral-7b-finetune"`. Disclosure for users + audit. |
| `requested_role` | The User role the assistant asks the realm to assign it (`auditor`, `analyst`, `proposer`, …). |
| `requested_permissions` | Comma-separated realm operations (e.g. `read_proposals,read_treasury,submit_proposal`). The hire-vote decides whether to grant. |
| `domains` | Comma-separated specialty tags (`governance,tax,justice`). Browse page filter. |
| `languages` | Comma-separated language tags (`en,es,zh`). |
| `training_data_summary` | Free-text disclosure for the audit process. |
| `eval_report_url` | External link or path inside the file_registry namespace pointing at evaluation transcripts. |
| `pricing_summary` | Free-text shape (e.g. `"$200/year per realm"`). The numeric `price_e8s` remains display-only. |

### Endpoints

Same shape as extensions and codices, suffix `_assistant`:

```
create_assistant(AssistantInput) -> GenericResult
update_assistant(AssistantInput) -> GenericResult
delist_assistant(text) -> GenericResult
get_assistant_details(text) -> AssistantResult
list_marketplace_assistants(page, per_page, verified_only) -> AssistantListResult
search_assistants(text, verified_only) -> Vec[AssistantListing]
get_my_assistants() -> Vec[AssistantListing]
buy_assistant(text) -> GenericResult
has_purchased_assistant(text, text) -> bool
top_assistants_by_downloads(nat64, bool) -> Vec[AssistantListing]
top_assistants_by_likes(nat64, bool) -> Vec[AssistantListing]
```

`like_item("assistant", id)` and `request_audit("assistant", id)`
work via the existing generic endpoints — `VALID_KINDS` was extended
to include `"assistant"`.

### Realm-side hiring (out of this canister)

The hiring flow itself is a governance act, not a marketplace act.
The marketplace's `buy_assistant` is **bookkeeping only** — it
records the install and credits the developer. The actual flow is:

1. UI (e.g. the realm's `package_manager` extension) drafts a
   `HireAssistantProposal` carrying the marketplace listing pointer
   and the realm-scope permission manifest.
2. Realm's normal voting rules apply.
3. On approval, realm_backend creates the assistant's `User`, assigns
   the requested role/permissions, registers its endpoint config,
   starts its event-feed subscription.
4. From that moment, `llm_chat` / `codex_viewer` / any other consumer
   surface routes its calls to the active assistant.

This realm-side wiring lives in a separate PR — see the
follow-up plan in the repo's plan artifacts.

## CLI

```bash
realms marketplace deploy [--network local|staging|ic]
                          [--mode auto|install|upgrade|reinstall]
                          [--identity NAME-OR-PEM]
                          [--with-registry / --no-with-registry]
                          [--file-registry-canister-id ID]
                          [--billing-service-principal PRINCIPAL]

realms marketplace status [--network ...]
realms marketplace call <method> [args] [--network ...] [--canister-id ID]
                        [--output json|candid] [--verbose]
```

## Off-chain billing service contract

The Stripe leg of the license-payment flow is handled by an off-chain
service (the same one that powers `realm_registry_backend.add_credits`,
running at `https://billing.realmsgos.dev`). Two endpoints power the
marketplace integration:

- `POST /marketplace/license/checkout`

  Request body:

  ```json
  {
    "principal_id": "<II principal text>",
    "return_url": "https://<marketplace_frontend>/developer?license_status=success",
    "marketplace_canister_id": "<canister id>"
  }
  ```

  Response:

  ```json
  { "checkout_url": "https://checkout.stripe.com/..." }
  ```

- `POST /marketplace/license/webhook` (Stripe → service)

  On Stripe success the service calls
  `marketplace_backend.record_license_payment(record { principal = ...; stripe_session_id = ...; amount_usd_cents = ...; duration_seconds = ...; payment_method = "stripe"; note = "" })`
  from the principal stored in `config.billing_service_principal`.
  The `amount_usd_cents` field is stored on the developer's license
  row as `last_payment_amount_usd_cents` for audit-trail purposes.

The service's principal must be configured on the marketplace via
`set_billing_service_principal(<principal>)` from a controller. The
local equivalent is `grant_manual_license`.

## Migration from v1 (Kybra)

The previous marketplace shipped as `kybra-simple-marketplace` (a
separate GitHub repo) and was pulled into the realms `dfx.json` as a
remote-WASM canister. v2 replaces that entirely:

- Builds locally via `python -m basilisk marketplace_backend …`,
  matching every other backend canister in this repo.
- Lives in `src/marketplace_*` (no longer in the
  `realms-extensions` submodule).
- Switched from a static build-time `extensions.json` snapshot to live,
  on-chain listings.

Existing demo/staging canister IDs (`ehyfg-wyaaa-aaaae-qg3qq-cai`) are
no longer pinned in `dfx.json` — first deploy on each network allocates
fresh ids.

## Testing

Unit tests in `tests/backend/marketplace/` cover all business logic
(34 tests including ranking ordering, like idempotency, license
expiry, and verification authorisation). Run with:

```bash
pytest tests/backend/marketplace -q
```

Local end-to-end smoke (requires `dfx` + `python -m basilisk install-dfx-extension`):

```bash
dfx start --background --clean
realms marketplace deploy --network local
MP=$(dfx canister id marketplace_backend)
dfx canister call $MP status
dfx canister call $MP create_extension '(record {
  extension_id = "demo"; name = "Demo"; description = "test";
  version = "0.1.0"; price_e8s = 0 : nat64; icon = "🧩";
  categories = "other";
  file_registry_canister_id = "'$(dfx canister id file_registry)'";
  file_registry_namespace = "ext/demo/0.1.0";
  download_url = "";
})'
dfx canister call $MP buy_extension '("demo")'
dfx canister call $MP like_item '("ext", "demo")'
dfx canister call $MP top_extensions_by_likes '(20 : nat64, false)'
```

Then visit the marketplace_frontend URL printed by `dfx deploy` to
exercise the UI.
